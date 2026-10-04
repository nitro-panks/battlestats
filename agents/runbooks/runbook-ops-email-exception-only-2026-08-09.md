# Runbook: ops email, exception-only alerting (2026-08-09)

**Status:** active
**Owner:** platform
**Subject:** `server/scripts/daily_ops_email.py` and its thresholds
**Supersedes:** the always-send daily digest behaviour of the same script (the script itself is amended, not replaced)

## What changed

The daily ops-digest email used to mail an LLM-synthesized digest of the
`observation-floor` / `crawl-yield` / `recapture-lapsed` benchmark snapshots
every single day, whether or not anything was wrong. It now mails only when a
**deterministic Python verdict** decides something is outside normal bounds.

Flow:

```
load snapshots  ->  evaluate() in Python  ->  no conditions ?  -> one stdout line, exit 0, NO mail
                                          ->  conditions     ?  -> LLM writes up ONLY those conditions, mail sent
```

The LLM is never the gate. It is invoked only to write up an alert Python has
already decided to send, and its system prompt forbids it from commenting on
anything outside `tripped_conditions`. An LLM gate would be
non-deterministically silent on the day it matters.

## Why the verdict must stay in Python

The script's original design comment already said Python selects the comparison
points "so the LLM never miscomputes a delta". The alert decision is the same
class of problem, one step further: a model asked "is this bad?" can answer "no"
on the one morning it should answer "yes", and nothing in the output would
reveal that it had. The thresholds below are named constants; a run either
crosses them or it does not.

## Keeping silence distinguishable from breakage

Exception-only mail has a structural hazard: the absence of mail becomes
ambiguous between "healthy" and "the whole reporting path is dead". Three
mechanisms close that, and none of them may be removed:

1. **The fail-loud path is untouched.** Any exception still mails
   `[battlestats] daily ops email FAILED` with a traceback, unconditionally,
   from the `__main__` guard. Exception-only applies to the DIGEST, not to the
   script's own errors. `test_main_guard_source_is_unconditional` asserts
   structurally that the guard never consults the verdict or a kill switch.
2. **Missing / stale / unreadable / mis-shaped snapshots are themselves alert
   conditions.** They are checked BEFORE any count is trusted.
3. **A weekly heartbeat** (`OPS_EMAIL_HEARTBEAT_DOW`, default `mon`) mails the
   deterministic table regardless of verdict, subject
   `[battlestats] ops heartbeat: all clear`. A script cannot detect its own
   non-execution: the timer dying, the unit being disabled and SMTP breaking all
   look exactly like a quiet healthy day. Only a periodic unconditional send
   proves the transport. Set the var to an empty string to disable, at the cost
   of that proof.
   **Corrected 2026-10-03:** until that date the code did not do what this item
   says. A clear-day heartbeat called the model with the digest prompt and only
   the subject was overwritten. `main()` now computes
   `heartbeat_only = beat and not alerting and not (always or forced)` and skips
   the Anthropic call when it is true, so the heartbeat is the deterministic
   table and the transport proof no longer depends on the API. An alert on the
   heartbeat day still goes out as an alert, and `--force` /
   `OPS_EMAIL_ALWAYS_SEND=1` still write the LLM digest. Record:
   `runbook-llm-token-optimization-2026-10-03.md`.

## Shape before numbers

The generalized lesson from the 2026-08-06 recapture truncation incident: a pass
cut short by the soft time limit carried real, durable counts and was
**numerically indistinguishable** from a healthy pass whose cursor had exhausted
the pool. The only signal was the `partial` field. So every snapshot's
status/partial/failed_buckets style fields are checked first, and a numeric
alert is never raised in place of the shape alert that explains it.

Two specifics worth keeping:

- **`partial is not False`, not `partial is True`.** Post-fix snapshots all carry
  an explicit `partial: false`. A *latest* snapshot that LACKS the field means
  the writer changed or an old code path resurfaced, i.e. truncation has become
  undetectable again. That fires `recapture_partial_field_absent:<realm>`.
- **Component sums are checked.** `still_dormant + advanced + hidden + no_data`
  must equal `scanned` (true on all 113 historical runs), and the crawl's five
  buckets must sum to `players_classified` (true on all 39 historical passes). A
  snapshot that does not describe itself cannot have its numbers trusted.

## Threshold derivation

Every number is derived from the observed historical distribution of the
snapshots on the production droplet
(`/opt/battlestats-server/shared/benchmarks`), read 2026-08-09. Corpora:

