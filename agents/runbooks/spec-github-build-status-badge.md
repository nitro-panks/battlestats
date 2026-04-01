# Spec: GitHub Build Status Badge

_Last updated: 2026-04-01_

_Status: Implemented_

## Purpose

Add a CI build status badge to the GitHub repository README so build health is visible at a glance on the repo landing page. The badge should reflect the current state of the `CI` workflow on `main`.

## Current State

- **CI workflow**: `.github/workflows/ci.yml` — named `CI`, triggers on push to `main` and PRs to `main`
- **Jobs**: Two parallel jobs — `Client Checks` (lint, test, build) and `Server Checks` (pytest with Postgres + Redis services)
- **README**: `/README.md` — no badges currently present
- **Repo**: `nitro-panks/battlestats` on GitHub

## Badge Options

### Option A — Single combined badge (recommended)

One badge reflecting the overall workflow status. If either job fails, the badge shows failing.

```markdown
[![CI](https://github.com/nitro-panks/battlestats/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nitro-panks/battlestats/actions/workflows/ci.yml)
```

Renders as a shield that links to the workflow runs page. Shows `passing`, `failing`, or `no status` depending on the latest `main` branch run.

### Option B — Per-job badges

Separate badges for client and server, useful if one side tends to break independently.

```markdown
[![Client](https://github.com/nitro-panks/battlestats/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/nitro-panks/battlestats/actions/workflows/ci.yml)
[![Server](https://github.com/nitro-panks/battlestats/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/nitro-panks/battlestats/actions/workflows/ci.yml)
```

Note: GitHub's native badge API does not support per-job filtering. Both badges would show the same overall status. For true per-job badges, you'd need shields.io with a job filter:

```markdown
[![Client](https://img.shields.io/github/actions/workflow/status/nitro-panks/battlestats/ci.yml?branch=main&label=client)](https://github.com/nitro-panks/battlestats/actions/workflows/ci.yml)
[![Server](https://img.shields.io/github/actions/workflow/status/nitro-panks/battlestats/ci.yml?branch=main&label=server)](https://github.com/nitro-panks/battlestats/actions/workflows/ci.yml)
```

However, shields.io also cannot filter by job name — only by workflow. True per-job granularity would require splitting `ci.yml` into `ci-client.yml` and `ci-server.yml`, each with its own badge URL.

### Recommendation

**Option A** — single combined badge. The workflow already runs both jobs in parallel; if either fails, the team needs to investigate regardless. One badge is simpler and avoids the false precision of two badges that show the same status.

If per-job visibility becomes important later, split the workflow into two files at that point.

## Additional Badges (Optional)

These are nice-to-have and can be added alongside the build badge:

| Badge | Source | Markdown |
|---|---|---|
| License | Static/shields.io | `[![License: CC BY-NC-SA 4.0](https://img.shields.io/badge/License-CC%20BY--NC--SA%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc-sa/4.0/)` |
| Version | VERSION file via shields.io | `[![Version](https://img.shields.io/badge/dynamic/regex?url=https%3A%2F%2Fraw.githubusercontent.com%2Fnitro-panks%2Fbattlestats%2Fmain%2FVERSION&search=%5Cd%2B%5C.%5Cd%2B%5C.%5Cd%2B&label=version)](https://github.com/nitro-panks/battlestats/releases)` |
| Site status | curl check / Upptime | Only if an uptime monitor is configured |

These are optional — the build status badge alone is the deliverable.

## Implementation

### Changes

**`README.md`** — Add badge on line 1, before the title:

```markdown
[![CI](https://github.com/nitro-panks/battlestats/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nitro-panks/battlestats/actions/workflows/ci.yml)

# battlestats
```

That's it. No workflow changes, no new files, no new dependencies.

### Placement convention

Badge goes at the very top of `README.md`, above the `# battlestats` heading. This is the standard GitHub convention — badges render as a compact status bar above the project title on the repo landing page.

## Known CI Issues

The current CI workflow has a few pre-existing test failures that will show the badge as `failing` until resolved:

| Test | Issue | Severity |
|---|---|---|
| `test_landing_clans_cache_miss_then_hit` | Test fixture missing `cached_clan_wr` field required by `score_best_clans()` | Low — test bug, not prod bug |
| `test_landing_clans_cache_clear_returns_fresh_data` | Same root cause | Low |
| `test_landing_clans_support_gzip_for_large_json_payloads` | Same root cause | Low |
| `test_checkpoint_url_derived_from_db_environment` | SSL env var mismatch in Docker test env | Low — env-specific |
| `test_agentic_memory_command` (2 errors) | Agentic memory module import issues | Low |

**Recommendation**: Fix the 3 `LandingClansCacheTests` fixture issues (add `cached_clan_wr`, `cached_total_battles`, `cached_active_member_count` to `_create_best_clan()`) before or alongside adding the badge, so the badge renders green on first commit.

## Validation

- [ ] Badge visible on GitHub repo landing page at `github.com/nitro-panks/battlestats`
- [ ] Badge links to Actions workflow runs page
- [ ] Badge shows `passing` (after pre-existing test fixes) or accurate current status
- [ ] Badge updates automatically on next push to `main`

## Version Impact

**No version bump** — documentation-only change.
