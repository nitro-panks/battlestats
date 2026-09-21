# Runbook: Django 6.0 Upgrade (5.1.15 → 5.2 LTS → 6.0.7)

_Created: 2026-07-30_
_Context: The project runs Django 5.1.15, whose last patch shipped 2025-12-02 — the 5.1 series is end-of-life and no longer receives security fixes, while 5.2 (LTS) and 6.0 both received patches on 2026-07-07. This runbook plans the two-hop upgrade to 6.0.7._
_QA: Both target versions were dry-run against the real test suite before this runbook was written — 5.2.16 and 6.0.7 each pass 850/850 (2 skipped). Django 6.0.7 additionally passes `manage.py check` with no issues and `makemigrations --check --dry-run` with no changes detected. Evidence in §3._
_Status: **DEPLOYED TO PRODUCTION 2026-07-30 in v4.9.0.** Phase 1 (`django==5.2.16`) merged at `9da05da`, CI green on Postgres with migration replay. Phase 2 (`django==6.0.7`, `djangorestframework==3.17.1`, `asgiref==3.10.0`) followed. Both passed the full release gate locally (850 backend, 493 frontend, production build) with `manage.py check` clean and no migration drift. Shipped alongside the landing recent-players change; see §6a for what the live estate actually showed._

## Purpose

Move off an unpatched Django series and onto 6.0.7, in two verifiable hops. This
runbook exists because the upgrade looked expensive from a distance and turned out
to be unusually cheap on inspection — and that claim needs its evidence recorded,
because "cheap" is the assumption that would make someone skip the validation.

Read this before touching `server/requirements.txt`. It records exactly which pins
move, which breaking changes were checked against this codebase and found to be
no-ops, and what the dry run did **not** cover.

## 1. Why now

| | |
|---|---|
| Running | Django **5.1.15**, released 2025-12-02 |
| Last 5.1 patch | **2025-12-02** — eight months ago |
| Latest 5.2 (LTS) | **5.2.16**, released 2026-07-07 |
| Latest 6.0 | **6.0.7**, released 2026-07-07 |

5.1 is no longer receiving patches. Both supported series shipped a release on the
same day last month; 5.1 did not. That is the whole argument — this is a security
posture change first and a feature upgrade second.

## 2. Decisions

**Two hops, not one.** `5.1.15 → 5.2.16` first, then `5.2.16 → 6.0.7`, each merged
and CI-green on its own. 5.2 is the LTS (supported into 2028) and is the safe
resting point: if 6.0 surfaces something in production that the suite did not, the
fallback is a supported version rather than an unpatched one.

**Do not adopt the 6.0 Background Tasks framework.** It is the headline 6.0 feature
and the one most likely to be mistaken for relevant here. Reading the release notes
precisely: it provides "task definition, validation, queuing, and result handling",
but "the responsibility for running them continues to belong to external worker
processes." It is an interface, not a worker. This project already has a mature
Celery topology — five queues, per-queue routing, `acks_late`, kill switches, a
zombie-worker watchdog. Adopting the framework would add an abstraction layer
without removing Celery. Revisit only if the broker choice ever becomes fungible.

**Take the ORM features opportunistically, in separate changes.** `Aggregate(order_by=…)`,
core `StringAgg`, and `AnyValue` (PG16+; we run 18) are genuinely useful against
`data.py`'s aggregation paths and its five raw-cursor escapes — ordered aggregation
is a classic reason to drop into raw SQL. None of that belongs in the upgrade
commit. See §7.

**Python floor is already met.** Django 6.0 supports Python 3.12, 3.13 and 3.14
only. CI, prod (`server/Dockerfile`, `python:3.12-slim`) and the local venv were
unified on 3.12 earlier today (`0319047`). Had CI still been on 3.10, this upgrade
would have been blocked behind that change.

## 3. Dry run — what was actually verified

Performed 2026-07-30 in a throwaway venv against the working tree at `0319047`,
Python 3.12, sqlite + `--nomigrations`.

**Django 6.0.7 needs exactly three pins to move:**

| pin | from | to | why |
|---|---|---|---|
| `django` | 5.1.15 | **6.0.7** | the upgrade |
| `djangorestframework` | 3.16.1 | **3.17.1** | 3.16.1 declares Django ≤5.2; 3.17.1 adds 6.0 |
| `asgiref` | 3.8.1 | **3.10.0** | Django 6.0.7 requires `asgiref>=3.9.1`; this is a hard resolver failure, not a warning |

The asgiref constraint is the one that bites first: the initial install failed with
`your requirements and django==6.0.7 are incompatible` and nothing more specific.
It is not mentioned in the release notes' "backwards incompatible" section because
it is an install-time dependency bound, not a code change.