| family | files | span | cadence observed |
|---|---|---|---|
| `observation-floor` | 64 (62 daily + 2 off-cycle) | 2026-06-08 .. 2026-08-08 | daily 04:30 UTC, gap min 24.00h max 24.02h |
| `crawl-yield` | 39 passes | 2026-06-22 .. 2026-08-07 | per realm; pass gap med na 69.7h / eu 117.8h / asia 107.8h, max 147.2h |
| `recapture-lapsed` | 113 runs | 2026-06-26 .. 2026-08-08 | daily per realm 10:10 / 10:30 / 10:50 UTC |

**Regime boundary.** The observation-floor record has a hard break on
2026-06-20/21 when the bulk floor came online (cov/7d 0.069 -> 0.404 in two
days). Numeric thresholds are derived from the **current regime only**,
2026-06-23 .. 2026-08-08, n=47 daily snapshots. Using the full record would put
the floors absurdly low and the alert would never fire.

**Deltas are deliberately not used.** Within the current regime the worst clean
day-over-day move is `asia.distinct_productive` **-43%** (and -30% na, -26% eu).
Any delta rule tight enough to detect a real fault would cry regression
constantly, which is exactly what the `/observation` skill's verdict discipline
forbids. Absolute floors set outside the observed envelope are the honest
instrument.

**Two classes of check, and the difference matters when reading an alert:**

- **Tuned detectors** (shape + staleness). Backed by a real incident; expected to
  fire when something is genuinely wrong.
- **Catastrophe backstops** (the numeric floors/ceilings). Set well outside the
  observed envelope; none has ever fired on the historical record, and that is
  the point. If one fires, something large has broken.

### Staleness (measured at run time, 11:31 UTC, against `captured_at`)

Derived from an explicit backtest: for each of 43 days, "how old was the newest
snapshot at 11:31 UTC that day?"

| family | healthy age at 11:31 (observed) | worst healthy | threshold | class | fires historically |
|---|---|---|---|---|---|
| `observation-floor` | **7.0h on all 44 days** | 7.0h | **24h** (`OPS_ALERT_OBS_MAX_AGE_HOURS`) | tuned | 0 |
| `crawl-yield` (per realm) | med na 43.6h / eu 52.5h / asia 57.9h | **131.6h** (asia) | **168h = 7d** (`OPS_ALERT_CRAWL_MAX_AGE_HOURS`) | tuned | 0 |
| `recapture-lapsed` (per realm) | 0.7h .. 1.4h | 1.4h | **24h** (`OPS_ALERT_RECAPTURE_MAX_AGE_HOURS`) | tuned | 34 (all true positives, see below) |

**The recapture 24h rule is the single most valuable check here.** A single
missed daily run lands at 24.7h (asia) / 25.0h (eu) / 25.4h (na), so the
threshold has to sit below 24.7h to catch one skipped run. Backtested, it fires
on na 3 days, eu 12 days, asia 19 days. **Every one of those is a true
positive**: they are precisely the pre-fix truncation era in which EU and ASIA
silently lost entire passes. ASIA's real gap was **418h** (2026-07-20 ->
2026-08-06). This rule would have raised it on **2026-07-22** instead of it being
found by hand two weeks later.

Backtest against the two days that represent the fixed steady state (2026-08-07
and 2026-08-08): **zero conditions on both**.

> **Known margin, watch this.** The healthy margin on the recapture rule is only
> **40 to 80 minutes** (runs at 10:10/10:30/10:50, mail at 11:31). A sweep that
> starts after roughly 11:15 will false-fire, and the sweep coexists with clan
> crawls. This is deliberate: catching a single missed run is worth more than the
> margin. If it turns into a nag, raise `OPS_ALERT_RECAPTURE_MAX_AGE_HOURS` to
> 26 (which then needs two consecutive misses) rather than removing the check.

`crawl-yield` looks lax at 7 days only because completed passes legitimately are
days apart; 131.6h was a genuinely healthy age.

### Observation floor, TOTAL scope

Regime = 2026-06-23 .. 2026-08-08, n=47.

