# Runbook — Ship Tool (shiptool.st) deep-link integration (2026-06-22)

**Status:** dated-active · **Kind:** runbook · **Section:** feature · **Owner:** product

Each ship surface now links out to its parameters page on
[Ship Tool](https://shiptool.st) — a black-gear logo chip next to the ship name on
the inline `ShipLeaderboard` drilldown and the `/ship/<id>` `ShipRouteView` masthead.
The link target is `https://shiptool.st/params?S=<code>`, e.g. Moskva →
`https://shiptool.st/params?S=RC110`.

## The key insight: the code is derived, not scraped

`RC110` is **computed**, not looked up. Ship Tool addresses a ship in its URL by a
short index derived from the WoWS **GameParams index** string. Its own bundle
(`createShortIndex`) does:

```js
index.match(/P([A-Z])S([A-Z])0*([0-9]+)$/)  // groups: nation, type, number
// joined -> short code. Leading zeros on the number are stripped.
```

So `PRSC110` → `RC110`, `PJSB018` (Yamato) → `JB18`, `PBSC111` (Edgar, T11) → `BC111`.
Verified end-to-end against the live site (rendered `params?S=JB18` → Yamato,
`params?S=JB510` → Shikishima). There is **no per-link scraping** and **no runtime
call to shiptool.st** — only a one-time, offline data build.

## Where the GameParams index comes from

The WG **public** API does **not** expose the index. WG's **Vortex** encyclopedia
does, keyed by the *same numeric ship_id we already store*:

```
GET https://vortex.worldofwarships.com/api/encyclopedia/en/vehicles/
  "4179539408": { "name": "PRSC110_Pr_66_Moskva", "level": 10, ... }
```

The index is the `name` prefix before the first `_` (`PRSC110`). All ~1025 vehicles
in the catalog conform to the regex (100% coverage at build time). Vortex is
WG-official and kept current across patches.

## Data flow

1. `populate_shiptool_codes` (management command) fetches the Vortex catalog,
   joins on `Ship.ship_id`, derives the short code via `derive_shiptool_code`
   (mirrors `createShortIndex`), and persists it to `Ship.shiptool_code`.
   Idempotent; only fills/updates, never clobbers an existing code on a transient
   catalog gap. Ships absent from Vortex / non-conforming keep `''`.
2. `get_ship_leaderboard` (data.py) emits `ship.shiptool_code` (or `null`) in the
   payload that powers **both** surfaces.
3. `ShipToolLink.tsx` renders the logo-chip link when a code is present, and
   nothing when absent (clean degradation). Fires the `shiptool-click` Umami event.

## Operating it

- **Refresh on a WoWS patch that adds ships:**
  `cd server && python manage.py populate_shiptool_codes` (add `--dry-run` to preview).
  New ships acquire a code on the next run; until then their link is simply hidden.
- Not yet wired to Celery Beat — it's a manual/per-patch op. If churn warrants it,
  a weekly Beat task is the obvious next step.
- The brand logo lives at `client/public/shiptool-logo.png` (Ship Tool's
  `logo192.png`). It's a black gear on transparent, so `ShipToolLink` sits it on an
  always-light chip to stay legible in dark theme.

## Deploy sequencing (first rollout)

Backend **before** frontend, and run the populate after migrate:

1. Deploy backend (rsyncs working tree → `migrate` applies `0077_ship_shiptool_code`).
2. `python manage.py populate_shiptool_codes` on the droplet to fill codes.
3. Deploy frontend (rebuild ships the new components + logo asset).
4. Verify: load a `/ship/<id>` for a current ship (e.g. Moskva) → the chip appears
   and links to `params?S=RC110`.

## Gotchas

- **Leading zeros are stripped** by `0*` — `PJSB018` → `JB18`, not `JB018`. This
  round-trips correctly on shiptool.st (verified). Don't "fix" it.
- Some ship_ids have multiple variants in Vortex (e.g. two Moskvas: `PRSC110`
  played, `PRSC910` an old clone). We join on the exact `ship_id` we store. Our
  real Moskva is `4179539408` → `RC110` (verified live).
- **Bracketed-name clones are suppressed.** WG marks removed/test clone ships
  with a bracketed name (`[Moskva]`, `[Yamato]`, … — 13 of them, all T10). Their
  ids *are* in Vortex (so they'd derive e.g. `RC910`), but Ship Tool has no such
  page (`params?S=RC910` renders nothing), so the command skips any `name`
  starting with `[`, leaving the link hidden. They have no leaderboard presence
  anyway.
- DB↔Vortex overlap was **100%** (1022/1022) at build time — no id-space drift,
  so the feature won't silently render nothing.

## Files

- `server/warships/models.py` — `Ship.shiptool_code` (migration `0077`)
- `server/warships/management/commands/populate_shiptool_codes.py`
- `server/warships/data.py` — `get_ship_leaderboard` payload field
- `client/app/components/ShipToolLink.tsx` (+ `client/public/shiptool-logo.png`)
- `client/app/components/ShipLeaderboard.tsx`, `ShipRouteView.tsx` — wiring
- Tests: `server/warships/tests/test_shiptool_codes.py`,
  `server/warships/tests/test_ship_badges.py` (payload field),
  `client/app/components/__tests__/ShipToolLink.test.tsx`