**Results on 6.0.7 + DRF 3.17.1 + asgiref 3.10.0:**

- `850 passed, 2 skipped` in 5.43s — identical pass count to 5.1.15
- **100 warnings — the same count as on 5.1.15.** No new deprecation warnings.
- `manage.py check` → `System check identified no issues (0 silenced).`
- `manage.py makemigrations --check --dry-run` → `No changes detected.` (exit 0)

**Results on 5.2.16** (the intermediate hop): `850 passed, 2 skipped`. Note this run
was performed on Python 3.14 during the separate Python-version investigation, not
3.12 — the Django-level behaviour is what it demonstrates. **Re-run it on 3.12
during Phase 1** rather than treating it as already covered.

## 4. Breaking changes, checked against this codebase

Every backwards-incompatible item in the 6.0 notes that could plausibly apply:

| 6.0 breaking change | Applies here? | Evidence |
|---|---|---|
| `DEFAULT_AUTO_FIELD` now defaults to `BigAutoField` | **No** | already set explicitly at `battlestats/settings.py:144` and `warships/apps.py:5` |
| Custom ORM expressions must return params as a **tuple** | **No** | zero `as_sql` implementations; no `Func`/`Aggregate`/`Transform`/`Lookup` subclasses anywhere in `warships/` |
| Dropped Python < 3.12 | **No** | 3.12 everywhere as of `0319047` |
| Database backend API renames (`returning_columns`, `fetch_returned_rows`) | **No** | third-party-backend surface; we use stock `django.db.backends.postgresql`. The five `connection.cursor()` sites in `data.py` are unaffected — that is public API |
| Dropped MariaDB 10.5 | **No** | PostgreSQL 18 |
| PostgreSQL floor raised | **No** | notes reference PG16 features; we run 18 |
| Adoption of Python's modern email API | **No** | the app sent no email when this was assessed. Since 2026-08-06 it does (`warships/opsmail.py`), but through **stdlib `smtplib`/`EmailMessage` directly**, never `django.core.mail`, so Django's email API is still not on the path and this row's verdict is unchanged |

**Ecosystem readiness** — every Django-coupled dependency already declares 6.0,
and for three of them the version we already pin is sufficient:

| package | pinned | declares Django 6.0? |
|---|---|---|
| `django-celery-beat` | 2.9.0 | **yes**, at our pin |
| `django-cors-headers` | 4.9.0 | **yes**, at our pin |
| `pytest-django` | 4.12.0 | **yes**, at our pin |
| `django-timezone-field` | 7.2.1 | latest 7.2.2 declares 6.0 and 6.1 |
| `djangorestframework` | 3.16.1 | **no** — must go to 3.17.1 |

## 5. Procedure

> **As-built note (2026-07-30):** both phases were executed as written, with one
> addition — Phase 2 also corrected the `django` environment marker in
> `requirements.txt` from `python_version >= '3.10'` to `>= '3.12'`, since 6.0
> requires 3.12. Cosmetic (pip enforces `requires_python` regardless) but the
> marker was stating something false. The prescribed soak between phases was
> **not** taken: the operator is deploying both together in a later batch, so the
> phases are separated in history for bisect value rather than in time.

### Phase 1 — 5.1.15 → 5.2.16 (LTS)

1. Worktree + branch (`chore/django-52-lts`), per `.claude/worktrees/` convention.
2. `server/requirements.txt`: `django==5.1.15` → `django==5.2.16`. Nothing else —
   5.2 does not require the asgiref or DRF bumps.
3. Rebuild the local venv:
   `uv pip install --python server/.venv/bin/python -r server/requirements.txt`
4. `manage.py makemigrations --check --dry-run` — expect `No changes detected`.
   **If it detects changes, stop** and inspect before generating anything.
5. `manage.py check` — expect no issues.
6. Full gate: `PYTHON_BIN=…/server/.venv/bin/python ./scripts/run_release_gate.sh`
   (~25s; runs client lint/tests/build plus all 850 backend tests).
7. Merge to main, push, confirm CI green **on Postgres with migrations** — this is
   the coverage the sqlite dry run does not provide (§6).
8. Deploy backend. Watch the Celery workers specifically: `django-celery-beat` is
   the only Django-coupled piece of the task layer.
9. Soak. Do not start Phase 2 the same day.

### Phase 2 — 5.2.16 → 6.0.7

1. Branch `chore/django-60`.
2. `server/requirements.txt`, all three together (they will not resolve separately):
   - `django==5.2.16` → `django==6.0.7`
   - `djangorestframework==3.16.1` → `djangorestframework==3.17.1`
   - `asgiref==3.8.1` → `asgiref==3.10.0`
