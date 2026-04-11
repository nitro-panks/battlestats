# Runbook: Search Toggle (Player / Clan)

## Summary

The header search bar supports toggling between player search and clan search via a compact pill toggle widget positioned to the left of the search input. By default, the toggle is in the left position (Players). Switching to the right position changes the search context to Clans.

## UI Behavior

- **Toggle widget**: Compact pill/slider with "P" and "C" hint letters. No visible labels.
- **Tooltip**: Shows "Search Players" or "Search Clans" depending on current mode.
- **Placeholder text**: "Search Players" (default) or "Search Clans" when toggled.
- **Accessibility**: `role="switch"`, `aria-checked`, `aria-label`.

### Player mode (default — left position)

- Autocomplete suggestions fetched from `GET /api/landing/player-suggestions?q=<query>&realm=<realm>`.
- Suggestions show a WR-colored dot + player name + hidden icon if applicable.
- Selecting a suggestion or pressing Enter navigates to `/player/<name>?realm=<realm>`.

### Clan mode (right position)

- Autocomplete suggestions fetched from `GET /api/landing/clan-suggestions?q=<query>&realm=<realm>`.
- Suggestions show `[TAG] Clan Name` with member count.
- Selecting a suggestion navigates to `/clan/<clan_id>-<slug>?realm=<realm>`.
- Pressing Enter with no highlighted suggestion auto-selects the first suggestion (there is no freeform `/clan/<name>` route).

### Mode switching

- Switching modes clears the suggestion list immediately.
- The query text is preserved across toggles.
- Separate client-side cache keys: `player:{realm}:{query}` and `clan:{realm}:{query}`.

## Backend Endpoint

### `GET /api/landing/clan-suggestions`

| Param | Required | Description |
|-------|----------|-------------|
| `q` | Yes | Search query (min 3 characters) |
| `realm` | No | Realm filter (default: `na`) |

**Response**: `200 OK`
```json
[
  {
    "clan_id": 12345,
    "tag": "STORM",
    "name": "Storm Fleet",
    "members_count": 40
  }
]
```

- Max 8 results.
- Matches against `Clan.name` OR `Clan.tag` via `ILIKE`.
- Prefix matches sorted first, then by `members_count DESC`.
- Redis cache: `{realm}:clan-suggest:{query}` with 10 min TTL.

### Database indexes

Migration `0048_clan_name_tag_trgm_indexes` adds `pg_trgm` GIN indexes on `warships_clan.name` and `warships_clan.tag` for performant `ILIKE` queries. Requires the `pg_trgm` extension (already enabled for the player name index).

## Files Changed

| File | Change |
|------|--------|
| `client/app/components/SearchModeToggle.tsx` | New toggle component |
| `client/app/components/HeaderSearch.tsx` | Integrated toggle, dual-mode suggestions, clan navigation |
| `server/warships/views.py` | New `clan_name_suggestions()` view |
| `server/battlestats/urls.py` | Registered `api/landing/clan-suggestions` route |
| `server/warships/migrations/0048_clan_name_tag_trgm_indexes.py` | GIN index migration |

## Test Coverage

- **Backend**: `test_views.py` — `ApiContractTests` class covers clan suggestion endpoint: matching, tag matching, short query, realm filtering, null byte safety.
- **Frontend**: `HeaderSearch.test.tsx` — toggle rendering, mode switching, clan endpoint fetch, clan navigation, suggestion clearing.

## Deploy Notes

1. Run `python manage.py migrate` to apply the `pg_trgm` GIN index migration.
2. The `pg_trgm` extension must already be active in the database (it is, for the existing `player_name_trgm_idx`).
3. No new env vars required.
4. No Celery task changes.

## Archive Condition

Archive this runbook when the search toggle is stable and no longer the subject of active iteration.
