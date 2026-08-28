# Ship characteristics from the game client (GameParams extract)

**Date:** 2026-08-28
**Status:** design approved, not implemented
**Surface:** `/ship/[shipSlug]` (characteristics block); `Ship` model; new offline extraction step
**Retires:** the `vortex.worldofwarships.com` HTTP dependency in `populate_shiptool_codes`
**Tooling dependency:** [`landaire/wows-toolkit`](https://github.com/landaire/wows-toolkit) `wowsunpack`, used as an opaque CLI

## Why

`/ship/[shipSlug]` is a ship page with no ship data on it. It shows identity
(name, tier, class, nation, premium) and a leaderboard, and then `ShipToolLink`
sends the visitor **off-site to shiptool.st** for the numbers they actually came
for. The WG public API does not expose those numbers, so this is not an oversight;
it is a structural gap in the data source.

The game client does expose them. `GameParams.data` is the authoritative ship
parameter database, and `wowsunpack` reads it directly. The same extract also
fixes two smaller problems: `Ship.is_premium` is a bare boolean standing in for a
distinction GameParams models properly, and `populate_shiptool_codes` currently
reaches an undocumented WG internal endpoint to recover a string the extract
already carries.

## What was verified (2026-08-27, build 13015811)

Evidence, not assumption. All figures measured locally against
`/mnt/d/World_of_Warships`.

- `wowsunpack game-params` completes in **7.2s**, producing 372 MB of pretty JSON
  (a `--min` CBOR mode yields 64 MB / 16,247 params; neither is the artifact).
- **The join is direct.** WG's public `ship_id` **is** the GameParams `id`.
  Checked both ends: Vortex key `3761157488` returns `PUSC509_Hector`, and that
  ship's GameParams object carries `"id": 3761157488` with `"index": "PUSC509"`.
  This is the fact the whole design rests on; it means no bridge table and no
  mapping service.
- A ship object carries 59 top-level keys, including `level`, `group`,
  `subnation`, `isPaperShip`, `archetype`, `RarityCategory`, `typeinfo`, the
  `A_*` module trees, `ShipUpgradeInfo` and `ShipAbilities`.
- **Hull variants are real:** 1116 ships have `A_Hull`, 345 `B_Hull`, 11 `C_Hull`,
  3 each of `D/E/F`. Roughly a third of ships have more than one hull, so "which
  hull" is a design decision, not a detail.
- Vortex returns 1038 vehicles in 19.7 MB per call.

## Non-goals

- **No armor, no ballistics.** `wowsunpack` can export `.geometry` armor models
  and simulate trajectories. That is a separate project: model parsing, GLB
  export, a physics sim, and a real frontend build. Explicitly out.
- **No replays.** `replayshark` parses `.wowsreplay` into 24 fully-detailed
  player records per file (measured: 30 replays yielded 577 distinct accounts,
  roughly 19x fan-out, with per-battle `potential_damage`, `spotting_damage`,
  builds, and a who-damaged-whom matrix keyed by account id). Considered on
  2026-08-27 and **set aside**: it needs an upload surface battlestats does not
  have, and uploaded replays are a biased sample in a way the observation floor
  is not. Recorded here so the capability is not rediscovered from scratch.
- **`ShipToolLink` stays.** This makes the ship page self-sufficient for the
  common numbers. It does not try to replace a dedicated build tool.
- **No stock/top hull toggle.** Deferred frontend scope; see Hull convention.

## Shape

Three stages, separated by a hard trust boundary. **Extraction cannot run in
production**: the droplet has no game install and never will (`res_packages`
alone is on the order of 100 GB).

### 1. Offline, on a machine with the game, on patch day

```
wowsunpack -g <game dir> game-params /tmp/gp.json     # ~7s, 372 MB
python server/scripts/extract_game_params.py \
    --input /tmp/gp.json \
    --output server/warships/data/gameparams.json
```

`wowsunpack` is invoked as an opaque binary. **No Rust enters the battlestats
build, test, or deploy path**, and no battlestats logic lives in the vendored
repo. `extract_game_params.py` is pure transformation: it owns the narrowing,
the hull resolution, and the artifact schema.

### 2. In git

The artifact is a **single file overwritten each patch**, at
`server/warships/data/gameparams.json`, not a per-build filename. The point is a
readable `git diff` showing exactly which ships WG changed. Estimated ~2 MB for
~1038 ships.

It is committed rather than left to ride the deploy's working-tree rsync. The
rsync would work (see `reference_deploy_ships_working_tree`), and that is exactly
the hazard: an uncommitted artifact is invisible to review, absent from a fresh
clone, and silently divergent between machines.

**This is game content, not player data.** The "never commit WG data" rule
concerns player PII (GDPR zips, CSVs). GameParams is neither, so that rule does
not apply here. Stated explicitly so it is not relitigated.

### 3. In production

`python manage.py import_game_params [--dry-run]`, modelled on
`populate_shiptool_codes`: idempotent, safe to re-run, reports what changed. The
droplet never sees a game file.

## Retiring Vortex

`populate_shiptool_codes` fetches 19.7 MB from
`https://vortex.worldofwarships.com/api/encyclopedia/en/vehicles/` for one
purpose: recovering the GameParams `index` string, from which `shiptool_code` is
derived by `createShortIndex`.

The artifact carries `index` directly. So the command **folds into**
`import_game_params` and the HTTP call is **deleted, not replaced**. The
`derive_shiptool_code` transform and its regex are kept verbatim; only the source
changes. `populate_shiptool_codes.py` is removed once the fold is verified.

This retires an undocumented WG internal endpoint that can change or vanish
without notice, which is a reliability win independent of the ship-page feature.

## Schema

Thirteen promoted scalar columns plus one JSONB column on `Ship`.

Promotion criterion: **filter, sort, or headline**. Everything else goes to JSONB.

| Column | Type | GameParams source | Hector (PUSC509) |
|---|---|---|---|
| `hull_hp` | int, null | `<top hull>.health` | 34400 |
| `max_speed` | float, null | `<top hull>.maxSpeed` | 34.0 |
| `turning_radius` | int, null | `<top hull>.turningRadius` | 760 |
| `rudder_shift` | float, null | `<top hull>.rudderTime` | 11.74 |
| `concealment_sea` | float, null | `<top hull>.visibilityFactor` | 11.58 |
| `concealment_air` | float, null | `<top hull>.visibilityFactorByPlane` | 7.41 |
| `concealment_fire_smoke` | float, null | `<top hull>.visibilityCoefGKInSmoke` | 5.34 |
| `archetype` | char, blank | `archetype` | `CA_Controller` |
| `rarity` | char, blank | `RarityCategory.name` | `Rare` |
| `param_group` | char, blank | `group` | `special` |
| `is_paper_ship` | bool | `isPaperShip` | true |
| `subnation` | char, blank | `subnation` | `Australia` |
| `game_build` | int, null | provenance | 13015811 |

Plus `params_json` (JSONB, null): module trees, per-gun and torpedo detail, AA,
consumable slots, and the upgrade graph. And `params_extracted_at` (datetime,
null) for provenance.

**Why JSONB for the tail.** This is the `ships_stats_json`-alongside-scalars shape
already used by `BattleObservation`, so it reads as native. It also means a WG
reshuffle of the module tree costs an artifact regeneration rather than a
migration, which matters because GameParams reshapes itself on patches.

Every column is nullable. A ship absent from the artifact, or a field WG removes,
leaves NULL. **No sentinel zeros**: a `0.0` concealment is a claim about the game,
and a missing value must not be able to make one.

### The identity fix

`is_premium` **stays as a column** for backward compatibility (serializers, the
client, `shipIdentity.ts` all read it), but stops being independently sourced. It
becomes a derived function of `param_group` and `RarityCategory`, which is where
the real distinction lives. `rarity`, `param_group`, `is_paper_ship` and
`subnation` are additive and break nothing.

## Hull convention

For the ~345 multi-hull ships, characteristics are taken from the **top hull**,
resolved through the `ShipUpgradeInfo` upgrade graph rather than by taking the
highest letter present.

Rationale: stock-hull numbers misrepresent nearly every tech-tree ship, and the
top hull is what the players on the site's own leaderboards are actually sailing.
That keeps the page consistent with the "our world is the last 30 days" thesis
(`project_30d_window_worldview`), where surfaces describe current form rather
than a theoretical configuration.

The letter heuristic is a fallback only, used when the upgrade graph does not
resolve, and it must log when it fires rather than resolve silently.

## Staleness

The failure mode is not a bad extract; it is a correct extract that quietly ages.
This project's recurring pain is exactly that shape: Flower blind for a month
while reporting healthy (`project_flower_blind_month_2026-07-27`), gunicorn
timeouts silently reverting three times, the ops digest counting lock-skips as
successes. A patch-day ritual with no alarm repeats the bet.

So:

1. The artifact records `game_build` (13015811), the semver (`15.7.0`) and
   `extracted_at`. `import_game_params` writes them onto every row it touches.
2. A check joins the **exception-only ops digest**
   (`runbook-ops-email-exception-only-2026-08-09.md`): fetch the live version
   from `encyclopedia/info/` and compare against the stored semver. Raise **only**
   on mismatch, so the digest stays silent when things are fine.

`encyclopedia/info/` is already exercised from the droplet with `WG_APP_ID` by
`server/scripts/smoke_test_wg_api.py`, so this is a proven call, not a new
dependency. **Open item:** confirm the exact response field name for the version
against a live call during implementation rather than trusting recalled schema.

This gives the ritual the thing `populate_shiptool_codes` lacks: the system says
it needs running, instead of a human having to remember.

## Testing

**Extractor** (pure transformation, so fixture-driven): a checked-in raw
GameParams subtree for deliberately awkward ships, and an expected artifact.
Chosen cases: Hector (premium, paper ship, single hull), Moskva (tech-tree,
multi-hull), one carrier and one submarine (hull structures that do not look like
a cruiser's).

**Loader** (pytest, existing sqlite harness, `DB_ENGINE=sqlite3 --nomigrations`):

- idempotency: a second run changes nothing
- a ship in the artifact but absent from the DB
- a ship in the DB but absent from the artifact
- a field WG has removed leaves NULL and does not become `0`

That last case is the one that matters. The `unwrap_or_default` instinct is
precisely what turns missing data into a plausible zero, and a plausible zero on
a concealment value is worse than a blank.

**Explicit non-goal:** no test asserts a specific value of any ship's HP. That
would test WG's balance decisions rather than our code, and would fail on a
balance patch for no good reason.

## Rollout

1. Migration: thirteen columns + `params_json` + `params_extracted_at`.
2. Generate the artifact; commit it.
3. `import_game_params --dry-run` against prod, inspect, then run for real.
4. Fold `shiptool_code` derivation in; delete the Vortex fetch; remove
   `populate_shiptool_codes.py`.
5. Add the staleness check to the ops digest.
6. Frontend: characteristics block on `/ship/[shipSlug]`. The only frontend work
   in scope.
7. Version bump, then **rebuild and redeploy the client** even though this is
   mostly backend: `NEXT_PUBLIC_APP_VERSION` is captured at build time, so a bump
   without a client deploy leaves the production footer stale. Non-negotiable per
   `CLAUDE.md`.

## Patch-day procedure (durable)

On a WoWS patch that changes ships:

```bash
wowsunpack -g <game dir> game-params /tmp/gp.json
python server/scripts/extract_game_params.py --input /tmp/gp.json \
    --output server/warships/data/gameparams.json
git diff server/warships/data/gameparams.json      # review what WG changed
# commit, deploy, then on the droplet:
python manage.py import_game_params --dry-run
python manage.py import_game_params
```

This **replaces** the existing `populate_shiptool_codes` patch-day step rather
than adding a second one.

## Open questions

1. The exact `encyclopedia/info/` field carrying the game version. Confirm live.
2. Whether `ShipUpgradeInfo` resolves the top hull cleanly for every multi-hull
   ship, or whether the letter fallback is needed in practice. Measure across all
   345 during implementation; the answer decides how loud the fallback log is.
3. Final artifact size once narrowing is written. ~2 MB is an estimate; if it
   lands materially above that, revisit what is in `params_json`.