3. Steps 3–8 as in Phase 1.
4. On the deploy, verify a real request path end-to-end, not just service status —
   `./scripts/healthcheck.sh` plus a live player page, since DRF moved a minor
   version alongside Django.

### Rollback

Both phases are pin-only changes with **no migration**, which is what makes rollback
cheap: revert the `requirements.txt` change and redeploy. `makemigrations --check`
returning clean on 6.0.7 is the fact that guarantees this — there is no schema state
to unwind. Release directories are retained under `/opt/battlestats-server/releases/`
per `KEEP_RELEASES` if a faster revert is needed.

## 6. What the dry run did NOT cover

State plainly, because the pass counts above are seductive:

- **Migrations against PostgreSQL.** The dry run used sqlite + `--nomigrations`.
  `makemigrations --check` proves no *new* migrations are needed; it does not
  exercise replaying the existing 80+ migrations on PG18. CI does exactly that on
  every push — treat the CI run as the real gate, not the local one.
- **A live Celery worker.** The suite dispatches tasks eagerly against an in-memory
  broker. No AMQP transport, no prefork worker, no Beat schedule, no `acks_late`
  redelivery. `django-celery-beat` declares 6.0 support, but declared ≠ exercised.
- **Sustained production load**, connection pooling behaviour under `CONN_HEALTH_CHECKS`,
  and the analytical paths that run with elevated `work_mem`.
- **The admin.** Little used here, but it is the largest surface 6.0 touched that
  the API tests do not reach.

## 6a. What the live deploy showed (2026-07-30, v4.9.0)

The §6 gaps were closed by observation at deploy time, not by argument:

| gap | observed |
|---|---|
| Migrations on PG18 under Django 6 | `migrate` ran clean: **"No migrations to apply"** — matching `makemigrations --check` |
| Live Celery estate | **5/5 lanes online** (default, hydration, background, floor, crawls), `failed_units: none` |
| AMQP transport + `acks_late` | queues drained to `ready=0` on every lane; 500-task sample **0.0% failure** |
| Beat schedule | **66 periodic tasks, 62 enabled** — the `post_migrate` registration survived intact |
| Runtime pins | prod reports Django **6.0.7**, DRF **3.17.1**, asgiref **3.10.0** |
| Public surface | 24/24 healthcheck endpoints pass; `GET /` 200 |

Flower's newest event was **11s old** at the check, which matters because of
`project_flower_blind_month_2026-07-27`: Flower has previously served *persisted*
state as live. Freshness was confirmed rather than assumed.

The admin remains the one §6 item still unexercised; it is little used here.

## 7. Follow-ups (deliberately out of scope)

- **`Aggregate(order_by=…)` + core `StringAgg` (6.0)** — revisit `data.py`'s five
  raw-cursor sites; ordered aggregation may now be expressible in the ORM.
- **`AnyValue` aggregate (6.0, PG16+)** — replaces the `Max()`-as-arbitrary-pick
  idiom in `GROUP BY`; we use `Max()` in six places, some likely this pattern.
- **`QuerySet.explain(memory=…, serialize=…)` (5.2, PG17+)** — directly useful for
  the recurring analytical-warmer tuning (`ANALYTICAL_WORK_MEM`, saturation triage).
- **`values()`/`values_list()` SELECT-ordering fix (5.2)** — makes `union()`
  predictable; check before relying on combined querysets.
- **Composite primary keys (5.2)** — `PlayerDailyShipStats` carries an implicit
  `BigAutoField` PK *plus* partial unique constraints on
  `(player, date, ship_id, mode[, season_id])`, so it maintains a surrogate index it
  does not semantically need. On one of the largest tables, against an 80 GiB disk
  with autoscale off, that is a real saving — **but a PK change is a full table
  rewrite on a hot table.** Evaluate against `project_db_table_audit_2026-07-19`
  before going near it.
- ~~Raise the CI `npm audit` gate from `critical` to `high` when `sharp` 0.35.0 ships
  stable.~~ **Done 2026-09-21**: `next` 16.2.12 → 16.3.5 pulled `sharp` 0.35.4 and
  cleared two critical `next` RCE advisories (GHSA-p293-qw3h-jr36,
  GHSA-2xp9-vwfh-vxw4) that had held CI red since 2026-09-09; prod-only audits to
  zero and the gate sits at `high` in `.github/workflows/ci.yml`.

## 8. Related

- `agents/runbooks/ops-env-reference.md` — env catalog, CI audit gate rationale
- `agents/runbooks/ops-infra-resources.md` — PG18 sizing, the 2-vCPU constraint
- `agents/runbooks/runbook-celery-queue-strategy.md` — the queue topology this
  upgrade must not disturb
- `CLAUDE.md` §Python version — why 3.12 is the ceiling for the pinned Celery stack,
  and why 3.14 is unavailable
