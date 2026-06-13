# Runbook: Dependabot Vulnerability Cleanup

_Created: 2026-04-28_
_Context: GitHub reported 18 open Dependabot alerts on `main` after the landing-warm hotfix push (2026-04-28 19:18 UTC). Plan only — execute in a follow-up tranche._
_Status: Inventory complete; fixes not yet applied._

## Purpose

Drive the open Dependabot alert count on `nitro-panks/battlestats:main` to zero, scoped to packages that actually ship. Most reported alerts are duplicated across `Pipfile.lock` (not deployed), `requirements.txt` (deployed always), and `requirements-agentic.txt` (deployed only with `DEPLOY_AGENTIC_RUNTIME=1`); the plan below dedupes by deploy target so the work matches actual exposure rather than alert count.

## What deploys, what doesn't

`server/deploy/deploy_to_droplet.sh:426-428` is the source of truth:

```bash
"${APP_ROOT}/venv/bin/pip" install --no-cache-dir -r "${REMOTE_RELEASE}/server/requirements.txt"
[ "$DEPLOY_AGENTIC_RUNTIME" = "1" ] && \
  "${APP_ROOT}/venv/bin/pip" install --no-cache-dir -r "${REMOTE_RELEASE}/server/requirements-agentic.txt"
```

- **Always installed:** `server/requirements.txt`.
- **Conditionally installed:** `server/requirements-agentic.txt` (off on the production droplet today — `DEPLOY_AGENTIC_RUNTIME=0`).
- **Never installed:** `server/Pipfile` and `server/Pipfile.lock`. They exist for local pipenv flows; the droplet does not run pipenv. Alerts pinned only to `Pipfile.lock` are scanner noise, **not production exposure**.
- **Frontend:** `client/package-lock.json` is what the build step locks against. Alerts here are real production exposure on `battlestats.online`.

## Inventory (as of 2026-04-28 19:25 UTC)

Deduplicated by package; "Manifests" lists every file the alert appeared in.

### High severity (real production exposure)

| # | Pkg | Eco | Fixed in | Manifests | Notes |
|---|---|---|---|---|---|
| 155 | `next` | npm | 16.2.3 | `client/package-lock.json` | DoS via Server Components. Direct dep (`client/package.json:19` `"next": "^16.2.0"`). **Highest priority — production frontend.** |
| 157 | `pillow` | pip | 12.2.0 | `server/Pipfile.lock` only | FITS GZIP decompression bomb. **Not deployed** (not in `requirements.txt` or `-agentic.txt`). Closes by regenerating or removing `Pipfile.lock`. |

### Medium severity

