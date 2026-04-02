# Runbook: Realm-Aware URLs (Disambiguation)

**Created**: 2026-04-01
**Status**: Implemented / Completed (2026-04-02)
**Depends on**: `spec-multi-realm-eu-support.md`, `runbook-multi-realm-hardening.md` (Phases 1-6 complete)
**Goal**: Embed the active realm in player and clan page URLs so that links are unambiguous, shareable, and indexable per realm.

---

## Problem

Player and clan page URLs currently carry no realm information:

- `/player/Captain123`
- `/clan/500012345-storm`

The active realm is resolved entirely from `RealmContext` (localStorage `bs-realm`). This causes three concrete failures:

1. **Shared links go to the wrong realm.** An NA user shares `/player/Captain123`. An EU user clicks it and sees the EU player with the same name — or a 404.
2. **The Share (copy) button copies a realmless URL.** `PlayerDetail` and `ClanDetail` copy `window.location.href`, which has no realm info. The copied link is ambiguous.
3. **SEO/sitemap collision.** `generateMetadata` produces a single canonical URL per player name. If the same name exists on both realms, only one can be indexed. The sitemap has no realm dimension.
4. **Footer attribution link is realmless.** The `lil_boots` link in `Footer.tsx` uses `buildPlayerPath('lil_boots')` with no realm, so it resolves to whichever realm the viewer has selected — which may not be NA where that player exists.

The multi-realm spec (`spec-multi-realm-eu-support.md`, line 39) deferred this explicitly:

> _"deep-linking can be added later via an optional `?realm=eu` query param on page URLs."_

This runbook implements that resolution.

---

## Design Decision: Query Parameter vs Path Segment

Two options were evaluated:

|                     | Query param (`?realm=eu`)                                                                                          | Path segment (`/eu/player/...`)                                |
| ------------------- | ------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------- |
| Routing change      | None — existing `[playerName]` / `[clanSlug]` routes unchanged                                                     | New `[realm]` layout segment or route group                    |
| SEO                 | Canonical must include `?realm=` to deduplicate; search engines generally respect this but it is less conventional | Clean per-realm canonicals; standard path-based indexing       |
| Shareability        | Works, but query params are easily stripped by chat apps and shorteners                                            | Robust — path segments survive all sharing contexts            |
| Implementation cost | Low — touches `buildPlayerPath`, `buildClanPath`, copy handler, metadata, sitemap                                  | Medium — new dynamic route segment, layout changes, middleware |
| Backward compat     | Old URLs still work (default to `na`)                                                                              | Needs redirect from old paths to `/na/...`                     |

**Decision: Query parameter.** The spec already anticipated this approach. It avoids a routing refactor, preserves backward compatibility (realmless URLs default to NA), and can be upgraded to path segments later if SEO requirements escalate. The key downside (strippable params) is acceptable for a stats site where the realm selector is always visible.

---

## Scope

### In scope

- `buildPlayerPath` and `buildClanPath` accept and embed `realm` in URLs
- Share/copy buttons produce realm-qualified URLs
- Footer `lil_boots` attribution link hardcodes `na` realm
- `generateMetadata` produces realm-qualified canonical URLs and OG tags
- `sitemap.ts` emits realm-qualified URLs
- Inbound URL `?realm=` param seeds `RealmContext` on page load (URL wins over localStorage)
- `RealmSelector` navigates with `?realm=` preserved

### Out of scope

- Path-segment routing (`/eu/player/...`)
- Backend changes (API `?realm=` on backend calls is already implemented)
- Asia realm activation

---

## Phase 1 — URL Builders Accept Realm

**Files**: `client/app/lib/entityRoutes.ts`, `client/app/lib/__tests__/entityRoutes.test.ts`

### Changes

1. Add `realm` parameter to `buildPlayerPath`:

   ```typescript
   export const buildPlayerPath = (
     playerName: string,
     realm?: string,
   ): string => {
     const base = `/player/${encodeURIComponent(playerName.trim())}`;
     return realm ? `${base}?realm=${realm}` : base;
   };
   ```

