# Runbook: Stale Symlink Diagnosis And Resolution

_Created: 2026-04-05_

_Status: Resolved — droplet remediated, deploy script fixed, docs reconciled_

## Purpose

Diagnose and resolve the recurring backend deploy failure where the second SSH block aborts before completing release setup, leaving releases without `.env` symlinks, wrong ownership, no `collectstatic`, and no cleanup of old releases.

## Root Cause

Commit `92b4cc7` ("fix: harden rabbitmq deploy and startup path", 2026-04-04 17:40) introduced `configure_local_rabbitmq()` into the deploy script's second SSH heredoc. This function runs under `set -euo pipefail` and executes early in the block — before `.env` symlink creation (line 364), `collectstatic` (line 375), `chown` (line 378), `activate_release` (line 381), and release cleanup (line 462).

When `configure_local_rabbitmq()` or any preceding step fails, the entire SSH block aborts. Since deploy `20260404173858` (the first deploy of this commit), **17 consecutive deploys** failed to complete the second SSH block. Evidence:

| Release | `.env` symlinks | `staticfiles` | File owner | Status |
|---|---|---|---|---|
| `20260404142255` - `20260404172112` (5 releases) | Present | N/A | `battlestats` | Fully deployed |
| `20260404173858` - `20260405103659` (17 releases) | Missing | Missing | UID 1000 | Incomplete — second SSH block aborted |

The `current` symlink was manually adjusted by operators or agents after each failed deploy rather than the deploy script completing normally.

## What Failed In The Deploy Pipeline

The backend deploy's second SSH block (lines 119-463 of `server/deploy/deploy_to_droplet.sh`) runs these steps sequentially under `set -euo pipefail`:

```
1. Copy env files to /etc/                    ← lines 122-128
2. Env file manipulation (sed, grep)          ← lines 130-203
3. configure_local_rabbitmq()                 ← line 340  ← FAILURE POINT
4. set_env_value calls                        ← lines 342-363
5. ln -sfn .env symlinks                      ← lines 364-365  ← NEVER REACHED
6. pip install                                ← lines 367-371  ← NEVER REACHED
7. manage.py migrate/collectstatic/check      ← lines 373-376  ← NEVER REACHED
8. chown -R battlestats:battlestats           ← line 378        ← NEVER REACHED
9. activate_release()                         ← line 381        ← NEVER REACHED
10. Write systemd units                       ← lines 383-447
11. Restart services                          ← line 452
12. verify_broker_connection                  ← line 453
13. materialize_best_player_snapshots         ← line 454
14. Release cleanup                           ← line 462        ← NEVER REACHED
```

When step 3 (or any step before 5) fails, steps 5-14 are skipped entirely. The rsync has already transferred code to the release directory, but the release is left in an incomplete state.

## Consequences

