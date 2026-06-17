---
name: crawl-yield
description: Pull the per-pass clan-crawl yield-by-source snapshots from the production droplet and give a readout of the crawl's marginal value — net-new discovery + dormant→active re-detection (yield the observation floor structurally cannot produce) vs. floor overlap. Use when the user says "/crawl-yield", "crawl yield", "is the crawler still earning its cost", "crawl yield readout", or asks whether the daily clan crawl has saturated into mostly re-confirmation. Read-only — never writes, never restarts anything.
---

# crawl-yield

Reads the durable per-pass snapshots written by the clan crawl at pass
completion (`emit_crawl_yield_snapshot`, `server/warships/clan_crawl.py` →
`/opt/battlestats-server/shared/benchmarks/crawl-yield/YYYY-MM-DD_HHMMZ_<realm>.json`)
and renders a **per-realm yield/overlap readout** answering one question: is the
daily full clan re-walk still surfacing players the observation floor *can't*,
or has it saturated into mostly re-confirming a universe we already have?

**Scope — read this before interpreting anything.** This measures the **clan
crawl**, not the observation floor. The two are complementary instruments:

- The **observation floor** (`/observation`) walks players who are *already*
  active (`last_battle_date >= today-DAYS`) and records battle history. It is
  structurally **blind to discovery** — it can never touch a player who isn't
  already in the active-7d set.
- The **clan crawl** owns exactly the two floor-impossible jobs this skill
  measures: **net-new account-ID discovery** and **dormant→active
  re-detection** (bulk-refreshing `last_battle_date` for not-currently-active
  players so a returner re-enters active-7d *without* a profile view).

So this is the right instrument for "do we still need the crawler / is it wasted
cycles," and the **wrong** instrument for floor coverage/freshness (use
`/observation`), enrichment progress (query `enrichment_status`), or live worker
health (`enrichment-status`).

## When to invoke

- "/crawl-yield", "crawl yield", "crawl yield readout"
- "is the crawler still earning its cost", "has the crawl saturated", "are we wasting crawl cycles"
- After lengthening/shortening crawl cadence, to confirm yield held up

Do **not** invoke for: floor coverage/freshness (`/observation`), enrichment-pool
progress (`enrichment_status`), or live crawler/worker health
(`enrichment-status`). This skill reads *completed-pass snapshots*, not live state.

## The buckets (what each snapshot carries)

Each snapshot classifies every player the crawl saved that pass:

| bucket | meaning | counts toward |
|---|---|---|
| `discovered_active` | net-new account ID, currently active | **yield** (floor-impossible) |
| `reactivated` | known player, dormant→active *this* crawl write | **yield** (floor-impossible) |
| `discovered_dormant` | net-new account ID, dormant | universe growth (seed corn for future reactivations) |
| `refreshed_active` | known player, already active | **overlap** (the floor already covers them) |
| `still_dormant` | known player, stayed dormant | no active value this pass |

Derived: `yield_total = discovered_active + reactivated`; `overlap_total =
refreshed_active`; `yield_frac` / `overlap_frac` over `players_classified`.

## Procedure

### 1. Pull recent snapshots (+ active_7d context for the verdict)

One SSH call. Snapshots are per-realm, per-pass (~1 daily per realm once passes
complete). Also grab the latest observation-floor snapshot's `active_7d` — the
saturation verdict needs the active-7d trend, which lives there, not here:

```bash
ssh root@battlestats.online '
DIR=/opt/battlestats-server/shared/benchmarks/crawl-yield
echo "AVAILABLE=$(ls -1 "$DIR"/*.json 2>/dev/null | wc -l)"
for f in $(ls -1t "$DIR"/*.json 2>/dev/null | head -18); do
  echo "===== $(basename "$f") ====="
  cat "$f"
done
echo "===== latest observation-floor (for active_7d context) ====="
OF=/opt/battlestats-server/shared/benchmarks/observation-floor
ls -1t "$OF"/*.json 2>/dev/null | head -1 | xargs -r cat | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps({\"captured_at\":d.get(\"captured_at\"),\"active_7d\":{r:v.get(\"active_7d\") for r,v in d.get(\"realms\",{}).items()},\"total_active_7d\":d.get(\"totals\",{}).get(\"active_7d\")}))" 2>/dev/null
'
```

