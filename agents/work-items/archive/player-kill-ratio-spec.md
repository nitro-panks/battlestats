# Player Kill Ratio (KR) Metric — PM Task

**Owner:** Project Manager Agent
**Date:** 2026-03-09
**Status:** Ready for engineering

## Objective

Define and implement a player-level "Kill Ratio" (KR) metric for use in the Player Explorer and player summary surfaces. This metric should be available for all players and should be consistent with per-ship KDR logic already present in the codebase.

## Background

- Per-ship KDR is currently calculated as `frags / pvp_battles` and exposed as `kdr` in ship stats.
- There is no player-level KR/KDR metric currently exposed in the player summary or explorer.
- The Player Explorer table needs a "Kill Ratio" column for each player.

## Requirements

- **Definition:**
  - Player-level KR should be defined as `total_pvp_frags / total_pvp_battles` (where frags = total ships destroyed in PvP, battles = total PvP battles played).
  - If a more robust definition is needed (e.g., accounting for deaths), clarify with engineering/analytics.
- **Source:**
  - Use the sum of `frags` from all PvP battles for the player (available from the WoWS API and/or aggregated from per-ship stats).
- **Display:**
  - Expose as a float with two decimal places (e.g., 1.23).
  - Show `—` if not available.
- **Surfaces:**
  - Add to player summary API and Player Explorer row.
  - Add to Player Explorer table in the UI.

## Acceptance Criteria

- [ ] Player-level KR is defined and documented in the codebase.
- [ ] KR is computed and stored/derived for each player.
- [ ] KR is exposed in the player summary and explorer API responses.
- [ ] Player Explorer table displays KR for each player.
- [ ] Documentation and tooltips clarify the meaning of KR.

## Open Questions

- Should KR be calculated as frags per battle, or frags per death? (Default: per battle, for consistency with per-ship KDR.)
- Is there a need to handle edge cases (e.g., zero battles)?

## Next Steps

- Engineering to implement aggregation and API exposure.
- QA to validate correctness and UI display.
