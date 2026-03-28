# Runbook: Icon Analysis — Generation, Refresh, Caching & Harmonization

**Created**: 2026-03-28
**Status**: Complete — all harmonization fixes implemented and deployed 2026-03-28

## Icon Inventory

Battlestats renders 7 distinct icon types across 3 surfaces (player detail header, clan members table, landing/search results). Each icon communicates a player classification derived from backend data.

| Icon | Symbol | Surfaces | Backend Source |
|------|--------|----------|----------------|
| Hidden Account | `faMask` | Player header | `Player.is_hidden` |
| Efficiency Rank | `Σ` circle badge | Player header, clan members, landing | `PlayerExplorerSummary.efficiency_rank_tier` |
| Clan Leader | `faCrown` | Player header, clan members | `Clan.leader_id` / `Clan.leader_name` |
| PvE Enjoyer | `faRobot` | Player header, clan members, landing | `is_pve_player(total_battles, pvp_battles)` |
| Sleepy/Inactive | `faBed` | Player header, clan members, landing | `is_sleepy_player(days_since_last_battle)` |
| Ranked Player | `faStar` | Player header, clan members, landing | `is_ranked_player(ranked_json)` |
| Clan Battle | `faShieldHalved` | Player header, clan members, landing | `is_clan_battle_enjoyer(battles, seasons)` |

---

## Generation & Refresh Analysis

### 1. Hidden Account (`faMask`)

**Generation**: Set by `update_player_data()` from WG API personal data response. Stored as `Player.is_hidden` boolean.

**Refresh cycle**: Every 1440 minutes (~24h) via `update_player_data()` freshness check.

**Assessment**: **Adequate.** Hidden status rarely changes. 24h refresh is appropriate. No computation cost — simple boolean field read.

### 2. Efficiency Rank (`Σ` badge)

**Generation**: Multi-step pipeline:
1. `update_player_efficiency_data()` fetches WG badge data per player (stale after 24h)
2. `_build_efficiency_rank_inputs()` computes normalized badge strength per player
3. `_recompute_efficiency_rank_snapshot_sql()` ranks all eligible players via `PERCENT_RANK()` window function, writes tier (E/I/II/III) and percentile to `PlayerExplorerSummary`

**Refresh cycle**:
- Per-player badge data: 24h (`PLAYER_EFFICIENCY_STALE_AFTER`)
- Population-wide rank snapshot: 48h (`EFFICIENCY_RANK_SNAPSHOT_STALE_AFTER`)
- Dispatch dedup: 15 minutes (`EFFICIENCY_SNAPSHOT_REFRESH_DISPATCH_TIMEOUT`)

**Freshness gate**: `_get_published_efficiency_rank_payload()` returns the icon only if the snapshot is fresh AND the player's underlying badge data hasn't changed since the snapshot. If stale, returns `has_efficiency_rank_icon: false` — the icon disappears until the next snapshot.

**Assessment**: **Appropriate but with a visibility gap.** When a snapshot expires (every 48h), all efficiency icons temporarily vanish until recomputation completes (~37s). This is by design (correctness over stale display) but can confuse users who see icons appear and disappear. The 48h window is reasonable for a population-level statistic.

### 3. Clan Leader (`faCrown`)

**Generation**: Set by `update_clan_data()` from WG API clan info. Stored as `Clan.leader_id` and `Clan.leader_name`.

**Refresh cycle**: Every 1440 minutes (~24h) via `clan_detail_needs_refresh()`.

**Assessment**: **Adequate.** Leader changes are rare. Matching logic uses both `leader_id` and `leader_name` fallback — robust.

### 4. PvE Enjoyer (`faRobot`)

**Generation**: Computed client-side from `is_pve_player(total_battles, pvp_battles)`. The function checks whether PvP battles are a minority of total battles. Data comes from `Player.total_battles` and `Player.pvp_battles`.

**Refresh cycle**: Updated whenever battle data refreshes (stale after ~1h via `player_battle_data_needs_refresh()`).

