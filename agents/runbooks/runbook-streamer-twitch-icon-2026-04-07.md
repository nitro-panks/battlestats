# Runbook: Streamer Twitch Icon

**Created**: 2026-04-07
**Status**: Implemented

## Goal

Add a Twitch icon to player icon trays for known streamers using a static, manually curated flag. The intended icon is the Font Awesome Twitch brand icon (`faTwitch`), rendered alongside the existing player classification badges.

This should be a database-backed manual flag, not an inferred classification. Operators should explicitly mark or unmark known streamer accounts.

## Recommended Data Model

Use a boolean on `Player`:

- `Player.is_streamer = models.BooleanField(default=False)`

Why this is the cleanest fit:

- The existing icon system already reads simple booleans from `Player` or `PlayerExplorerSummary` and renders them into shared icon trays.
- The user wants manual, static curation. A boolean is the smallest reversible slice.
- The database already isolates players by `player_id` and `realm`, so streamer flags can remain account-specific per realm.

Do not key streamer status by player name alone. Names are not the stable identifier. The durable mapping should be by `(player_id, realm)`.

## Initial Known Streamer Mapping

Resolved from the live Battlestats player API on 2026-04-07.

| Display name | Canonical account name | Realm | Player ID | Notes |
| --- | --- | --- | ---: | --- |
| Notser | Notser | `na` | `1005803644` | Valid live account |
| Notser | Notser | `eu` | `551165634` | Valid live account |
| OverlordBou | OverLordBou | `na` | `1007388916` | Live API canonicalizes to `OverLordBou` |
| NProv | NProv | `na` | `1003184455` | User-supplied Battlestats profile resolved successfully |
| messovich | messovich | `eu` | `601662651` | User-supplied Battlestats profile resolved successfully |
| Rita | RitaGamer | `eu` | `504126228` | User-supplied Battlestats profile resolved successfully |
| Viper | vipersocks | `eu` | `501018468` | User-supplied Battlestats profile resolved successfully |
| TheOldManGaming | TheOldManGaming | `eu` | `502025228` | User-supplied Battlestats profile resolved successfully |
| clydethamonkey | CLyDeThaMonKeY | `eu` | `507836339` | Live API canonicalizes to mixed-case account name |
| I_have_no_Minimap | I_have_no_Minimap | `eu` | `618362421` | User-supplied Battlestats profile resolved successfully |

Current non-matches observed during verification:

- `Notser` on `asia`: `404`
- `OverLordBou` on `eu`: `404`
- `OverLordBou` on `asia`: `404`

## Operator-Approved Additions Pending Battlestats Resolution

The following names were explicitly approved by the operator on 2026-04-07 for inclusion in the streamer set:

- `ritagamer2`

Current lookup status:

- `ritagamer2` returned `404` on `na`, `eu`, and `asia`

This should remain an approved streamer candidate, but it cannot be added to the production `(player_id, realm)` mapping until the exact Battlestats-resolvable in-game account name is identified.

Resolution note:

- `vipersocks_` was later traced to the Battlestats account `vipersocks` on `eu` (`501018468`) and has been promoted into the verified mapping table above.

Most likely causes:

- the Twitch handle differs from the in-game account name
- the creator plays under a different realm-specific account name
- the account has not been hydrated or visited in Battlestats under that handle yet

Once the exact in-game account names are known, promote them into the ID-backed mapping table above.

## Additional Platform Discovery

Public platform snapshots were checked on 2026-04-07 using:

- Twitch category page for World of Warships
- YouTube search results for `world of warships stream`
- YouTube search results for `world of warships cc`

### Twitch-discovered creator candidates

These names appeared directly on the Twitch World of Warships category page and look like plausible streamer candidates for manual review:

- `chaosmachinegr`
- `ritagamer2`
- `paniramix`
- `statsbloke`
- `serenityblizzard`
- `theoldmangamingno`
- `vipersocks_`
- `davyj90`
- `der7tezwerg_`
- `clydethamonkey`

Additional Twitch names supplied by the operator on 2026-04-07 and checked against Battlestats:

- `windstorm266`
- `battlefieldbadco1`
- `Tsaver`
- `Amyntikos10`
- `RedTigerrf`
- `jensi2018`
- `lafamiliaHazze`
- `unlivingskunk`
- `u_n_d_e_r_w_o_o_d`
- `7sedoy7angel7`
- `sezuooo`
- `the1andonlyeee`
- `termiite`
- `mrrecoOn`
- `kabanauts`
- `mangamingofc`
- `wolle_1887`

### YouTube-discovered creator candidates

These channels appeared in the YouTube result snapshots and look relevant to World of Warships creator coverage:

- `Potato Quality`
- `Flamu`
- `Normal Guy of the North`
- `World of Warships Official Channel`
- `World of Warships - Best Moments`

### Additional Battlestats-verified matches

The following creator names were checked against the live Battlestats player API and resolved successfully.

