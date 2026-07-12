# Runbook: Hidden Profile → Tier-Type Chart Warms Forever (Fix)

**Status:** Active
**Shipped:** 2026-07-11 (branch `fix/hidden-profile-chart`)
**Affected surface:** Player detail → Profile tab (`/player/<name>`)
**Observed player:** `castorice_my_beloved` (`player_id=1040379275`, na)

---

## Symptom

The Profile tab shows, indefinitely:

> Profile charts are still warming. Try again in a moment.

Reloading repeats the cycle; the "Tier vs Type Profile" chart never renders. The
same player's page still shows rich lifetime stats and reports `is_hidden: false`.

## Root cause

The player **hid their WoWS profile**. That single event drives both the stuck
chart and a stale `is_hidden`:

1. A hidden account returns no ship data from WG `ships/stats/` (`status: ok`,
   `meta.hidden: [id]`, empty `data`).
2. `update_battle_data` (`data.py`) treated "no ship data" as "record empty to
   avoid re-selection" and overwrote `battles_json = []` (stamping
   `battles_updated_at = now()`).
3. `fetch_player_tier_type_correlation` (`data.py`) and the view
   `player_correlation_distribution` (`views.py`) keyed "still warming" on
   `not player.battles_json`. That predicate **cannot distinguish**
   `battles_json is None` (never fetched → legitimately warming) from `== []`
   (fetched, came back empty → will never repopulate) — both are falsy.
4. So the endpoint set `X-Tier-Type-Pending: true` with empty `player_cells`
   forever, and each Profile-tab poll re-dispatched `update_battle_data_task`,
   which re-hit the hidden wall and rewrote `battles_json = []`. A closed loop.

`is_hidden` stayed `false` because it is only set from `account/info`'s
`hidden_profile` (`data.py`), and no account/info refresh had run since the hide.

## Fix (two levers)

### 1. Discriminator — pending gates on `battles_json is None` only

- `fetch_player_tier_type_correlation` (`data.py`): the refresh-dispatch +
  empty-`player_cells` branch now triggers on `player.battles_json is None`. An
  empty `[]` falls through to `_build_tier_type_player_cells([])` → `[]`, a
  **terminal** state (no re-dispatch).
- `player_correlation_distribution` (`views.py`): sets `X-Tier-Type-Pending`
  only when `is_population_pending or (player.battles_json is None and not
  player_cells)`.

Net: a hidden / no-ships player gets a settled `200` with empty `player_cells`
and **no** pending header, so the client renders the population heatmap without a
player overlay instead of spinning. A genuinely-cold new player
(`battles_json is None`) still warms + polls exactly as before.

### 2. `is_hidden` flip on the reliable WG signal (transient-safe)

- New `_fetch_ship_stats_for_player_with_hidden` (`api/ships.py`) returns
  `(ships_dict, is_hidden)` with a **tri-state** `ships_dict` that separates the
  three ways a fetch can come back without ships:
  - `None` → transient/transport failure (request did not complete).
  - `{}` → a completed fetch with no ships: hidden profile (`is_hidden=True`,
    from the response `meta.hidden` list) **or** a visible account with zero
    ships (`is_hidden=False`).
  - non-empty → the ships payload.
  A transient failure is **never** reported as hidden, so a WG blip cannot hide
  a visible player.
- `update_battle_data` (`data.py`) branches on that:
  - `ship_data is None` (transient) → **return without touching the row** — no
    `battles_json = []` clobber, no `is_hidden` flip. The player stays eligible
    for retry by the floor / next view, so the chart still self-heals (this
    preserves the pre-change behavior for transient WG errors instead of
    dropping the chart to a terminal empty).
  - `not ship_data` (definitive empty) → record `battles_json = []`; if
    `meta.hidden` named the account, also flip `Player.is_hidden = True` (scoped
    `update_fields`) so the whole profile reflects the hide.
  The account/info path remains the other (independent) writer of `is_hidden`.

`_fetch_ship_stats_for_player` is left untouched — the observation-floor /
enrichment callers keep their exact `None`/`{}` empty semantics.

## Tests

`warships/tests/test_hidden_profile_chart.py`:
- `_fetch_ship_stats_for_player_with_hidden` reports hidden from `meta.hidden`,
  reports **not** hidden on a transient (`None`) response, and returns ships for
  a visible account.
- `update_battle_data` flips `is_hidden` on the hidden signal, records a
  terminal `[]` without flipping on a definitive-empty visible account, and
  leaves `battles_json` **unchanged** on a transient (`None`) failure.
- The tier-type endpoint: `battles_json is None` → pending + dispatch;
  `battles_json == []` → terminal, no pending, no dispatch.

`test_incremental_battles.py::UpdateBattleDataCaptureHookTests` was updated to
mock the new `_fetch_ship_stats_for_player_with_hidden`.

## Ops note

An already-affected player self-heals once `update_battle_data` runs again (via a
page view or the observation floor): the flip sets `is_hidden`, and the chart
stops spinning immediately regardless (the discriminator makes `[]` terminal).
No backfill required.

Distinct from the archived `archive/runbook-profile-chart-warming-stuck.md`,
whose root cause is a **never-populated** `battles_json is None` player whose
empty fetch left `battles_updated_at` null and re-dispatched forever. That is the
`is None` branch this fix explicitly keeps warming; the bug here is the sibling
`battles_json == []` (clobbered-empty) case those older bugs did not cover.
