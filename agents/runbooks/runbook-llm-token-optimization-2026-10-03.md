# Runbook: LLM Token Usage in the Two Unattended Emails

_Created: 2026-10-03_
_Context: A cost pass over every Anthropic API call in this repo. The finding is that total spend is on the order of one US dollar per month, so the correct amount of optimization is small: one measurement change, one formatting change, one owner-approved behavior change (the heartbeat no longer calls the model), and one decision still left to the owner._
_QA: Call-site claims verified by reading the code in a worktree at `ca71c69`. Line numbers throughout refer to that commit, before changes 1 and 2 were applied; the helper added by change 1 shifts later lines in each script by 18. Character counts are measured from the test fixtures. Token counts and dollar figures are estimates: no API key is configured locally, no probe call was made, and neither script logged `usage` before this change. Rates fetched from the Anthropic pricing page on 2026-10-03._

## QA Notes

_Reviewed 2026-10-03 against /home/august/code/battlestats/.claude/worktrees/token-optimization-1003. 41 assertions checked, 6 corrected._

### Resolved
- **"The comments at lines 1041 and 1258 record that a tighter cap once produced an empty response"** -> actual: the daily comment spans lines 1040 to 1044 and the weekly one 1258 to 1262 (`server/scripts/daily_ops_email.py:1040`, `server/scripts/weekly_traffic_email.py:1258`) -> both references in the body now give the ranges.
- **"the `AnthropicCallShapeTests` docstrings describe Opus 5 behavior" (implying both test modules)** -> actual: that class exists only in the daily module (`server/warships/tests/test_daily_ops_email.py:718`); the weekly equivalent is a method of `ContractTests` (`server/warships/tests/test_weekly_traffic_email.py:1014`) -> D2 now names the test method and both classes.
- **"the fixture lacks a few optional blocks"** -> actual: `write_healthy_tree` writes one observation snapshot (`server/warships/tests/test_daily_ops_email.py:70`), so `gather_observation` returns `d1: None`, `d7: None` and no `delta_vs_d1` (`server/scripts/daily_ops_email.py:167`) -> the body now states exactly which blocks are missing and gives a single production estimate.
- **Validation command omitted `DJANGO_SECRET_KEY`** -> actual: settings read it from the environment (`server/battlestats/settings.py:13`) and the project's documented test command sets `DJANGO_SECRET_KEY=k` -> added to the command.
- **Change 1 did not say stdout or stderr, nor what happens on `usage: null` or on an HTTP error** -> picked stdout, matching the scripts' existing `[ok]` and `[warn]` lines (`server/scripts/daily_ops_email.py:1237`, `server/scripts/weekly_traffic_email.py:1394`); null usage prints zeros; an HTTP error raises in `urlopen` (`server/scripts/daily_ops_email.py:1074`) before a payload exists, so no line is printed -> all three stated in change 1. The only documented dry-run consumer passes `--no-llm` (`.claude/skills/ops-alert/SKILL.md:101`), so the extra stdout line cannot reach it.
- **Tests were named loosely ("UsageLoggingTests", "new tests") and the runbook index was not mentioned** -> actual: every existing test patches `call_anthropic` whole (`server/warships/tests/test_daily_ops_email.py:246`, `server/warships/tests/test_weekly_traffic_email.py:986`), so no existing test covers the request body; and the runbook index requires a line per runbook in the same change (`agents/runbooks/README.md:5`) -> one named class `AnthropicRequestTests` per module covers both changes; a "Documentation bookkeeping" section adds the README line and the registry entry.

### Unverified
- The live `ANTHROPIC_MODEL` value: it lives in `/etc/battlestats-ops-email.env` on the droplet and in Pass; neither was read. All cost figures assume the code default `claude-opus-5`.
- Every token count and dollar figure: derived from character counts, not from a `usage` field. No API key is configured locally and no probe call was made.
- Alert frequency (4 to 16 per month): extrapolated from one week cited in `.claude/skills/ops-alert/SKILL.md:60`; the production journal was not read.
- That the weekly timer unit is named `battlestats-traffic-digest`: stated only in `agents/runbooks/runbook-weekly-traffic-email-2026-08-09.md:315`; unlike the ops digest unit it is not created by `server/deploy/deploy_to_droplet.sh`, and the droplet was not inspected.
- That compact JSON leaves output quality unchanged: reasoned, not measured. No eval exists for either email.
- The 512-token cache minimum for Opus 5 and Haiku 4.5's rejection of `effort`: taken from the bundled `claude-api` skill reference, not exercised against the API.

