# Runbook: Dependency Audit

**Created**: 2026-03-29
**Status**: Phase 1 implemented. Phase 2 deferred (infeasible with current Docker architecture).

## Scope

Audit of all declared Python (Pipfile, requirements.txt) and JavaScript (package.json) dependencies against actual codebase usage.

## Findings Summary

| Severity | Count | Status |
|----------|-------|--------|
| Unused production dependency | 5 | Resolved |
| Stale requirements.txt entries | 2 | Resolved |
| Missing Pipfile entry (`redis`) | 1 | Resolved |
| Redundant explicit transitive deps | 2 | Resolved |
| Unused client dependency | 4 | Resolved |
| Dev/test tools shipped to production | 4 | Deferred (see Phase 2 notes) |

---

## Python Backend Findings

### 1. REMOVED: `pandas` (was in Pipfile `[packages]`)

No imports of `pandas` anywhere in `server/`. Pulled in `numpy` (~30 MB) and `python-dateutil` as transitive deps. Test method names like `test_update_tiers_data_aggregates_without_pandas` confirm it was deliberately removed from business logic previously.

### 2. REMOVED: `python-dateutil` (was in Pipfile `[packages]`)

No direct imports of `dateutil` in `server/`. Was only present as a transitive dep of `pandas` (now removed) and `celery` (which still brings it in).

### 3. REMOVED: `model-bakery` (was in Pipfile `[packages]`)

No imports of `model_bakery` or `baker` in any test file. All tests use direct `Player.objects.create()` calls.

### 4. REMOVED: `kombu`, `amqp` (were in Pipfile `[packages]`)

Both are transitive dependencies of `celery`. `kombu` is imported directly in `views.py` (`from kombu.exceptions import OperationalError as KombuOperationalError`) but is guaranteed present via celery. `amqp` is never imported directly. Removed from Pipfile; both remain installed as transitive deps. Retained `kombu` in requirements.txt for version pinning.

### 5. ADDED: `redis` (was missing from Pipfile)

**Critical find during QA**: Django's built-in `RedisCache` backend requires the `redis` pip package. It was present in the hand-maintained `requirements.txt` but NOT in Pipfile. Regenerating requirements.txt from Pipfile would have silently dropped it, breaking production caching. Now explicitly declared in Pipfile.

### 6. REMOVED FROM requirements.txt: `django-redis`, `wmctrl`, `numpy`, `pandas`, `model-bakery`, `amqp`

- `django-redis` — Not in Pipfile, not the configured cache backend (Django's built-in `RedisCache` is used). Stale entry.
- `wmctrl` — Linux window manager utility. Stale entry, not even installed in local venv.
- `numpy`, `pandas`, `model-bakery`, `amqp` — Removed alongside Pipfile cleanup.

### 7. NOT MOVED (deferred): `pdbpp`, `coverage`, `pytest`, `pytest-django`

These are dev/test tools listed under `[packages]` instead of `[dev-packages]`, so they ship in the production Docker image.

**Why deferred**: `run_test_suite.sh` runs tests **inside the production Docker container** via `docker compose exec -T server python manage.py test`. Moving test deps out of `[packages]` would remove them from `requirements.txt`, which the Dockerfile uses for `pip install`. Tests inside the container would break. A viable solution requires either:
- A Docker build argument (`ARG INSTALL_DEV=false`) to conditionally install dev deps
- A separate test-specific Docker service/Dockerfile
- Restructuring the test runner to not use `docker compose exec` against the prod container

This is an architectural change beyond the scope of a dependency cleanup.

### 8. OBSERVATION: `pygments` stays in `[packages]`

Since `pdbpp` remains in `[packages]` (see above), `pygments` must also stay — it's a runtime dependency of `pdbpp`.

### 9. OBSERVATION: requirements.txt is hand-maintained

The Pipfile.lock resolves to ~183 packages (due to crewai's large dependency tree: chromadb, aiohttp, onnxruntime, etc.) but requirements.txt only lists ~47 core packages. The crewai ecosystem's transitive deps are resolved and installed by pip at Docker build time via the crewai wheel, not pinned in requirements.txt. Blindly regenerating with `pipenv requirements` would inflate the file 4x. **Continue hand-maintaining requirements.txt.**

---

## JavaScript Frontend Findings

### 10. REMOVED: `axios` (was in package.json `dependencies`)

No imports of `axios` anywhere in `client/app/`. The project uses Next.js native `fetch()` via `sharedJsonFetch.ts`.

### 11. REMOVED: `spinners-react` (was in package.json `dependencies`)

No imports found. Loading states handled via CSS or inline SVG.

### 12. REMOVED: `@fortawesome/free-brands-svg-icons` (was in package.json `dependencies`)

Every `@fortawesome` import in the codebase uses `@fortawesome/free-solid-svg-icons`. Zero imports from `free-brands-svg-icons`.

### 13. REMOVED: `@fortawesome/free-regular-svg-icons` (was in package.json `dependencies`)

Zero imports. All icons use the solid variant.

---

## Verification Results

### Backend
- Docker image rebuilt successfully
- All imports verified: `from warships.data import ...`, `from warships.views import ...` — OK
- 271 tests passed; 3 pre-existing failures (unrelated: `score_best_clans` test data, gzip contract test)

### Frontend
- `npm run build` — passes locally
- 21 test suites passed, 99 tests passed; 3 pre-existing suite failures (unrelated: PlayerSearch, PlayerDetail, ClanSVG)

---

## Packages Confirmed Used

**Python**: django, requests, gunicorn, psycopg2-binary, django-cors-headers, tzdata, django-dotenv, celery, django-celery-beat, djangorestframework, crewai, langgraph, langsmith, langgraph-checkpoint-postgres, urllib3, sqlparse, redis, pdbpp, pygments, coverage, pytest, pytest-django

**JavaScript**: react, react-dom, next, d3, @fortawesome/fontawesome-svg-core, @fortawesome/react-fontawesome, @fortawesome/free-solid-svg-icons, tailwindcss, postcss, typescript, eslint, eslint-config-next, jest, jest-environment-jsdom, @testing-library/react, @testing-library/jest-dom, @playwright/test, @types/*