1. **No `.env` symlinks**: Management commands run via the deploy script (migrate, collectstatic, check) cannot load database credentials via dotenv. Services started via systemd are unaffected because they load env via `EnvironmentFile=/etc/battlestats-server.env`.
2. **No `collectstatic`**: Django admin and DRF static assets are not collected.
3. **Wrong ownership**: Files remain owned by UID 1000 (the deployer's local UID) instead of `battlestats`.
4. **No activation**: `current` symlink stays on the previous release. Operators must manually activate.
5. **No cleanup**: Old releases accumulate. The droplet had 22 release directories (should be 5).
6. **No post-restart drift check**: The drift check at lines 455-459 never runs, so symlink issues go undetected by the script.

## Droplet Remediation (Completed 2026-04-05)

The following fixes were applied directly to the active release (`20260405103659`) on the production droplet:

1. Created `.env` → `/etc/battlestats-server.env` symlink
2. Created `.env.secrets` → `/etc/battlestats-server.secrets.env` symlink
3. Created `staticfiles` directory
4. Ran `collectstatic` (163 static files)
5. Fixed ownership: `chown -R battlestats:battlestats`
6. Cleaned up stale releases: 22 → 5
7. Django check passes clean (0 issues, 0 silenced)
8. All 9 services active
9. API smoke: landing 200, clans 200, player detail 200

## Deploy Script Fix (Pending)

The deploy script needs structural hardening so that a failure in `configure_local_rabbitmq()` does not skip the entire release setup. Two approaches:

### Option A: Move `configure_local_rabbitmq()` before `activate_release()` but after essential setup

Reorder the second SSH block so that `.env` symlinks, pip install, migrate, collectstatic, chown, and activation run BEFORE the optional RabbitMQ configuration. This way, a RabbitMQ failure doesn't prevent the core deploy from completing.

Proposed order:

```
1. Copy env files to /etc/
2. Env file manipulation
3. set_env_value calls
4. ln -sfn .env symlinks                      ← must succeed for manage.py
5. pip install
6. manage.py migrate/collectstatic/check
7. chown -R
8. activate_release()
9. Write systemd units
10. configure_local_rabbitmq()                ← moved after core deploy
11. Restart services (including rabbitmq)
12. verify_broker_connection
13. materialize_best_player_snapshots
14. Post-restart drift check
15. Release cleanup
```

### Option B: Isolate `configure_local_rabbitmq()` with error handling

Wrap the RabbitMQ setup so it logs failures but doesn't abort the deploy:

```bash
if ! configure_local_rabbitmq; then
  echo "WARNING: RabbitMQ configuration failed — broker may need manual setup" >&2
fi
```

This is less clean because it could leave RabbitMQ in a broken state without aborting.

### Recommendation

**Option A** is preferred. The RabbitMQ configuration is a broker-management concern that should not gate release setup. The deploy script should guarantee that every release directory is fully set up (`.env` symlinks, correct ownership, static assets collected, migrations applied) before attempting optional service configuration.

Additionally, the release cleanup step should be unconditional — move it outside the `set -e` block or run it in a trap so that failed deploys don't accumulate stale directories.

## Documentation Reconciliation (Pending)

The post-deploy runbook (`runbook-post-deploy-post-bounce-operations-2026-04-05.md`) contains stale content:

1. **Line 92**: Says "Required a manual fix during the CB rollout; verify explicitly every time" — the deploy now verifies automatically and exits non-zero on failure.
2. **Line 346**: Says client deploy needs atomic activation hardening — already implemented.
3. **Line 380**: Says client deploy "still uses an unchecked `ln -sfn`" — already fixed.
4. **Lines 281-353**: Describes a phased implementation plan that is already complete.

## Verification Commands

### Check release completeness

```bash
ssh root@battlestats.online '
  for r in /opt/battlestats-server/releases/*/; do
    name=$(basename "$r")
    has_env="no"; test -e "${r}server/.env" && has_env="yes"
    has_secrets="no"; test -e "${r}server/.env.secrets" && has_secrets="yes"
    has_static="no"; test -d "${r}server/static" && has_static="yes"
    owner=$(stat -c "%U" "${r}server/manage.py" 2>/dev/null || echo "?")
    echo "${name}: .env=${has_env} .env.secrets=${has_secrets} static=${has_static} owner=${owner}"
  done
'
```

### Repair an incomplete release

```bash
ssh root@battlestats.online '
  RELEASE=/opt/battlestats-server/releases/<RELEASE_ID>
  ln -sfn /etc/battlestats-server.env "${RELEASE}/server/.env"
  ln -sfn /etc/battlestats-server.secrets.env "${RELEASE}/server/.env.secrets"
  mkdir -p "${RELEASE}/server/staticfiles"
  chown -R battlestats:battlestats "${RELEASE}"
  cd "${RELEASE}/server"
  set -a; source /etc/battlestats-server.env; source /etc/battlestats-server.secrets.env; set +a
  /opt/battlestats-server/venv/bin/python manage.py collectstatic --noinput
  /opt/battlestats-server/venv/bin/python manage.py check
'
```

### Full health check

```bash
ssh root@battlestats.online '
  readlink -f /opt/battlestats-server/current
  systemctl is-active battlestats-gunicorn battlestats-celery battlestats-celery-hydration battlestats-celery-background battlestats-beat redis-server rabbitmq-server battlestats-client nginx
  curl -s -o /dev/null -w "API: %{http_code}\n" http://127.0.0.1:8888/api/landing/players/?mode=best&limit=1
'
```

## Related Docs

- `runbook-post-deploy-post-bounce-operations-2026-04-05.md` — post-deploy checklist (contains stale content to reconcile)
- `runbook-landing-best-player-subsort-materialization-2026-04-05.md` — documents the original symlink failures
- `runbook-backend-droplet-deploy.md` — backend deploy behavior
- `runbook-client-droplet-deploy.md` — client deploy behavior
- `agents/runbooks/archive/runbook-incident-rabbitmq-compromise-2026-04-04.md` — the incident that drove the RabbitMQ hardening commit
