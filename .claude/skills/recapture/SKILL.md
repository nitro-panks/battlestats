---
name: recapture
description: Read the latest lapsed-player recapture sweep results from the production droplet and give a per-realm yield readout — how many dormant ("gone") players the cheap bulk account/info sweep found have actually returned, split by whether they re-entered the active-7d floor scope (harvested free) and whether they're clanless (the marginal value nothing else recovers). Use when the user says "/recapture", "recapture readout", "how's recapture", "are returning players being found", "lapsed player yield", or asks how the dormant-player recapture sweep is doing. Read-only — never writes, never restarts anything.
---

# recapture

Reads the durable per-run JSON yield snapshots that `recapture_lapsed_players`
writes at the end of each run (`<RECAPTURE_BENCHMARK_DIR>/YYYY-MM-DD_HHMMZ_<realm>.json`,
default dir `/opt/battlestats-server/shared/benchmarks/recapture-lapsed`, sibling
of the crawl-yield / observation-floor benchmarks), and renders a per-realm yield
readout answering: is the daily dormant-pool sweep actually finding returning
players, and how many re-enter floor scope for free? (Files, not the worker
journal: the background worker suppresses module-logger INFO, so a logged summary
line never lands — same reason `/observation` and `/crawl-yield` read files.)

Background: the observation floor only sees active-7d players, so a player who's
been quiet longer is never re-checked and a returner stays invisible to battle
capture until a profile view or clan crawl. The recapture sweep
(`recapture_lapsed_players_task`, per-realm Beat ~10:10/10:30/10:50 UTC) cheaply
re-checks the dormant pool via bulk `account/info`; when a player's
`last_battle_time` has advanced back inside active-7d it rewrites
`last_battle_date` so the existing floor harvests them next cycle. Full context:
`agents/runbooks/runbook-recapture-lapsed-players-2026-06-26.md`.

**Scope.** This measures the **recapture sweep**, not the floor or the crawl. For
floor coverage/freshness use `/observation`; for the clan crawl's discovery /
dormant→active yield use `/crawl-yield` (the crawl is the *other* dormant→active
instrument, scoped to clan members). This skill reads the *last completed run*, not
live worker health.

## When to invoke

- "/recapture", "recapture readout", "how's recapture", "recapture yield"
- "are returning players being found", "lapsed player yield", "did the sweep find anyone"
- After flipping `RECAPTURE_LAPSED_APPLY` or changing the band/limit, to confirm yield

Do **not** invoke for: floor coverage (`/observation`), clan-crawl yield
(`/crawl-yield`), or live worker health (`enrichment-status` / `event-check`).

## How to read it

Pull the latest snapshot per realm plus the live config (so you can tell
apply-mode from detect-only):

```bash
ssh root@battlestats.online '
DIR=/opt/battlestats-server/shared/benchmarks/recapture-lapsed
echo "=== latest snapshot per realm ===";
for r in na eu asia; do
  f=$(ls -1t "$DIR"/*_"$r".json 2>/dev/null | head -1);
  [ -n "$f" ] && { echo "--- $r ($(basename "$f")) ---"; cat "$f"; } || echo "--- $r: (no run yet) ---";
done
echo "=== config (env) ===";
grep -E "^RECAPTURE_LAPSED_" /etc/battlestats-server.env || echo "(no RECAPTURE_LAPSED_* set)";
'
```

If a realm shows "(no run yet)": either the Beat hasn't fired yet (it runs
~10:10/10:30/10:50 UTC) or `RECAPTURE_LAPSED_ENABLED` is not `1` (gated off — say
so). A manual kick is `recapture_lapsed_players_task.delay(realm="eu")` from a
server-venv `manage.py shell` (or `manage.py recapture_lapsed_players --realm eu
--limit 5000` for a one-off detect-only sample). Snapshots are timestamped and
kept, so a realm's `ls -1t … | head` is "the last run"; older files are history.

## The snapshot fields

Each JSON snapshot carries: `realm`, `mode` (`apply` writes + rotates; `detect`
measures only), `band_days`, `scanned`, `wg_calls`, `cursor_stamped`, and the
yield breakdown:

- **`advanced`** — players whose WG `last_battle_time` moved past our stored value
  = genuine new activity since we last knew. This is the headline "returners
  found." `advanced / scanned` is the yield rate.
- **`into7d`** — of those, how many landed back **inside active-7d**. These are
  promoted into floor scope and **harvested for free** on the next floor cycle —
  the whole point.
- **`into7d_clanless`** — the subset with no clan. **This is the marginal value**:
  returners the clan crawl structurally can't recover (it only walks clan
  rosters). A profile view is the only other way they'd have been found.
- **`still_lapsed`** — advanced but still outside active-7d (e.g. played once at
  day 200→day 120). Their displayed idle is corrected but the floor won't harvest
  them.
- **`still_dormant`** — checked, no new battles since our stored value (the bulk
  of any healthy sweep). `hidden` / `no_data` / `errors` are the non-productive
  remainder.

## Readout shape

Present a compact per-realm table (realm · mode · scanned · advanced (yield%) ·
into7d · into7d_clanless · still_lapsed), then 2–4 sentences of interpretation:

- Lead with the **into7d_clanless** count across realms — that's the returners
  *only* this sweep recovers; it's the number that justifies the feature.
- Note the **yield rate** (advanced/scanned) and whether it's worth the cadence;
  a healthy dormant pool is mostly `still_dormant`, so low single-digit % yield is
  expected and fine — the question is absolute returner count, not the rate.
- Flag anomalies: `mode=detect` (writes are off — returners are being *measured*
  not *recaptured*, flip `RECAPTURE_LAPSED_APPLY=1`); high `errors`/`no_data`
  (WG trouble); `scanned` much smaller than the band (cursor exhausted the pool
  → it's in maintenance mode, which is the steady state).

End with the live config line: `ENABLED=<0/1> APPLY=<0/1> band=<min-max>d
limit=<n>`, and whether the sweep is doing real work or just measuring.

Read-only: never edit env, restart workers, or dispatch a run unless the user
explicitly asks for a manual kick.