| metric | observed regime range | threshold | margin | class |
|---|---|---|---|---|
| `coverage_ratio_vs_7d` | 0.2441 (07-09) .. 0.3577 (08-03) | `< 0.18` | 26% under min | backstop |
| `distinct_productive` | 51,889 .. 74,632 | `< 38,000` | 27% under min | backstop |
| `active_7d` | 200,615 .. 221,054 | `< 150,000` or `> 300,000` | 25% / 36% outside | backstop |
| `active_1d` | 75,304 .. 99,632 | `< 40,000` | 47% under min | backstop |
| `productive_rate` | 0.8522 .. 0.9510 | `< 0.60` | 30% under min | backstop |
| `fresh_frac` | 0.2617 .. 0.3710 | `< 0.15` | 43% under min | backstop |
| `never_observed` | 14 .. 1,728 | `> 10,000` | 5.8x max | backstop |
| `obs_bulk_floor` | 68,713 .. 107,907 | `< 30,000` | 56% under min | backstop |
| `obs_poll` | 6,551 .. 15,835 | `> 60,000` | 3.8x max | backstop |
| `fresh_within_24h` | 55,637 .. 79,127 | **none** | derived = `fresh_frac x active_7d`; covered by `fresh_frac`, a second rule would only double-report | n/a |
| `stale_over_24h` | 131,104 .. 156,721 | **combined only:** `> 175,000` **AND** `distinct_productive < 45,000` | see below | backstop |

`stale_over_24h` gets no standalone rule on purpose. It is mostly the change-gate
**non-mover wall**: a player who did not battle is gate-skipped without a fresh
observation, so a large steady value is by design, not a backlog. The
`/observation` skill is explicit that only a rising stale **with** falling
`distinct_productive` means cadence is slipping. Worst historical pairing was
156,721 stale alongside 51,889 productive (2026-07-09) and the combined rule
correctly stays quiet on it.

### Observation floor, PER-REALM scope

| metric | lowest observed across realms | threshold | class |
|---|---|---|---|
| `coverage_ratio_vs_7d` | 0.1918 (asia, 07-15); na 0.2245, eu 0.2183 | `< 0.12` | backstop |
| `distinct_productive` | 13,142 (asia, 07-15); na 12,225, eu 19,924 | `< 8,000` | backstop |

The per-realm floors sit further below their minima than the totals because
per-realm day-over-day variance is much larger (asia -43% worst clean move).
Remaining per-realm fields inherit the TOTAL rules; duplicating all ten per realm
would triple the alert surface for no added detection.

### Crawl yield, per realm

| metric | observed range | threshold | class |
|---|---|---|---|
| `players_classified` | steady na 274.2k .. 275.9k; eu 471.7k .. 473.8k; asia 256.8k .. 260.8k | **per realm** (`thr_realm`): na `< 250,000`, eu `< 430,000`, asia `< 235,000`; global fallback `< 150,000` | tuned |
| `yield_total` (`discovered_active + reactivated`) | na 2,089 .. 7,234; eu 5,234 .. 25,970; asia 3,282 .. 11,150 | `< 200` | backstop |
| bucket sum vs `players_classified` | exact on 39/39 passes | mismatch alerts | tuned (shape) |
| `yield_frac` / `overlap_frac` / `discovered_dormant` / `refreshed_active` / `still_dormant` | na `yield_frac` as low as 0.0076; `discovered_dormant` as low as 0 | **none** | see below |

#### `players_classified` went per realm on 2026-08-11 (and this one CAN fire historically)

`players_classified` is a **per-realm catalog size**, not a rate, and the realms
differ by 1.8× (asia ~260k vs eu ~473k). A single global floor is therefore only
ever tight for the smallest realm: at `150,000` it tolerated a 45% coverage loss
on na and **68% on eu**. It let two genuinely partial passes through silently and
caught the third only by magnitude.

Each realm's healthy band is narrow — the within-realm spread is 0.45%–1.5% over
seven weeks — so floors sit at ~91% of each realm's observed steady-state minimum,
leaving 6–19× the observed variation as headroom while detecting a ~9% coverage
loss:

| realm | steady band (n) | floor | = % of min | env override |
|---|---|---|---|---|
| na | 274,188 .. 275,869 (18) | 250,000 | 91.2% | `OPS_ALERT_CRAWL_CLASSIFIED_MIN_NA` |
| eu | 471,664 .. 473,814 (9) | 430,000 | 91.2% | `OPS_ALERT_CRAWL_CLASSIFIED_MIN_EU` |
| asia | 256,847 .. 260,796 (12) | 235,000 | 91.5% | `OPS_ALERT_CRAWL_CLASSIFIED_MIN_ASIA` |

**This is the one rule that deliberately breaks the "never fired on the historical
record" invariant this file otherwise holds.** Backtested against the full
42-snapshot corpus: 39 quiet, 3 fire, and all 3 are passes that were not healthy
full walks.

| pass | classified | % of floor | what it was |
|---|---|---|---|
| na 2026-08-10 | 93,353 | 37% | the WG `504` + DNS outage; the incident that prompted this |
| eu 2026-07-17 | 336,000 | 78% | a partial pass the old global floor absorbed **silently** |
| eu 2026-06-22 | 262,271 | 61% | the instrumentation-rollout first pass, partially accumulated |

