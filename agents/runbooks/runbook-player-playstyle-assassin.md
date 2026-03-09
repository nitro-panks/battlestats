# Runbook: Player Playstyle Taxonomy

## Goal

Maintain the player playstyle taxonomy so verdict labels map cleanly onto meaningful WoWS performance bands and remain consistent across API refreshes, crawler saves, and the player detail UI.

## Threshold Rationale

- WoWS player color bands in this repo already treat `>= 60%` win rate as the start of the purple/unicum tier, so `Assassin` starts there.
- `Warrior` now covers the stronger blue band at `56%` to `<60%` with stable survival.
- `Stalwart` covers the merely-good but dependable band at `52%` to `<56%` with stable survival.
- `Daredevil` remains the aggressive low-survival mirror for both the `Stalwart` and `Warrior` skill bands.
- `Hot Potato` starts below `42%` win rate with poor survival, making it a rarer worst-of-the-worst shelf rather than swallowing too much of the ordinary low-red population.
- `Potato` remains the low-survival bucket for `45%` to `<48%`, while `Survivor` remains the lower-win-rate but higher-survival branch.

## Current Thresholds

1. `< 100 battles` -> `Recruit`
2. `>= 60% WR` -> `Assassin`
3. `56% to < 60% WR` -> `Warrior` or `Daredevil` depending on survival
4. `52% to < 56% WR` -> `Stalwart` or `Daredevil` depending on survival
5. `48% to < 52% WR` -> `Flotsam` or `Jetsam` depending on survival
6. `42% to < 48% WR` -> `Survivor` or `Potato` depending on survival
7. `< 42% WR` -> `Survivor` or `Hot Potato` depending on survival

Low-survival split:

- `pvp_survival_rate < 33.0` is treated as the aggressive/fragile branch.

## Code Changes

1. Centralize verdict calculation in `warships.data.compute_player_verdict()`.
2. Use that helper from both:
   - `warships.data.update_player_data()`
   - `warships.clan_crawl.save_player()`
3. Recalculate stored verdict rows with the management command:
   - `python manage.py backfill_player_verdicts --changed-only --batch-size 2000`
4. Keep the UI label as `Playstyle` while preserving the `verdict` field name in the API/model.
5. Keep helper text in sync in `client/app/components/PlayerDetail.tsx`.

## Execution Steps

1. Apply the code changes.
2. Run targeted backend tests covering verdict assignment.
3. Run the verdict backfill command against the live dataset.
4. Spot-check grouped verdict counts after backfill.
5. Commit and push.

## Validation

- Targeted tests:
  - `python manage.py test warships.tests.test_data.PlayerDataHardeningTests warships.tests.test_data.PlayerExplorerSummaryTests`
- Optional UI sanity:
  - open representative players and confirm the player detail section renders the expected verdict and helper copy.
- Data sanity:
  - query grouped verdict counts after backfill.

## Rollback

1. Revert the commit.
2. Re-run the verdict backfill command after the revert if you need stored verdicts recalculated back to the old scheme.