### Open Questions
1. **Should the Monday heartbeat call the model?** The documentation says the heartbeat mails the deterministic table (`agents/runbooks/runbook-ops-email-exception-only-2026-08-09.md:49`); the code sent an LLM digest (`server/scripts/daily_ops_email.py:1275`). Skipping the model saves the largest share of spend and changes the Monday mail's body. Blocked D1 only. **Answered 2026-10-03: the owner approved; D1 is executed, see its execution record.**
2. **Is a model change wanted?** `claude-opus-5-5` is 20% cheaper and request-compatible; anything lower is a quality tradeoff with no eval to check it. Blocks D2 only.

## Purpose

Records where this project spends LLM tokens, how much, and which levers are worth pulling. Read it before changing the model, the prompts, or the payload of either email script, and before anyone proposes prompt caching or the Batch API here: both were evaluated and rejected below, with the reasons.

## Scope: the only two call sites

The Anthropic Messages API is called from exactly two places, both over raw `urllib` (stdlib-only by contract; no SDK):

| Script | Function | Cadence of the timer | When the LLM is actually called |
|---|---|---|---|
| `server/scripts/daily_ops_email.py` | `call_anthropic` (line 1037) | daily 11:30 UTC | Before D1: only when a mail is going out, that is an alert day, the Monday heartbeat, `OPS_EMAIL_ALWAYS_SEND=1`, `--force`, or `--dry-run` without `--no-llm`. Since D1 (2026-10-03) a heartbeat-only send no longer calls it. The early return at line 1233 exits before any API call on a clear day. |
| `server/scripts/weekly_traffic_email.py` | `call_anthropic` (line 1256) | Mondays 10:30 UTC | Every run, unless `--no-llm` or the key is absent. |

`grep -ri anthropic` over `server/`, `client/`, `scripts/` and `functions/` finds no other caller. `server/warships/views.py` and `server/warships/data.py` contain no Anthropic or LLM reference. `server/scripts/check_env_drift.sh` only reconciles the `ANTHROPIC_MODEL` value; it calls nothing.

There is **no retry and no second call** in either script. The two `call_anthropic(...)` invocations in `daily_ops_email.py` (lines 1265 and 1275) are the two arms of an `if alerting / else`: exactly one runs. On any API failure both scripts fall back to deterministic Python rendering (`render_plain` at line 1290 of the daily script; `render(..., lead_error=...)` in the weekly script). Nothing is resent.

## Current state

### Model

Both scripts resolve the model as `cfg("ANTHROPIC_MODEL", "claude-opus-5")` (daily line 1259, weekly line 1370). The code default is `claude-opus-5`. The live value is whatever `/etc/battlestats-ops-email.env` carries on the droplet, generated from the Pass entry `battlestats/anthropic-model`. **This runbook did not read the live value** (no production or secrets access was used); run `server/scripts/check_env_drift.sh` to see it. All figures below assume `claude-opus-5` at $5 input / $25 output per million tokens.

### Request shape

| | Daily ops email | Weekly traffic email |
|---|---|---|
| System prompt | `SYSTEM_PROMPT` (line 940, 3,225 chars) on heartbeat or forced sends; `ALERT_SYSTEM_PROMPT` (line 993, 2,715 chars) on alert days | `SYSTEM_PROMPT` (line 1148, 2,491 chars) |
| User content | one instruction sentence plus the whole `data` dict as JSON, `indent=2` (line 1059) | one sentence plus `llm_payload(data)` as JSON, `indent=2` (line 1271) |
| Payload size (test fixture) | 7,121 chars indented; 4,544 compact. 2,577 chars (36%) are indentation and newlines | 1,710 chars indented; 1,155 compact. 559 chars (33%) are whitespace |
| `max_tokens` | 8000 (line 1046) | 4000 (line 1264) |
| Effort | `output_config: {"effort": "low"}` (line 1047) | same (line 1265) |
| Thinking | not sent; the model's adaptive default applies | same |
| Output | strict JSON carrying a subject and a complete HTML email body | strict JSON carrying one 2 to 4 sentence paragraph |
| Usage logging | none | none |