**Assessment**: **Adequate.** Lightweight boolean derived from already-refreshed fields. No separate computation or caching needed.

### 5. Sleepy/Inactive (`faBed`)

**Generation**: Computed client-side from `is_sleepy_player(days_since_last_battle)`. Checks if `days_since_last_battle > 365`.

**Refresh cycle**: `days_since_last_battle` updated during `update_player_data()` (~24h cycle).

**Assessment**: **Adequate.** The 24h refresh means this icon can lag by up to a day when a dormant player returns, but for a 365-day threshold this is immaterial.

### 6. Ranked Player (`faStar`)

**Generation**: Computed from `is_ranked_player(ranked_json)` — checks if any ranked season data exists. League color derived from `get_highest_ranked_league_name(ranked_json)`.

**Refresh cycle**: Ranked data stale after 24h (`player_ranked_data_needs_refresh()`). Incremental ranked crawl also refreshes periodically.

**Assessment**: **Adequate.** Ranked seasons change infrequently. 24h refresh is appropriate.

### 7. Clan Battle (`faShieldHalved`)

**Generation**: Computed from `is_clan_battle_enjoyer(clan_battle_total_battles, clan_battle_seasons_participated)`. Requires 40+ battles and 2+ seasons. Win rate from `clan_battle_overall_win_rate`.

**Refresh cycle**: Stale after 7 days (`CLAN_BATTLE_SUMMARY_STALE_DAYS`). Refreshed via `maybe_refresh_clan_battle_data()` on clan member page visits.

**Assessment**: **Adequate.** Clan battle data is seasonal and changes slowly. 7-day refresh is appropriate. Visit-triggered refresh ensures active clans stay current.

---

## Caching & Performance Analysis

### Data availability by surface

| Surface | Data source | Cached? | Latency |
|---------|------------|---------|---------|
| **Player detail header** | `fetch_player_summary()` → `build_player_summary()` | No response cache; DB read per request | ~230ms p50, 304ms p95 |
| **Clan members table** | `clan_members` view | **Redis response cache (5min TTL)** | ~200ms cache hit |
| **Landing/search** | `get_landing_players_payload()` | **Redis published cache (12h TTL)** | <50ms cache hit |

### Icon-specific caching

| Icon | Pre-computed? | Stored in DB? | Requires API call? |
|------|--------------|---------------|-------------------|
| Hidden | Yes | `Player.is_hidden` | No (read from DB) |
| Efficiency Rank | Yes | `PlayerExplorerSummary.*` | No (snapshot pre-computed) |
| Clan Leader | Yes | `Clan.leader_id` | No (read from DB) |
| PvE | No | Derived from `Player` fields | No |
| Sleepy | No | Derived from `Player.days_since_last_battle` | No |
| Ranked | No | Derived from `Player.ranked_json` | No |
| Clan Battle | Yes | `PlayerExplorerSummary.clan_battle_*` | No |

**Assessment**: **Good.** All icon data is either pre-computed in the DB or derived from already-loaded fields. No icon triggers an external API call at render time. The heaviest icon (Efficiency Rank) uses a batch SQL snapshot rather than per-player computation.

---

## Harmonization Opportunities

### H1: Duplicated icon definitions across 3 surfaces

The same icons are defined as separate components in 3 files with inconsistent patterns:

| Location | Pattern | Size | Wrapper |
|----------|---------|------|---------|
| `ClanMembers.tsx` | Bare `<FontAwesomeIcon>` | `text-[11px]` | None — FA component is the root element |
| `PlayerDetail.tsx` | `<span>` wrapping `<FontAwesomeIcon>` | `text-sm` | `<span className="inline-flex items-center cursor-help">` |
| `PlayerSearch.tsx` | `<span>` wrapping `<FontAwesomeIcon>` | `text-xs` | `<span className="inline-flex items-center cursor-help">` |