| Creator name | Realm | Player ID | Canonical account name |
| --- | --- | ---: | --- |
| StatsBloke | `na` | `1032207698` | `StatsBloke` |
| StatsBloke | `eu` | `544862717` | `StatsBloke` |
| ChaosMachineGR | `eu` | `546077628` | `ChaosMachineGR` |
| Paniramix | `na` | `1040354269` | `Paniramix` |
| Paniramix | `eu` | `547072276` | `Paniramix` |
| Paniramix | `asia` | `2045404269` | `Paniramix` |
| DavyJ90 | `eu` | `519830231` | `davyJ90` |
| Der7teZwerg_ | `eu` | `538815436` | `Der7teZwerg_` |
| Flamu | `eu` | `539072843` | `Flamu` |
| RitaGamer | `eu` | `504126228` | `RitaGamer` |
| vipersocks | `eu` | `501018468` | `vipersocks` |
| TheOldManGaming | `eu` | `502025228` | `TheOldManGaming` |
| clydethamonkey | `eu` | `507836339` | `CLyDeThaMonKeY` |
| I_have_no_Minimap | `eu` | `618362421` | `I_have_no_Minimap` |
| NProv | `na` | `1003184455` | `NProv` |
| messovich | `eu` | `601662651` | `messovich` |
| battlefieldbadco1 | `eu` | `586236219` | `Battlefieldbadco1` |
| Tsaver | `eu` | `501896318` | `Tsaver` |
| lafamiliaHazze | `eu` | `575820384` | `LaFamiliaHaZze` |
| unlivingskunk | `na` | `1043979835` | `UnlivingSkunk` |
| u_n_d_e_r_w_o_o_d | `eu` | `588405399` | `U_N_D_E_R_W_O_O_D` |
| sezuooo | `eu` | `571198679` | `SEZUOOO` |
| the1andonlyeee | `eu` | `531307702` | `the1andOnlyEEE` |
| termiite | `na` | `1076018645` | `Termiite` |

### Platform names seen but not yet confirmed to Battlestats accounts

These names were visible on Twitch or YouTube, but were not yet confirmed in the Battlestats lookup pass done for this runbook update:

- `serenityblizzard`
- `theoldmangamingno` (likely platform handle distinct from verified Battlestats account `TheOldManGaming`)
- `windstorm266`
- `Amyntikos10`
- `RedTigerrf`
- `jensi2018`
- `7sedoy7angel7`
- `mrrecoOn`
- `kabanauts`
- `mangamingofc`
- `wolle_1887`
- `Potato Quality`
- `Normal Guy of the North`

These should be treated as review candidates, not production mapping entries, until a corresponding `(player_id, realm)` record is confirmed.

## Manual Update Workflow

Recommended operator workflow after the boolean exists:

1. Find the target account by `player_id` and `realm`.
2. Flip `is_streamer = true` for approved streamer accounts.
3. Flip `is_streamer = false` to remove the badge.

Example SQL:

```sql
UPDATE warships_player
SET is_streamer = TRUE
WHERE player_id = 1005803644 AND realm = 'na';

UPDATE warships_player
SET is_streamer = TRUE
WHERE player_id = 551165634 AND realm = 'eu';

UPDATE warships_player
SET is_streamer = TRUE
WHERE player_id = 1007388916 AND realm = 'na';
```

Equivalent Django shell shape:

```python
from warships.models import Player

Player.objects.filter(player_id=1005803644, realm='na').update(is_streamer=True)
Player.objects.filter(player_id=551165634, realm='eu').update(is_streamer=True)
Player.objects.filter(player_id=1007388916, realm='na').update(is_streamer=True)
```

## Backend Changes

### 1. Model and migration

Add the flag on [server/warships/models.py](/home/august/code/archive/battlestats/server/warships/models.py).

Likely change:

- Add `is_streamer = models.BooleanField(default=False)` to `Player`
- Generate a migration in `server/warships/migrations/`

### 2. Player detail payload

Expose the new flag in the player detail serializer path used by `/api/player/<name>/`.

Touchpoints:

- [server/warships/views.py](/home/august/code/archive/battlestats/server/warships/views.py)
- [server/warships/serializers.py](/home/august/code/archive/battlestats/server/warships/serializers.py)
- [server/warships/data.py](/home/august/code/archive/battlestats/server/warships/data.py)

Expected work:

- Include `is_streamer` in the player serializer / summary payload returned to the frontend.

### 3. Landing player payload

Expose the flag in landing-player rows so the landing page icon tray can render it.

Touchpoint:

- [server/warships/landing.py](/home/august/code/archive/battlestats/server/warships/landing.py)

Expected work:

- Include `is_streamer` in `_serialize_landing_player_rows()`.
- Ensure the corresponding player lookup includes the field when loading `Player` rows.

### 4. Optional clan member payload

If the Twitch icon should also appear in clan member trays, expose the flag through the clan-members payload.

Likely touchpoints:

- [server/warships/views.py](/home/august/code/archive/battlestats/server/warships/views.py)
- [server/warships/serializers.py](/home/august/code/archive/battlestats/server/warships/serializers.py)

## Frontend Changes

### 1. Shared icon component

Add a dedicated shared component, matching the existing badge pattern established by the other icon components.