The old global fired on 1 of those 3. Note the eu 2026-07-17 pass is why the
invariant had to give: `crawl_bucket_mismatch` could not see it either, because
its five buckets summed to 336,000 exactly. A partial pass is internally
consistent — only its magnitude betrays it.

`yield_total` stays **global and loose on purpose**. Unlike classified it is
genuinely volatile (a 3–4× swing within one realm) because it tracks real player
churn rather than catalog size, so a tight floor would cry regression constantly.

No threshold on `yield_frac`, `overlap_frac` or the individual buckets, and this
is a deliberate refusal, not an oversight. A low `yield_frac` is a **cadence /
saturation** question, not an incident, and the `/crawl-yield` skill requires two
to three same-realm passes plus flat `active_7d` before any such verdict; a daily
alert mail must not be making it. `discovered_dormant` has legitimately been 0 on
na. `yield_total < 200` is set an order of magnitude below the lowest observed
pass so it can only mean total collapse of the crawl's floor-impossible value.

### Recapture, per realm

| metric | observed range (n=113) | threshold | class |
|---|---|---|---|
| `partial` | absent pre-2026-08-06; `false` on all post-fix runs; never `true` | **`is not False` alerts** | tuned (shape) |
| `mode` | `apply` on all 113 | `!= "apply"` alerts | tuned |
| `chunk_errors` | **0 on all 113** | `> 0` | tuned |
| `no_data` | 2 .. 23 (of 30,000 scanned) | `> 500` | backstop |
| `scanned` | 30,000 on all 113 | `<= 0` alerts | backstop |
| `wg_calls` | 300 on all 113 | `<= 0` with `scanned > 0` alerts | tuned (shape) |
| `cursor_stamped` | 30,000 on all 113 | `<= 0` in apply mode alerts | tuned (shape) |
| `advanced` | 33 .. 2,297 (33 was an off-cycle 3rd-of-day na run) | `< 10` | backstop |
| component sum vs `scanned` | exact on every inspected run | mismatch alerts | tuned (shape) |
| `into7d`, `into7d_clanless`, `into7d_clanned`, `still_lapsed`, `still_lapsed_clanless`, `still_dormant`, `yield_frac`, `band_days`, `limit`, `candidates`, `hidden` | `into7d_clanless` as low as **2**; `hidden` 0 .. 18; `yield_frac` 0.0011 .. 0.0766 | **none** | see below |

No threshold on the yield-quality fields. The `/recapture` skill is explicit that
a healthy dormant pool is mostly `still_dormant`, so a low single-digit percent
yield is expected and the absolute returner count is what matters. Historically
`into7d_clanless` has been as low as **2** and `advanced` as low as **33** on
legitimate runs, so any floor tight enough to be meaningful would fire on healthy
days. `advanced < 10` is the one retained floor: it is effectively "the sweep
found nobody at all". `scanned` much smaller than `limit` on a **non-partial**
run is the healthy "cursor exhausted the pool" steady state and is correctly not
an alert; that is exactly why the `partial` check has to come first.

## Environment variables

All read through `warships.opsmail.cfg`, i.e. process env seeded from
`/etc/battlestats-ops-email.env`. **Values are canonical in Pass**; update Pass
and regenerate the file. Do not hand-edit the file on the droplet.

| var | default | meaning |
|---|---|---|
| `OPS_EMAIL_ALWAYS_SEND` | `0` | Kill switch. `1` restores the old always-mail daily digest. |
| `OPS_EMAIL_HEARTBEAT_DOW` | `mon` | Weekday (`mon`..`sun`) for the unconditional heartbeat send. Empty string disables it and forfeits the transport proof. |
| `OPS_ALERT_<NAME>` | see `DEFAULT_THRESHOLDS` | Per-threshold numeric override, e.g. `OPS_ALERT_RECAPTURE_MAX_AGE_HOURS=26`. An unparseable value silently falls back to the default. |

Unchanged: `BENCH_DIR`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `SMTP_*`,
`MAIL_*`, `OPS_EMAIL_ENV_FILE`.

## Operating it

```bash
# What would fire today, without sending or calling the API:
ssh root@battlestats.online \
  '/usr/bin/python3 /opt/battlestats-server/shared/bin/daily_ops_email.py --dry-run --no-llm'

# Force one send now (transport check):
... daily_ops_email.py --force
```

`--dry-run` prints the verdict first: condition count, each code with its detail,
then the rendered mail. That is the fastest way to confirm a threshold change.

