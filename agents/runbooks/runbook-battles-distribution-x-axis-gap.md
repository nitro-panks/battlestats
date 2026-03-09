# Runbook: Battles Distribution X-Axis Gap

## Goal

Remove the apparent empty gap at the left side of the battles-played distribution charts.

## Observed Behavior

- The battles-played distribution visually appears to have no data until roughly `150` battles.
- Live payload inspection shows the first non-zero bucket is actually `100` to `200` battles.
- The backend is intentionally filtering the tracked population to players with at least `100` PvP battles.

## Root Cause

- The battles-played distribution uses a log x-scale.
- Each bucket is drawn at the geometric midpoint of the bucket rather than the left edge.
- For the first bucket, `100` to `200`, the plotted point is:

$$
\sqrt{100 \cdot 200} \approx 141.4
$$

- The axis domain still began at the raw bin edge `100`, so the chart showed a left-side span before the first visible point.
- This is a rendering-domain mismatch, not missing data.

## Plan

1. Keep the backend population filter and bucket definitions unchanged.
2. Change the frontend log-scale domain for population distributions to start at the first plotted midpoint rather than the first raw bin edge.
3. Filter log-scale tick values to the visible domain so off-domain ticks are not requested.
4. Validate the live payload and run a client production build.

## Files

- `client/app/components/PopulationDistributionSVG.tsx`

## Validation Steps

1. Inspect live payload:
   - `GET /api/fetch/player_distribution/battles_played/`
   - Confirm the first non-zero bucket starts at `100`.
2. Build the client:
   - `cd client && npm run build`
3. Open a player detail page and confirm the curve starts flush with the left boundary instead of leaving a visible dead zone.

## Notes

- If a specific player marker is below the tracked-population floor, the combined x-domain can still expand left to keep that player marker visible.
- The backend data floor remains meaningful: the population distribution is intentionally computed only for players with at least `100` PvP battles.