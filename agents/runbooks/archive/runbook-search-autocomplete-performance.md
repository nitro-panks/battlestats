# Runbook: Search Autocomplete Performance

**Created**: 2026-03-29
**Updated**: 2026-03-29
**Status**: Complete — all fixes deployed and verified

## Context

The header search bar autocomplete was painfully slow (2–22 seconds per keystroke) and had a dark mode styling bug (white input background with light text).

## Root Causes

### 1. Dark mode input not themed
The `<input>` in `HeaderSearch.tsx` had no `bg-*` or `text-*` classes, inheriting browser-default white background. The dropdown list correctly used `bg-[var(--bg-page)]` but the input itself did not.

### 2. No client-side caching
Every keystroke (after 180ms debounce) fired a fresh `fetch()` to `/api/landing/player-suggestions?q=...`. Backspacing and re-typing the same prefix re-fetched from the server each time.

### 3. Django `icontains` bypasses trigram index
The `Player.objects.filter(name__icontains=query)` ORM call generates `WHERE UPPER(name) LIKE UPPER('%query%')`. The `UPPER()` wrapping prevents PostgreSQL from using the existing `pg_trgm` GIN index (`player_name_trgm_idx`, migration 0019), forcing a parallel sequential scan on 275K rows.

**Before (EXPLAIN ANALYZE)**: Sequential scan, 2,746ms execution
**After (raw ILIKE)**: Bitmap Index Scan on `player_name_trgm_idx`, 191ms execution

### 4. Two-character queries too broad
Queries like `li` match ~12K rows (nearly 5% of the table), causing even the trigram index to fall back to sequential scan. Three characters is the minimum for the trigram index to be selective.

### 5. Managed Postgres I/O latency
Even with the trigram index, DigitalOcean managed Postgres has variable I/O latency (0.3–9s for the same query), making server-side caching essential.

## Fixes Applied

### Fix 1: Dark mode input styling — DONE
- Added `bg-[var(--bg-page)]`, `text-[var(--text-primary)]`, `placeholder:text-[var(--text-secondary)]` to the input element
- **Location**: `client/app/components/HeaderSearch.tsx:190`

### Fix 2: Client-side suggestion cache — DONE
- Module-level `Map<string, HeaderSearchSuggestion[]>` keyed by lowercase query
- Cache hits skip fetch and debounce entirely — instant results
- FIFO eviction at 200 entries to bound memory
- Persists for the browser session (survives tab navigation)
- **Location**: `client/app/components/HeaderSearch.tsx:21-22` (cache), `:79-84` (cache check)

### Fix 3: Raw ILIKE for trigram index — DONE
- Replaced Django ORM `icontains` with raw SQL using `ILIKE`
- Parameterized query prevents SQL injection
- Both the contains filter (`%query%`) and prefix sort (`query%`) use ILIKE
- **Location**: `server/warships/views.py:834-849`

### Fix 4: Minimum 3-character query — DONE
- Client: suggestions not fetched until `query.trim().length >= 3`
- Server: returns `[]` for queries shorter than 3 characters
- **Location**: `client/app/components/HeaderSearch.tsx:75`, `server/warships/views.py:828`

### Fix 5: Server-side Redis cache — DONE
- Cache key: `suggest:<query_lowercase>`
- TTL: 600 seconds (10 minutes)
- Player names are stable enough that brief staleness is acceptable
- **Location**: `server/warships/views.py:831-833,851`

## Performance Results

| Layer | Latency | When |
|-------|---------|------|
| Client Map cache | 0ms | Revisited prefix in same session |
| Redis cache hit | ~160ms | Any user repeats a query within 10 min |
| Postgres (trigram index) | 200ms–3s | First query for a new prefix (variable DB I/O) |
| **Before all fixes** | **2–22s** | **Every keystroke** |

## Database

- **Table**: `warships_player` (~275K rows)
- **Index**: `player_name_trgm_idx` — GIN index using `gin_trgm_ops`, partial `WHERE name <> ''` (migration 0019)
- **Extension**: `pg_trgm` (already installed)
- **Important**: Do not switch back to Django's `icontains` — it wraps with `UPPER()` which bypasses the trigram index

## Code Locations

- `client/app/components/HeaderSearch.tsx` — Search input, suggestion cache, autocomplete UI
- `server/warships/views.py:824-852` — `player_name_suggestions` endpoint
- `server/warships/migrations/0019_add_player_name_trigram_index.py` — Trigram index migration