**Pair `--dry-run` with `--no-llm`.** `--dry-run` deliberately skips the
all-clear early return so you can always see the verdict, which means a clear-day
dry run will still call the Anthropic API if a key is in the environment. Harmless,
but it costs a request; `--no-llm` makes it free.

The unit is `battlestats-ops-digest.service`, `WorkingDirectory=
/opt/battlestats-server/current/server`, `ExecStart=/usr/bin/python3
scripts/daily_ops_email.py` (bare system python, deliberately, to keep the
stdlib-only property honest). It therefore runs the **deployed repo copy**;
`/opt/battlestats-server/shared/bin/daily_ops_email.py` is a dead 2026-07-01
leftover that nothing executes. The timer carries `RandomizedDelaySec=300`, so the
real fire window is 11:30 to 11:35 UTC; the staleness margins above account for
the late end.

Transport is the **systemd timer `battlestats-ops-digest.timer` at 11:31 UTC**,
not root's crontab; the two snapshot-writing jobs (04:30 and 04:35) are the cron
entries. Unchanged by this work.

## Reading an alert

Order the response by the condition class:

1. `snapshot_unreadable:*` / `snapshots_missing:*` / `realm_snapshot_missing:*` —
   the instrument is broken, not the system it measures. Check the writer.
2. `snapshot_stale:*` — the producing job did not run. For recapture that is
   `recapture_lapsed_players_task` on the `background` worker; check
   `journalctl -u battlestats-celery-background` for a soft time limit.
3. `recapture_partial:*` / `recapture_partial_field_absent:*` /
   `snapshot_status:*` / `*_mismatch:*` — the snapshot's counts are not
   trustworthy. Do not reason from its numbers until this is resolved.
4. Numeric codes (`obs_*`, `crawl_*`, `recapture_*`) — backstops. One firing
   means something large moved; go to the matching skill (`/observation`,
   `/crawl-yield`, `/recapture`) for the interpretation discipline before
   drawing a conclusion.

### Long-cycle tasks are exempt from the zero-success rule (2026-08-28)

`LONG_CYCLE_TASKS` in `daily_ops_email.py` is a frozenset of task names skipped
by the `celery_task_failing` rule. One member today:
`warships.tasks.crawl_all_clans_task`.

The zero-success discriminator assumes the 24h window bounds the unit of work.
For the clan crawl it does not: a full pass takes ~12-18h against a 20700s
(5h45m) per-dispatch soft limit, so truncation is the designed steady state and a
pass completes every 2-4 dispatches. On the droplet journal for the seven days to
2026-08-28 there were three completions, so **4 of those 7 days** held zero
successes and at least one `SoftTimeLimitExceeded` — the exact shape the rule
alerts on. That is the same failure mode the any-failure rule had with cache
warmers, one level up: a rule that fires four mornings in seven stops being read.

**The exemption costs something and the cost is not hedged.** `celery_task_realm_failing`
only fires when at least one realm is succeeding, so an exempt task broken in
*every* realm trips neither Celery rule. Cover falls entirely to
`snapshot_stale:crawl-yield:<realm>` at 168h — seven days of latency on a rare
total failure, traded against a false positive four days in seven. Widen the set
only for a task that (a) has a unit of work larger than the window **and** (b) is
already covered by a staleness rule on its output.

Runbook: `agents/runbooks/runbook-ops-alert-remediation-2026-08-28.md`.

## Test coverage

`server/warships/tests/test_daily_ops_email.py`, 47 tests. Notably:

- healthy input trips zero conditions, sends nothing, **and does not call the
  Anthropic API at all** (asserting only "did not send" would pass even if the
  script synthesized a digest and discarded it);
- one test per tripped condition, each asserting `send_email` was called once
  with an `[battlestats] ops ALERT` subject;
- staleness has paired quiet/fire tests either side of each threshold (7h quiet
  vs 31h fires; 23h quiet vs 25.4h fires; 130h quiet vs 200h fires);
- `stale_over_24h` alone must stay quiet; only the combined rule fires;
- an Anthropic outage on the fire path still sends, with the condition named in
  the subject;
- the fail-loud `__main__` guard still mails, plus a structural assertion that it
  contains no verdict or kill-switch reference.

## Related

- `.claude/skills/observation/SKILL.md`, `.claude/skills/crawl-yield/SKILL.md`,
  `.claude/skills/recapture/SKILL.md` — the interpretation discipline the
  thresholds are built to respect.
- `agents/runbooks/runbook-recapture-lapsed-players-2026-06-26.md` — the
  `partial` semantics and the 2026-08-06 truncation fix.
- `agents/runbooks/ops-env-reference.md` — the env-var catalog.