The production daily payload is larger than the fixture. `write_healthy_tree` writes a single observation snapshot, so the fixture's `observation.d1` and `observation.d7` are null and `delta_vs_d1` is absent; in production all three are present. Assume about 9 thousand characters indented.

### Assessment of each dimension

- **Payload fields.** The weekly payload is already deliberately narrow: `llm_payload` (line 1190) withholds per-route, per-referrer and per-day counts for correctness reasons documented in its docstring. Nothing to remove. The daily payload is the full gather output. On alert days the instruction tells the model to ignore everything outside `tripped_conditions`, so most of it is unused in principle; but the alert prompt also says "every number you need is in the payload", and the tripped families' blocks supply the context for the "where to look" line. Trimming it is a quality risk for under a cent per alert. Not proposed.
- **History.** The daily payload carries `d1` and `d7` observation blocks and the previous crawl pass per realm. The digest prompt's noise discipline ("only flag a regression if sustained across multiple clean days") depends on them. Keep.
- **Precision.** Deltas are already rounded to 4 places and ages to 2. Nothing to gain.
- **JSON formatting.** `indent=2` spends roughly a third of the payload characters on whitespace. This is the one free input saving. See change 2.
- **System versus user split.** Correct as it stands: stable instructions in `system`, volatile data in the user turn. The split only matters for caching, which does not apply (see non-goals).
- **`max_tokens`.** It is a ceiling, not a cost. The comments at daily lines 1040 to 1044 and weekly lines 1258 to 1262 record that a tighter cap once produced an empty response because the cap covers thinking and text together. Lowering it saves nothing and reintroduces that failure. Keep.
- **Effort.** Already `low`, the cheapest level. Nothing below it.
- **Output length.** The daily call's output is a whole HTML email, and output is billed at five times the input rate: output is the dominant cost of that call. Shortening it would change the email. Not proposed.
- **Retry path.** None exists. Nothing to fix.

## Estimated volume and cost

The two tables below describe the state **before D1**. The revised figures are under "Revised estimate after D1".

Estimates from character counts at roughly 3 characters per token for this mix of prose and JSON. Nothing here is measured from a real `usage` field; change 1 exists to replace these with measurements.

| Call | Input tokens | Output tokens (incl. low-effort thinking) | Cost per call at $5 / $25 |
|---|---|---|---|
| Daily ops, heartbeat or alert | about 3,500 to 4,500 | about 1,500 to 3,000 | about $0.06 to $0.10 |
| Weekly traffic lead | about 1,300 | about 300 to 600 | about $0.015 to $0.02 |

Calls per month: the weekly script runs 4.3 times. The daily script calls the model on about 4.3 heartbeat Mondays plus every alert day; the ops-alert skill records four alerts in one August week, so a range of 4 to 16 alert days per month is assumed.

| | Calls per month | Tokens per month (in / out) | Cost per month |
|---|---|---|---|
| Daily ops email | 8 to 20 | 30k to 90k / 15k to 60k | about $0.50 to $2.00 |
| Weekly traffic email | 4.3 | about 6k / about 2k | about $0.07 to $0.09 |
| **Total** | | **under 100k in, under 65k out** | **about $0.60 to $2.10** |

Every proposal below should be read against that ceiling. A lever that saves 20% saves cents.

## Changes, ranked by estimated saving

Ranked by savings ceiling, not application order.

| # | Change | Type | Estimated saving | Basis | Status |
|---|---|---|---|---|---|
| D1 | Heartbeat mails the deterministic table instead of an LLM digest | behavior change | about $0.25 to $0.45 per month (about a quarter to a half of total) | inferred from call counts | executed 2026-10-03 on owner approval |
| D2 | Model change | quality tradeoff | 20% (Opus 5.5) to 60% (Sonnet 5.5) of the bill: about $0.12 to $1.20 per month | pricing page | **needs owner decision** |
| 2 | Compact JSON in both user turns | free win | about 10% to 20% of input tokens: about $0.02 to $0.06 per month | inferred from measured character counts | executed |
| 1 | Log `usage` from each response | measurement | $0; turns every figure in this runbook into a measurement | n/a | executed |