**If `AVAILABLE=0`:** there are no completed-pass snapshots yet. This is the
expected state until ~12–18h after the instrumentation deployed (a snapshot is
written only when a full clan-walk pass *completes* — partial/interrupted passes
keep accruing into Redis and emit nothing). Say so plainly, note the per-realm
crawl schedule (eu ~03:00, na ~09:00, asia ~22:00 UTC start; a pass runs many
hours), and stop. Do not invent a reading.

### 2. Select comparison points BY realm, then by `captured_at`

Snapshots are **per realm** — a file is one realm's one completed pass. Group by
realm first. For each realm pick:

- **L** = that realm's latest pass (`captured_at`).
- **prev** = that realm's prior pass, for a 2-point trend.

Realms complete passes on different days (striped schedule), so do **not** diff a
na pass against an eu pass — only same-realm passes are comparable.

### 3. Interpret — yield vs overlap, with the active_7d context

For each realm and the total, report `players_classified`, the five buckets,
`yield_total`/`yield_frac`, `overlap_total`/`overlap_frac`, and pair it with that
realm's `active_7d` from the observation-floor snapshot.

**The decision (and its two traps — both live in the runbook):**

- **flat `active_7d` + high `yield_frac`** → discovery is load-bearing, exactly
  offsetting churn. **Keep the crawl as-is.**
- **flat `active_7d` + low `yield_frac` AND low `discovered_dormant`** →
  saturated; the daily re-walk is mostly re-confirmation. **Candidate to
  lengthen cadence** and hand the freed DB-write headroom to the floor.
- **Trap 1 — don't trim on a low ratio alone.** A low `yield_frac` driven by
  **high `discovered_dormant`** means the universe is *still growing*; those
  dormant discoveries are the seed corn for *future* `reactivated` hits. Trimming
  discovery there starves the re-detection you're trying to protect. "Saturated"
  requires **both** `yield_frac` and `discovered_dormant` low.
- **Trap 2 — don't read cov/7d alone when testing crawl-off.** Killing
  re-detection *shrinks* `active_7d` (reactivations stay invisible until viewed),
  which makes `/observation`'s `coverage_ratio_vs_7d` look artificially *better*
  while real coverage worsens. Watch `active_7d` + reactivation count.

### 4. Report

```
Crawl yield-by-source — battlestats.online
Snapshots: <N> passes across na/eu/asia   |   active_7d ctx from obs-floor <captured_at>

  realm   pass (captured_at)   classified   disc_active  reactivated  disc_dormant   refreshed(overlap)  still_dormant   yield_frac   overlap_frac   active_7d
  na      …                    …            …            …            …              …                   …               …%           …%             …
  eu      …                    …            …            …            …              …                   …               …%           …%             …
  asia    …                    …            …            …            …              …                   …               …%           …%             …

Read: <per-realm one-liner — yield vs overlap, whether discovered_dormant keeps it
load-bearing, and what active_7d is doing. Verdict only if the pattern is clear
across ≥2 same-realm passes; otherwise "need N more clean passes.">
```

## Scope and limits

- **Read-only.** SSHes, cats JSON, interprets. Never writes, never restarts, never re-runs a crawl.
- Reports **completed-pass snapshots**, not live state. A pass mid-flight has accrued counts in Redis (`crawl:yield:<realm>:<marker>`) that haven't been emitted yet.
- **Verdict discipline.** Like the observation floor, per-pass counts vary (time-of-day, partial windows, clan-list churn). Don't declare "saturated, trim the crawl" off one pass — require the pattern to hold across **≥2–3 same-realm passes** with `active_7d` flat. A single low-yield pass is noise.
- **Clan crawl only.** Not floor coverage (`/observation`), not enrichment, not worker health.
- Background: `agents/runbooks/runbook-bulk-battle-observation-capture-2026-06-06.md` ("Benchmarks" → "Crawl yield-by-source instrumentation"). Kill switch: `CRAWL_YIELD_INSTRUMENT_ENABLED`.