**Issues:**
- `ClanMembers.tsx` icons lack `cursor-help` and the wrapping `<span>` pattern, making hover/focus behavior inconsistent
- `ClanMembers.tsx` uses `text-[11px]` while `PlayerSearch.tsx` uses `text-xs` (12px) — near-identical but not the same
- `PlayerDetail.tsx` `HeaderClanBattleShield` doesn't accept `null` winRate (requires `number`), while `ClanMembers.tsx` and `PlayerSearch.tsx` versions accept `null`
- `PlayerDetail.tsx` uses `selectColorByWR()` for clan battle shield color; `ClanMembers.tsx` uses `wrColor()` — potentially different color scales
- 5 icon types are copy-pasted 3 times each = 15 component definitions for 5 logical icons

**Fix**: Extract shared icon components to dedicated files (like `HiddenAccountIcon` and `EfficiencyRankIcon` already are) with a `size` prop. One definition per icon, consumed everywhere.

### H2: Inconsistent accessibility patterns

- `ClanMembers.tsx`: `title` and `aria-label` directly on `<FontAwesomeIcon>` — works but FontAwesome may not forward `aria-label` correctly to the SVG
- `PlayerDetail.tsx` and `PlayerSearch.tsx`: `aria-label` on wrapper `<span>`, `aria-hidden="true"` on the icon — correct accessible pattern
- `HiddenAccountIcon.tsx`: Follows the correct wrapper pattern
- `EfficiencyRankIcon.tsx`: Uses `aria-label` on the `<span>` — correct

**Fix**: Adopt the wrapper pattern (`<span aria-label="..."><FontAwesomeIcon aria-hidden="true" /></span>`) universally. The `ClanMembers.tsx` icons should be updated.

### H3: Duplicated WR color function

**QA correction**: `selectColorByWR` and `wrColor` produce identical colors (same thresholds, same hex values). The only difference is `wrColor` accepts `null`. Both were duplicated across ~10 files (components + chart modules).

**Fix**: Extract to `client/app/lib/wrColor.ts` and import everywhere.

### H4: ~~Efficiency Rank icon visibility threshold inconsistency~~ (Not an issue)

**QA correction**: All three surfaces (PlayerDetail, ClanMembers, PlayerSearch) use `=== 'E'` — Expert only. There is no inconsistency. The runbook's original claim that clan members and landing show all tiers was wrong.

---

## Implementation Status (2026-03-28)

| Fix | Status |
|-----|--------|
| H1: Shared icon components | **Done** — 5 new components with `size` prop (`header`/`inline`/`search`) |
| H2: Accessibility pattern | **Done** — all icons now use `<span aria-label><FA aria-hidden /></span>` |
| H3: Shared WR color | **Done** — `client/app/lib/wrColor.ts`, removed ~10 local copies |
| H4: Efficiency visibility | **No action needed** — all surfaces consistent (Expert only) |

### Files created
- `client/app/lib/wrColor.ts`
- `client/app/components/LeaderCrownIcon.tsx`
- `client/app/components/PveEnjoyerIcon.tsx`
- `client/app/components/InactiveIcon.tsx`
- `client/app/components/RankedPlayerIcon.tsx`
- `client/app/components/ClanBattleShieldIcon.tsx`

### Files modified
- `client/app/components/ClanMembers.tsx` — replaced 5 inline icons + local `wrColor`
- `client/app/components/PlayerDetail.tsx` — replaced 5 inline icons + local `selectColorByWR`
- `client/app/components/PlayerSearch.tsx` — replaced 4 inline icons + local `wrColor`
- `client/app/components/HeaderSearch.tsx` — replaced local `wrColor`

## Summary

| Area | Verdict |
|------|---------|
| Generation timing | All appropriate — refresh cycles match data volatility |
| Caching | Good — all icon data is pre-computed or derived from cached fields |
| Performance | No icon triggers API calls at render time; heaviest (efficiency) uses batch SQL |
| Harmonization | **Resolved** — 15 duplicate definitions consolidated to 5 shared components |
| Accessibility | **Resolved** — all icons use correct wrapper pattern |
| Color consistency | **Resolved** — single `wrColor` utility shared across all surfaces |