### Change 1: log the `usage` object (executed)

Neither script recorded what a call cost, so no claim about tokens could be checked. After the response body is parsed, each `call_anthropic` now prints one line to stdout, which the systemd journal captures:

```
[llm] model=claude-opus-5 stop_reason=end_turn input_tokens=4012 output_tokens=1877 cache_creation_input_tokens=0 cache_read_input_tokens=0
```

Edits:

- `server/scripts/daily_ops_email.py`: add a module-level `_usage_line(payload: dict) -> str` above `call_anthropic`; call `print(_usage_line(payload))` immediately after the `json.loads(resp.read()...)` line and before the refusal check, so a refused or unparseable response is still accounted for. Stdout, not stderr: it matches the existing `[ok]` and `[warn]` journal lines (daily lines 1237, 1318, 1333).
- `server/scripts/weekly_traffic_email.py`: the same helper and the same call site.

The helper reads only `model`, `stop_reason` and the `usage` dict from the response. It never touches the request, so the API key cannot reach the line. A response without a `usage` key, or with `usage: null`, prints zeros rather than raising: the log line must never be the reason an email fails. An HTTP error raises inside `urlopen` before any payload exists, so no line is printed for it; the existing `llm_error` text already names that case. Public signatures of both `call_anthropic` functions are unchanged, and their only callers are the two `main()` functions.

Under `--dry-run` the line appears on stdout ahead of the rendered output. That is intended; a dry run without `--no-llm` is a billed call and should say so.

Verification: a new class `AnthropicRequestTests` in each test module (`server/warships/tests/test_daily_ops_email.py`, `server/warships/tests/test_weekly_traffic_email.py`) patches `urllib.request.urlopen` on the loaded script module, calls the real `call_anthropic`, and asserts the `[llm]` line, the absence of the key from stdout, and tolerance of a missing `usage` field. No existing test calls the real `call_anthropic` (all patch it), so none needs updating; the weekly `ContractTests` import allowlist is unaffected because no import is added. After the next production run, `journalctl -u battlestats-ops-digest.service -u battlestats-traffic-digest.service | grep '\[llm\]'` shows real figures; update the estimate table above from them.

### Change 2: compact JSON in the user turn (executed)

Edits:

- `server/scripts/daily_ops_email.py` line 1059: `json.dumps(data_package, indent=2, default=str)` becomes `json.dumps(data_package, separators=(",", ":"), default=str)`.
- `server/scripts/weekly_traffic_email.py` line 1271: the same substitution on `json.dumps(data, ...)`.

Key order, keys, values and nesting are unchanged; only insignificant whitespace is removed. The fixture payloads shrink from 7,121 to 4,544 characters (daily) and from 1,710 to 1,155 (weekly). The token saving is smaller than the 36% character saving because a tokenizer merges runs of indentation; 10% to 20% of input tokens is the working estimate, which is about 400 to 800 tokens per daily call and about 100 per weekly call. At $5 per million input tokens that is two to six cents per month.

Quality: the model receives the same data. Compact JSON is a format current models read without loss, and every numeric rule in both prompts refers to fields by name, not by layout. No eval exists for either email, so this rests on reasoning, not on a measured comparison; if a lead or alert ever misattributes a figure to the wrong realm after this change, revert these two lines first.

Verification: tests in the same `AnthropicRequestTests` classes assert that the user turn sent to the API contains the compact serialization, that it round-trips to the original dict, and that it contains no newline inside the JSON portion. After deployment, compare `input_tokens` in the `[llm]` line against the estimate above.

## D1: the Monday heartbeat no longer calls the model (executed 2026-10-03, owner-approved)

### What was wrong