2. Add `realm` parameter to `buildClanPath`:

   ```typescript
   export const buildClanPath = (
     clanId: number | string,
     clanName?: string,
     realm?: string,
   ): string => {
     const normalizedId = String(clanId).trim();
     const slug = slugifySegment(clanName || "");
     const base = slug
       ? `/clan/${normalizedId}-${slug}`
       : `/clan/${normalizedId}`;
     return realm ? `${base}?realm=${realm}` : base;
   };
   ```

3. Update tests in `entityRoutes.test.ts` — add cases for realm param present and absent.

### Validation

- `npm test -- --runInBand client/app/lib/__tests__/entityRoutes.test.ts` passes
- Existing callers that don't pass `realm` continue to produce realmless URLs (backward compat)

---

## Phase 2 — All Navigation Callers Pass Realm

**Files**: Every file that imports `buildPlayerPath` or `buildClanPath`

### Callers to update

| File                  | Call                                 | Realm source                   |
| --------------------- | ------------------------------------ | ------------------------------ |
| `HeaderSearch.tsx`    | `buildPlayerPath(trimmedQuery)`      | `useRealm()`                   |
| `PlayerRouteView.tsx` | `buildPlayerPath(memberName)`        | `useRealm()`                   |
| `PlayerRouteView.tsx` | `buildClanPath(clanId, clanName)`    | `useRealm()`                   |
| `PlayerDetail.tsx`    | `buildClanPath(player.clan_id, ...)` | `useRealm()`                   |
| `PlayerSearch.tsx`    | `buildPlayerPath(memberName)`        | `useRealm()`                   |
| `PlayerSearch.tsx`    | `buildClanPath(clan.clan_id, ...)`   | `useRealm()`                   |
| `ClanRouteView.tsx`   | `buildPlayerPath(memberName)`        | `useRealm()`                   |
| `Footer.tsx`          | `buildPlayerPath('lil_boots')`       | Hardcoded `'na'` (see Phase 5) |

Each caller already has access to `useRealm()` (all are `"use client"` components). Pass `realm` as the final argument.

### Validation

- `npm test -- --runInBand` — all unit tests pass
- Manual: switch to EU, navigate to a player, inspect URL — should contain `?realm=eu`
- Manual: switch to NA, navigate — should contain `?realm=na`

---

## Phase 3 — Inbound URL Realm Seeds Context

**Files**: `client/app/context/RealmContext.tsx`, player/clan page components

When a page loads with `?realm=eu` in the URL, the realm context must pick it up. URL takes precedence over localStorage.

### Changes

1. In `RealmContext.tsx`, on initial mount read `window.location.search` for a `realm` param. If it's a valid realm (`na` | `eu`), use it as the initial value and persist to localStorage:

   ```typescript
   const urlRealm = new URLSearchParams(window.location.search).get("realm");
   const initial =
     urlRealm && VALID_REALMS.includes(urlRealm) ? urlRealm : storedRealm;
   ```

2. When `RealmSelector` changes realm, update the URL query param in addition to localStorage. Use `router.replace` (not push) to avoid polluting history:

   ```typescript
   const url = new URL(window.location.href);
   url.searchParams.set("realm", newRealm);
   router.replace(url.pathname + url.search);
   ```