| # | Pkg | Eco | Fixed in | Manifests | Notes |
|---|---|---|---|---|---|
| 164/161/160 | `langsmith` | pip | 0.7.31 | `server/Pipfile`, `requirements-agentic.txt`, `Pipfile.lock` | Direct dep at `langsmith==0.7.14` in `requirements-agentic.txt:5`. Token-redaction bypass. Only ships when `DEPLOY_AGENTIC_RUNTIME=1` — currently off in prod, so dormant. Bump anyway for cleanliness. |
| 163 | `postcss` | npm | 8.5.10 | `client/package-lock.json` | XSS via unescaped `</style>`. Direct dep (`client/package.json:35`). Real frontend exposure. |
| 162 | `python-dotenv` | pip | 1.2.2 | `Pipfile.lock` only | Symlink TOCTOU. **Not deployed.** Closes by regen/removal. |
| 159 | `python-multipart` | pip | 0.0.26 | `Pipfile.lock` only | DoS via large preamble. **Not deployed.** |
| 158/156 | `pytest` | pip | 9.0.3 | `requirements.txt:32`, `Pipfile.lock` | tmpdir handling. **Test-only**, but is in deployed `requirements.txt` (kept intentionally for `pytest`-on-droplet release-gate runs). Bump direct pin from `8.3.2` → `9.0.3`; mind `pytest-django==4.8.0` compat (test in CI). |
| 153 | `langchain-core` | pip | 1.2.28 | `Pipfile.lock` only | f-string template validation. **Not deployed** (transitive of langchain stack outside `requirements-agentic.txt`'s pinned set). |
| 152 | `cryptography` | pip | 46.0.7 | `Pipfile.lock` only | Buffer overflow on non-contiguous buffers. **Not deployed.** |
| 148 | `diskcache` | pip | **no fix** | `Pipfile.lock` only | Unsafe pickle deserialization. **Not deployed; no upstream fix.** Closes when `Pipfile.lock` is regenerated/removed; alternative is GHSA dismissal as "not affected." |
| 142 | `requests` | pip | 2.33.0 | `Pipfile.lock` | Already pinned at `2.33.0` in `requirements.txt:39`. Lock-file lag only. |

### Low severity

| # | Pkg | Eco | Fixed in | Manifests | Notes |
|---|---|---|---|---|---|
| 154 | `uv` | pip | 0.11.6 | `Pipfile.lock` only | RECORD-entry file deletion. **Not deployed.** |
| 151/150/149 | `Pygments` | pip | 2.20.0 | `Pipfile`, `requirements.txt:30`, `Pipfile.lock` | ReDoS in GUID regex. Direct pin at `2.19.2`. Pulled by `pdbpp`; bump direct. |

## Plan

Three independent vertical slices. Each ends with a green release gate, a deploy where applicable, and a verification step. Branch off `main` for each.

### Slice 1 — Frontend (next + postcss). Highest priority.

1. Branch `chore/dependabot-frontend-2026-04-28` off `main`.
2. `cd client && npm install next@^16.2.3 postcss@^8.5.10` (postcss is a direct dep at `package.json:35`; npm will refresh `package-lock.json`).
3. `npm run lint && npm test && npm run build` — full frontend gate.
4. Smoke-test locally with `npm run dev` against the droplet API; visit `/`, `/player/lil_boots`, `/clan/1000067803-rain` to confirm nothing regressed.
5. Commit, push, open PR, merge.
6. `./client/deploy/deploy_to_droplet.sh battlestats.online`.
7. Verify alert #155 and #163 close on GitHub within ~10 min.

Risk: `next 16.2.3` is a patch within the same `^16.2.0` range we already accept. Compatibility risk is minimal. `postcss 8.5.10` similarly patch-level.

### Slice 2 — Backend deployed pins (`requirements.txt` + `-agentic.txt`).

1. Branch `chore/dependabot-backend-pins-2026-04-28` off `main`.
2. Edit `server/requirements.txt`:
   - `pytest==8.3.2` → `pytest==9.0.3`
   - `pygments==2.19.2` → `pygments==2.20.0`
3. Edit `server/requirements-agentic.txt`:
   - `langsmith==0.7.14` → `langsmith==0.7.31`
4. `cd server && pip install -r requirements.txt` in a clean venv (or use the existing one) and rerun the lean release gate:
   ```
   python -m pytest --nomigrations \
     warships/tests/test_views.py \
     warships/tests/test_landing.py \
     warships/tests/test_realm_isolation.py \
     warships/tests/test_data_product_contracts.py \
     -x --tb=short
   ```
   Watch for `pytest-django==4.8.0` compat issues with `pytest==9.0.3` — if any, either bump `pytest-django` to its current matching minor, or stay on a `pytest 8.x` line that still has the tmpdir fix backported. Note: GHSA-6w46-j5rx-g56g lists 9.0.3 as the patched version; there is no 8.x backport, so the realistic options are upgrade-and-retest, or accept and dismiss.
5. Commit, push, open PR, merge.
6. `./server/deploy/deploy_to_droplet.sh battlestats.online`. (Agentic extras only redeploy if you also pass `DEPLOY_AGENTIC_RUNTIME=1`; default deploy will *not* pull `langsmith==0.7.31`. Either flip the flag for one deploy or accept that #161 stays open until the agentic stack ships.)
7. Verify alerts #156, #150, #161 close.

Risk: `pytest 9.x` is a major bump from `8.x` and is the riskiest part of this slice. Stage by running the gate locally first; revert via PR if any test pattern breaks (custom fixtures, deprecated APIs).

### Slice 3 — Pipfile.lock cleanup (closes 13 alerts at once).

`server/Pipfile.lock` is not consumed by the production deploy. Two paths:

**Option A — Regenerate (preferred if pipenv is still used locally).**
1. Branch `chore/dependabot-pipfile-regen-2026-04-28` off `main`.
2. `cd server && pipenv lock --clear` (or whatever local invocation is current). This pulls fresh top-of-tree resolutions for the transitive deps that are out of date.
3. Verify the regenerated lockfile by running tests against a fresh `pipenv install --dev` venv.
4. Commit only the regenerated `Pipfile.lock` (and `Pipfile` if any constraints were tightened).
5. Push, open PR, merge.

**Option B — Retire pipenv entirely.**
1. Branch `chore/retire-pipenv-2026-04-28` off `main`.
2. `git rm server/Pipfile server/Pipfile.lock`.
3. Add `Pipfile*` to `.gitignore` so future local pipenv use doesn't reintroduce.
4. Commit with message explaining: deploy uses `pip -r requirements.txt` exclusively, pipenv is unused operationally, lockfile drift is the only source of these alerts.
5. Push, open PR, merge.

Either option closes alerts #157, #162, #159, #153, #152, #148, #142, #154, #151, #149, plus the duplicate-manifest entries for langsmith/pytest/pygments/requests (which Slice 2 also closes via the deployed manifests).

Recommendation: **Option B** unless `pipenv install --dev` is part of someone's local flow. The deploy path is `pip -r requirements.txt`; carrying a parallel `Pipfile.lock` is an alert generator with no operational value. Confirm with the user before retiring — if they use it for local venv setup, fall back to Option A.

## Open question for the user before executing

Slice 3 hinges on whether `Pipfile`/`Pipfile.lock` are still part of the local dev flow. If yes → Option A. If no → Option B. The runbook does not assume; the executor must ask.

## Verification

After all three slices land:

1. `gh api repos/nitro-panks/battlestats/dependabot/alerts --paginate -q '.[] | select(.state=="open") | .number' | wc -l` → expected `0`.
2. Healthcheck stays green: tail `/home/august/code/archive/battlestats/logs/healthcheck/healthcheck.log` for one cycle. No new failures.
3. `./server/scripts/check_enrichment_crawler.sh battlestats.online` confirms the backend deploy didn't disturb the enrichment loop.
4. Frontend smoke: load `/`, a player page, and a clan page on `battlestats.online` and confirm no console errors.

## Doctrine pre-commit checklist (per `agents/knowledge/agentic-team-doctrine.json`)

- **Documentation review:** N/A; no behavior changes.
- **Doc-vs-code reconciliation:** N/A.
- **Test coverage:** existing release gate covers the bumps; no new tests needed unless `pytest 9.x` requires fixture rewrites.
- **Runbook archiving:** archive **this** runbook (`runbook-archive` skill) once all 18 alerts close and the verification steps pass.
- **Contract safety:** no API or payload changes.
- **Runbook reconciliation:** update the **Status** field at the top of this file to `Resolved` and add a closing paragraph noting the merged PRs and the GitHub alert dashboard hitting zero before archiving.

## References

- Deploy script paths: `server/deploy/deploy_to_droplet.sh:426-428` (pip install), `client/deploy/deploy_to_droplet.sh` (frontend).
- Manifest paths: `server/requirements.txt`, `server/requirements-agentic.txt`, `server/Pipfile.lock`, `client/package.json`, `client/package-lock.json`.
- GitHub Dependabot alerts: https://github.com/nitro-panks/battlestats/security/dependabot

## Out of scope

- Audit logging / SAST / SCA tooling beyond Dependabot. The current cadence is "address when GitHub flags." If alert volume becomes a regular load, file a follow-up to add a CI job that fails on new high-severity advisories.
- Auto-merge configuration for Dependabot PRs. Worth considering for patch-level npm/pip bumps that pass the release gate, but a separate decision.