Recommended new file:

- `client/app/components/TwitchStreamerIcon.tsx`

Implementation shape:

- Use `FontAwesomeIcon` from `@fortawesome/react-fontawesome`
- Use `faTwitch` from `@fortawesome/free-brands-svg-icons`
- Support the existing size variants: `header`, `inline`, `search`
- Use the same wrapper pattern as the other icons for `title`, `aria-label`, and `aria-hidden`

### 2. Player detail header tray

Add the icon to the player detail header tray in [client/app/components/PlayerDetail.tsx](/home/august/code/archive/battlestats/client/app/components/PlayerDetail.tsx).

Expected work:

- Extend the `player` prop shape with `is_streamer?: boolean`
- Render `<TwitchStreamerIcon size="header" />` in the existing badge row

### 3. Landing / search player trays

Add the icon to landing-page player trays in [client/app/components/PlayerSearch.tsx](/home/august/code/archive/battlestats/client/app/components/PlayerSearch.tsx).

Expected work:

- Extend `LandingPlayer` with `is_streamer?: boolean` in [client/app/components/entityTypes.ts](/home/august/code/archive/battlestats/client/app/components/entityTypes.ts)
- Render `<TwitchStreamerIcon size="search" />` in `PlayerNameGrid`

### 4. Clan member trays

If clan member trays should also show streamer status, update [client/app/components/ClanMembers.tsx](/home/august/code/archive/battlestats/client/app/components/ClanMembers.tsx).

Expected work:

- Extend `ClanMemberData` to carry `is_streamer?: boolean`
- Render `<TwitchStreamerIcon size="inline" />` in both hidden and clickable member rows

## QA Findings

Runbook QA against the current codebase found one scope correction before implementation:

- The normalized contract artifacts in `agents/contracts/data-products/` track `PlayerSummarySerializer` and `PlayerExplorerRowSerializer`, not the player detail serializer or landing payloads used by this feature.
- The implemented slice therefore leaves `player-summary.odcs.yaml` and `player-explorer-rows.odcs.yaml` unchanged.
- The shipped payload changes are limited to `PlayerSerializer`, landing-player rows, and clan-member rows.

This keeps the rollout to the smallest safe vertical slice while still covering every tray surface that renders the new Twitch icon.

## Type And Contract Updates

No normalized contract changes were required for the shipped slice.

Reason:

- [server/warships/tests/test_data_product_contracts.py](/home/august/code/archive/battlestats/server/warships/tests/test_data_product_contracts.py) only enforces alignment for `PlayerSummarySerializer` and `PlayerExplorerRowSerializer`.
- The streamer badge rollout does not modify those serializers in the current implementation slice.

If streamer status is later added to the normalized summary or explorer APIs, update those ODCS artifacts and the contract-alignment test in the same commit.

## Test Coverage To Add

### Backend

Add serializer / view coverage proving the new field appears.

Likely file:

- [server/warships/tests/test_views.py](/home/august/code/archive/battlestats/server/warships/tests/test_views.py)

Implemented tests:

- player detail response exposes `is_streamer`
- landing random and recent player rows expose `is_streamer`
- clan members endpoint exposes `is_streamer`

### Frontend

Recommended files:

- `client/app/components/__tests__/PlayerDetail.test.tsx`
- `client/app/components/__tests__/PlayerSearch.test.tsx`

Implemented assertions:

- Twitch icon renders for flagged player detail payloads
- Twitch icon renders for flagged landing rows without displacing the existing badge set

## Rollout Notes

Shipped implementation:

1. Added `Player.is_streamer` with migration `0042_player_is_streamer`.
2. Exposed the flag through player detail, landing/search rows, and clan-member rows.
3. Added `TwitchStreamerIcon.tsx` and rendered it in the player detail, landing/search, and clan member icon trays.
4. Added focused backend and frontend regression coverage.

Operational note:

- After schema deploy, approved streamer accounts still need manual `is_streamer = true` updates in the target environment.
- Landing and player-detail caches may continue to serve pre-flag payloads until expiry or refresh, so perform the flag updates after the deploy and let the normal cache warm cycle republish the rows.

## Validation

Implementation validation performed during rollout:

- generated Django migration `server/warships/migrations/0042_player_is_streamer.py`
- synced client dependency lockfile for `@fortawesome/free-brands-svg-icons`
- added focused backend coverage in [server/warships/tests/test_views.py](/home/august/code/archive/battlestats/server/warships/tests/test_views.py)
- added focused frontend coverage in [client/app/components/__tests__/PlayerDetail.test.tsx](/home/august/code/archive/battlestats/client/app/components/__tests__/PlayerDetail.test.tsx) and [client/app/components/__tests__/PlayerSearch.test.tsx](/home/august/code/archive/battlestats/client/app/components/__tests__/PlayerSearch.test.tsx)

## Open Decision

The remaining product choice is scope:

- Minimum scope: player detail header + landing/search player trays
- Expanded scope: also show the Twitch icon in clan member trays

The user direction so far supports the expanded scope because the icon “could live in the player icon trays,” and clan member rows already use the same tray pattern.