3. If user is on a player/clan page and switches realm via selector, current behavior redirects to `/`. This should instead redirect to the same entity path with the new `?realm=` — the backend will resolve it for the new realm (or return a 404/not-found state if the entity doesn't exist there).

### Validation

- Open `/player/SomeName?realm=eu` in a fresh incognito window — realm selector should show EU
- Change realm via selector on a player page — URL should update to `?realm=na`
- Paste a `?realm=eu` link when localStorage says `na` — EU should win

---

## Phase 4 — Share Button Copies Realm-Qualified URL

**Files**: `client/app/components/PlayerDetail.tsx`, `client/app/components/ClanDetail.tsx`

### Changes

Both components already copy `window.location.href`. After Phase 3, `window.location.href` will include `?realm=` if the user navigated via a realm-aware link or the selector updated the URL. However, as a safety net, the copy handler should ensure the realm param is present:

```typescript
const handleShare = async () => {
  try {
    const url = new URL(window.location.href);
    if (!url.searchParams.has("realm")) {
      url.searchParams.set("realm", realm); // from useRealm()
    }
    await navigator.clipboard.writeText(url.toString());
    setShareState("copied");
  } catch (error) {
    console.error("Failed to copy URL:", error);
    setShareState("failed");
  }
};
```

### Validation

- Navigate to a player, click Share, paste — URL includes `?realm=na` or `?realm=eu`
- Manually strip `?realm=` from address bar, click Share — copied URL still has realm

---

## Phase 5 — Footer Attribution Link

**File**: `client/app/components/Footer.tsx`

### Changes

The `lil_boots` player link should always point to the NA realm since that's where the account exists. Hardcode `'na'`:

```typescript
<Link href={buildPlayerPath('lil_boots', 'na')} ...>
```

This is intentionally not `useRealm()` — the attribution link should always resolve to the correct player regardless of the viewer's realm context.

### Validation

- With EU selected, click `lil_boots` in footer — URL should be `/player/lil_boots?realm=na`, page should load the NA player

---

## Phase 6 — SEO: Metadata and Sitemap

**Files**: `client/app/player/[playerName]/page.tsx`, `client/app/clan/[clanSlug]/page.tsx`, `client/app/sitemap.ts`

### Metadata

`generateMetadata` currently produces a canonical URL without realm. Since this function runs server-side and doesn't have access to `RealmContext`, it needs to read the `realm` query param from the request:

1. In `generateMetadata`, read `searchParams` (Next.js passes these to page-level metadata functions):

   ```typescript
   interface PlayerPageProps {
     params: Promise<{ playerName: string }>;
     searchParams: Promise<{ realm?: string }>;
   }

   export async function generateMetadata({
     params,
     searchParams,
   }: PlayerPageProps): Promise<Metadata> {
     const { playerName } = await params;
     const { realm } = await searchParams;
     const realmParam = realm && ["na", "eu"].includes(realm) ? realm : "na";
     const decoded = decodeURIComponent(playerName);
     const url = getSiteUrl(`/player/${playerName}?realm=${realmParam}`);
     // ... canonical, OG, twitter all use this url
   }
   ```

2. Apply the same pattern to the clan page metadata.

### Sitemap

`client/app/sitemap.ts` currently fetches entities from `/api/sitemap-entities/`. Update to:

1. Fetch entities per realm (the backend endpoint already accepts `?realm=`).
2. Emit one sitemap entry per entity per realm, with `?realm=` in the URL.

### Validation

- View source on `/player/SomeName?realm=eu` — canonical should include `?realm=eu`
- `curl` the sitemap — entries should have realm-qualified URLs
- No duplicate canonicals across realms for the same player name

---

## Phase 7 — Test Coverage

**Files**: New and existing test files

### New tests

1. **`entityRoutes.test.ts`** — `buildPlayerPath` and `buildClanPath` with realm param
2. **`RealmContext` test** — URL param seeds context, URL wins over localStorage
3. **`PlayerDetail` / `ClanDetail` test** — Share button always produces realm-qualified URL
4. **`Footer` test** — `lil_boots` link includes `?realm=na`
5. **Sitemap test** — Entries are realm-qualified

### Updated tests

- `PlayerRouteView.test.tsx`, `ClanRouteView.test.tsx` — Navigation calls include `?realm=`
- `siteOrigin.test.ts` — If `getSiteUrl` is involved in canonical generation

### Validation

- `npm test -- --runInBand` — all pass
- `npm run build` — no type errors

---

## Rollback

Every phase is additive. Realmless URLs continue to work (default to NA). If issues arise:

1. Revert the URL builder changes — callers fall back to realmless paths.
2. `RealmContext` falls back to localStorage-only resolution.
3. Share button falls back to copying `window.location.href` as-is.

No database or backend changes required. No migrations to reverse.

---

## Acceptance Criteria

- [ ] `/player/Captain123?realm=eu` loads the EU player
- [ ] `/player/Captain123?realm=na` loads the NA player
- [ ] `/player/Captain123` (no param) defaults to NA
- [ ] Share button always copies a realm-qualified URL
- [ ] Pasting a `?realm=eu` link overrides the viewer's localStorage realm
- [ ] Realm selector updates the URL query param in place
- [ ] Footer `lil_boots` link always points to NA
- [ ] Sitemap has separate realm-qualified entries per realm
- [ ] Canonical URLs in `<head>` include realm
- [ ] All existing tests pass; new realm-URL tests added
- [ ] `npm run build` succeeds with no type errors
