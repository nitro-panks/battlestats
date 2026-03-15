# Runbook: Ranked WR vs Battles Heatmap Granularity

_Last updated: 2026-03-15_

## Purpose

Document how the ranked battles vs. win rate heatmap granularity is tuned, why the buckets were tightened, and how to validate future changes.

## Background

The first ranked heatmap shipped with broad log-scale x-axis buckets and `1.5` point win-rate bands on the y-axis.

On 2026-03-15, x-axis granularity was tightened from doubling bins to geometric bins using a growth factor of `sqrt(2)`, which split each octave in half.

This follow-up change tightens the chart again in both dimensions:

1. x-axis growth factor changes from `sqrt(2)` to `2^(1/4)`, which roughly doubles the number of total-games buckets again while keeping the log-scale shape.
2. y-axis win-rate bands change from `1.5` to `0.75`, which roughly doubles vertical precision.

## Current Settings

- Metric: `ranked_wr_battles`
- Minimum ranked battles: `50`
- X-axis scale: logarithmic
- X-axis base edge: `50`
- X-axis growth factor: `2^(1/4)` (`~1.1892`)
- Major x-axis ticks: `50, 100, 200, 400, ...`
- Early x-axis bucket edges: `50, 59, 71, 84, 100, 119, 141, 168, 200`
- Y-axis range: `35.0` to `75.0`
- Y-axis bin width: `0.75`
- Cache version: `ranked_wr_battles:v6`

## Why This Granularity

The ranked population is sparse at low battle counts and stretches quickly at higher totals. A log x-axis remains the right shape, but broader bins blur too many nearby players together.

The tighter y-axis helps distinguish nearby win-rate pockets, especially in the middle of the field where many ranked players cluster between roughly `50%` and `60%`.

## Trace Dashboard Expectation

The `/trace` page should surface this chart tuning as a learning artifact, not just workflow logs. That makes the tuning history visible alongside the diagnostics for recent agent work.

Expected trace-dashboard fields:

- runbook path
- current x-axis growth factor
- current y-axis bin width
- example early x-axis bins
- cache version

## Validation

1. Run: `cd server && DB_ENGINE=sqlite3 DJANGO_SETTINGS_MODULE=battlestats.settings DJANGO_SECRET_KEY=test-secret PYTHONPATH=$PWD /home/august/code/archive/battlestats/.venv/bin/python -m pytest warships/tests/test_views.py -q -k ranked_wr_battles`
2. Confirm the payload still reports major x ticks at `50` and `100`.
3. Confirm occupied tiles now land in tighter x ranges such as `59-71` and `119-141` for the seeded fixture.
4. Confirm occupied tiles now land in tighter y ranges such as `56.0-56.75` and `59.75-60.5` for the seeded fixture.
5. Open `/trace` and confirm the learning section shows the ranked heatmap tuning card and points to this runbook path.

## Rollback

If the chart becomes too visually noisy:

1. change `x_bin_growth_factor` back to `math.sqrt(2)`,
2. change `y_bin_width` back to `1.5`,
3. bump the ranked correlation cache version,
4. update the trace-dashboard learning card to match.
