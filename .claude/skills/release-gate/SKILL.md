---
name: release-gate
description: Run the curated lean release gate for battlestats (backend pytest subset + frontend npm test) and report pass/fail with the failing tests. Use when the user says "run the release gate", "release gate", "before I cut a release", "ready to release", or asks whether the release gate passes. Read-only — does not bump VERSION or invoke release.sh.
---

# release-gate

Runs the curated release gate exactly as documented in `CLAUDE.md`. The gate is a deliberately small subset of tests that proves the most contract-load-bearing surfaces still work; it is the gate that minor and major releases must pass before `scripts/release.sh` runs.

## When to invoke

- "run the release gate", "release gate", "before I cut a release", "ready to release", "are we green"
- After finishing a feature change, before bumping VERSION
- Whenever the user asks for a fast confidence check that does not require the full Docker `run_test_suite.sh`

Do **not** invoke for: a single-file unit-test run (use direct `pytest` / `npm test -- <file>` for that), or for the full Docker stack release gate (that's `./run_test_suite.sh`).

## Procedure

### 1. Detect target

Default to running **both** backend and frontend in parallel unless the user specifies one. Backend and frontend are independent and can run concurrently in two background shells.

If the working tree is clean and `git diff HEAD` shows no changes, ask the user whether they want to run the gate anyway (sometimes useful before deploy) or skip.

### 2. Run the gate

**Backend** (run in background):
```bash
cd /home/august/code/battlestats/server && python -m pytest \
  warships/tests/test_views.py \
  warships/tests/test_landing.py \
  warships/tests/test_realm_isolation.py \
  warships/tests/test_data_product_contracts.py \
  -x --tb=short
```

**Frontend** (run in background):
```bash
cd /home/august/code/battlestats/client && npm test
```

Use the `Bash` tool's `run_in_background: true` for both, then `Monitor` (or `BashOutput`) to collect results. Do not poll with sleep loops.

### 3. Report

Output exactly this shape:

```
Release gate — <duration>

Backend:  PASS|FAIL — <N> passed, <M> failed (<files run>)
  └─ Failures: <test::name>
       <terse failure summary>
Frontend: PASS|FAIL — <N> passed, <M> failed
  └─ Failures: <test::name>
       <terse failure summary>

Verdict: GATE GREEN | GATE RED — <one-line summary>
```

If GREEN: also print the next step (`./scripts/release.sh <patch|minor|major>` if a release is the user's intent).
If RED: list the failing tests with their file paths so the user can jump straight in.

## Scope and limits

- This skill runs the gate and reports. It does **not** fix failing tests, bump VERSION, tag, or push.
- For `patch` releases, CLAUDE.md notes the gate may be skipped — surface this if the user is cutting a patch release and seems to be running the gate out of habit.
- This is a fast confidence check (~1–3 min). For deeper validation use `./run_test_suite.sh` (full Docker stack).
- Do not stash uncommitted changes; the user's working tree is sacred.
