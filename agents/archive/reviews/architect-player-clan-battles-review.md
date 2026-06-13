# Architect Review — Player Profile Clan Battles

## Verdict

Approved for implementation with one hard requirement: the player detail page must use a **player-scoped clan battle contract**, not the existing clan aggregate endpoint.

## Findings

1. The backend already contains the correct upstream primitive for this feature in the cached per-player clan battle fetch path, so no new WG integration is needed.
2. The existing clan battle UI and serializer are clan-scoped and summarize the current roster, which would be semantically wrong on player detail.
3. The proposed placement under the clan list is architecturally sound because it keeps clan-context surfaces together in the left column and avoids mixing this feature into the right-column personal chart stack.
4. The existing `_clan_battle_season_sort_key(...)` logic must be reused so season ordering stays based on metadata dates rather than raw WG season ids.

## Requirements

1. Add a dedicated player clan battle serializer and endpoint.
2. Reuse `_get_player_clan_battle_season_stats(account_id)` and `_get_clan_battle_seasons_metadata()` rather than duplicating fetch logic in the view.
3. Keep the endpoint flat and season-oriented for MVP.
4. Preserve existing clan detail clan-battle behavior unchanged.

## Recommended UI Shape

1. Summary strip with seasons played, total battles, and overall WR.
2. Compact season table underneath.
3. Defer heavier charts until the contract proves stable in real usage.

## Risks

1. Reusing clan aggregate data on player detail would mislead users.
2. Adding a large chart in the left column too early could overload that lane.
3. A second, redundant cache layer would add complexity without current evidence it is needed.

## Review Outcome

Proceed with MVP implementation as scoped in the spec.