The exception-only runbook states that the heartbeat "mails the deterministic table regardless of verdict" (`runbook-ops-email-exception-only-2026-08-09.md`, safeguard 3). The module docstring of `daily_ops_email.py` says the same, and the `render_plain` docstring lists "the weekly heartbeat" among its uses. The code did otherwise: on a clear Monday `main()` reached `call_anthropic(model, api_key, data)` with the full digest prompt, and only the subject was overwritten to `[battlestats] ops heartbeat: all clear`. The heartbeat was an LLM-written digest, about 4.3 times a month, at the most expensive call shape in the repo, and the transport proof depended on the Anthropic API being up.

### Execution record

In `main()` of `server/scripts/daily_ops_email.py`:

```python
heartbeat_only = beat and not alerting and not (always or forced)
...
if not no_llm and not heartbeat_only:
```

The model is skipped exactly when all of the following hold: today is the configured heartbeat day (`heartbeat_due(now)`), no condition tripped, `OPS_EMAIL_ALWAYS_SEND` is off, and `--force` was not passed. The mail is then `render_plain(data, conditions, reason)` with the heartbeat reason line. This is the same predicate that already stamped the heartbeat subject, and the subject branch now uses the named variable; the subject line itself is unchanged.

Every other path is as it was:

| Situation | Model called | Prompt | Subject |
|---|---|---|---|
| Any alert day, heartbeat day or not | yes | `ALERT_SYSTEM_PROMPT` | `[battlestats] ops ALERT...` |
| `--force` or `OPS_EMAIL_ALWAYS_SEND=1`, clear day, heartbeat day or not | yes | digest `SYSTEM_PROMPT` | the model's digest subject |
| Heartbeat day, clear, not forced | **no (changed)** | none | `[battlestats] ops heartbeat: all clear` |
| Heartbeat day, clear, `--dry-run` | **no (changed)**; a dry run shows what would be sent | none | same, printed not sent |
| Non-heartbeat clear day, `--dry-run` without `--no-llm` | yes (unchanged) | digest `SYSTEM_PROMPT` | the model's digest subject, printed not sent |
| Any path with `--no-llm`, no key, or an API failure | no (unchanged) | none | from `render_plain` |
| Clear day, no heartbeat, no force, no dry run | no mail at all (unchanged) | | |

Because the heartbeat table is now sent by design, the red "LLM synthesis failed" banner does not appear on it: `llm_error` stays unset.

Tests added to `LivenessTests` in `server/warships/tests/test_daily_ops_email.py`: `test_heartbeat_only_send_does_not_call_the_model`, `test_heartbeat_day_dry_run_does_not_call_the_model_either`, `test_an_alert_on_the_heartbeat_day_still_calls_the_model`, `test_an_alert_send_calls_the_model`, `test_a_forced_send_on_the_heartbeat_day_still_writes_the_digest`.

### Revised estimate after D1

The daily script now calls the model only on alert days (assumed 4 to 16 per month) and on forced sends.

| | Calls per month | Tokens per month (in / out) | Cost per month |
|---|---|---|---|
| Daily ops email | 4 to 16 | 15k to 70k / 6k to 48k | about $0.25 to $1.60 |
| Weekly traffic email | 4.3 | about 6k / about 2k | about $0.07 to $0.09 |
| **Total** | | **under 80k in, under 50k out** | **about $0.30 to $1.70** |

The saving is the 4.3 removed heartbeat calls at $0.06 to $0.10 each: about $0.25 to $0.45 per month. Inferred from call counts, not measured. On a month with few alerts the weekly traffic lead becomes a visible share of what remains.

### Left alone, on purpose

Two places where documentation and code differ beyond the heartbeat body. Neither was changed, because each needs the owner's intent:

- The docs say the heartbeat mails the table "regardless of verdict". On a Monday with tripped conditions the code sends one mail, the alert, with the alert subject; no separate heartbeat-subject mail goes out that week. The transport is still proven, but a filter on the heartbeat subject sees a gap.
- The module docstring says "the model is invoked only to write up an alert Python has already decided to send". The digest prompt is still reachable on a clear day through `--force`, `OPS_EMAIL_ALWAYS_SEND=1`, and `--dry-run` without `--no-llm` on a non-heartbeat day.

## Needs owner decision (not executed)

### D2: model choice

The task in both scripts is short-form writing over a small, pre-computed data package at `low` effort. Options, at rates fetched 2026-10-03:

| Model | Input / output per MTok | Relative cost | Notes |
|---|---|---|---|
| `claude-opus-5` (code default) | $5 / $25 | baseline | |
| `claude-opus-5-5` | $4 / $20 | 20% lower | Same tier, newer. The request shape these scripts send is compatible: no `thinking` parameter, explicit `effort: low`, no `tool_choice`, no prefill. Its refusal classifiers are broader; both scripts already name a refusal and fall back to deterministic rendering. |
| `claude-sonnet-5-5` | $2 / $10 | 60% lower | Tier step-down. Same request compatibility. The risk is adherence to the long negative rule lists (never compute a number; never juxtapose two figures), which were written in response to observed violations. |
| `claude-haiku-4-5` | $1 / $5 | 80% lower | Not a drop-in: it rejects `output_config.effort`, so the request body would need a per-model branch. Not recommended. |

No eval exists for either email, so no step-down can be validated here; the only check is the owner reading the output. The absolute saving is at most about a dollar a month. If the owner wants one, the low-risk move is `claude-opus-5-5`. A model change is made in three places that `check_env_drift.sh` reconciles: the Pass entry, the live env file, and the code default in both scripts. Note also that the comments at daily lines 1040 to 1044 and weekly lines 1258 to 1262, and the docstrings of `test_thinking_is_bounded_by_low_effort_not_disabled` (in `AnthropicCallShapeTests` in the daily test module and in `ContractTests` in the weekly one), describe Opus 5 behavior specifically and would need rewording.

## Non-goals

- **Prompt caching.** Not applicable, and adding `cache_control` would cost money. A cache entry lives 5 minutes, or 1 hour at a 2x write price. The closest two calls ever get is the Monday pair, an hour apart, in different scripts with different system prompts; otherwise calls are at least 24 hours apart. No request can ever read a prior request's cache. Both system prompts exceed the 512-token minimum for Opus 5, so a marker would actually write an entry and bill the prefix at 1.25x for zero reads. Per the owner's standing note, `count_tokens` overstates the billed cache prefix and only `usage.cache_read_input_tokens` on a real response proves a hit; the `[llm]` line from change 1 prints that field, and it should read 0 forever.
- **Batch API.** 50% off, and nobody waits on the weekly lead, but a batch needs a submit, poll and collect cycle across timer runs, with a 24-hour completion window. That is new state and new failure modes in two scripts whose design goal is to be stdlib-only and fail-loud, in exchange for about four cents a month on the weekly call. Alerts are time-sensitive and cannot batch at all.
- **Lowering `max_tokens`.** Saves nothing; see the assessment above.
- **Trimming the daily payload on alert days.** Under a cent per alert against a real risk of an alert that cannot explain itself.
- **Shortening either system prompt.** Each rule in them records a specific observed failure. The whole of both prompts costs about half a cent per call.
- **Disabling thinking.** The comments at the call sites explain why this is forbidden: leaked tags break the JSON parse.
- **Moving to the SDK.** Would break the no-venv contract and saves no tokens.

## Validation

- `cd server && DJANGO_SECRET_KEY=k DB_ENGINE=sqlite3 DB_NAME=<scratch>.sqlite3 DB_SSLMODE='' DB_SSLROOTCERT='' REDIS_URL='' CELERY_BROKER_URL=memory:// CELERY_RESULT_BACKEND=cache+memory:// .venv/bin/python -m pytest --nomigrations warships/tests/test_daily_ops_email.py warships/tests/test_weekly_traffic_email.py warships/tests/test_opsmail.py`
- No email was sent, no production system was touched, and no API call was made in preparing or executing this runbook.

## Documentation bookkeeping (executed)

- `agents/runbooks/README.md`: one index line for this runbook, beside the two email runbooks.
- `agents/doc_registry.json`: one registry entry, shaped like the weekly traffic email entry.

## Follow-ups

- After the first production runs carrying change 1, replace the estimates in "Estimated volume and cost" with the journal's `[llm]` figures.
- Owner to decide D2.
- Owner to decide the two items under "Left alone, on purpose" in the D1 section.
- D1 takes effect on the droplet only after the next backend deploy; the first Monday mail after that should be the plain table with the heartbeat subject, and the journal should carry no `[llm]` line for that run.
