# Runbook: Database Target Switching

_Last updated: 2026-03-19_

_Status: Active operations reference_

## Goal

Switch the running battlestats backend between the managed DigitalOcean Postgres database and the optional local Docker Postgres database with one command.

## Command

From the repository root:

```bash
./server/scripts/switch_db_target.sh cloud
./server/scripts/switch_db_target.sh local
```

Optional validation flags:

```bash
./server/scripts/switch_db_target.sh cloud --dry-run
./server/scripts/switch_db_target.sh local --skip-check
```

## Files Used

Non-secret target definitions:

- `server/.env.cloud`
- `server/.env.local`

Secret target definitions:

- `server/.env.secrets.cloud`
- `server/.env.secrets.local`

Active runtime files replaced by the switcher:

- `server/.env`
- `server/.env.secrets`

## What The Script Does

1. Copies the selected target files into the active `server/.env` and `server/.env.secrets` paths.
2. For `local`, starts the optional Docker Postgres service with the `local-db` profile.
3. Restarts the Django, Celery worker, and Celery beat services.
4. For `cloud`, stops the optional local Postgres container if it is running.
5. Runs `python manage.py check` inside the Django container unless `--skip-check` is passed.
6. Prints the current backend service status and active DB connection fields.

## Expected Modes

Cloud mode:

- `DB_HOST=db-postgresql-nyc3-11231-do-user-8591796-0.m.db.ondigitalocean.com`
- `DB_PORT=25060`
- `DB_NAME=defaultdb`
- `DB_USER=doadmin`
- `DB_SSLMODE=require`

Local mode:

- `DB_HOST=db`
- `DB_PORT=5432`
- `DB_NAME=battlestats`
- `DB_USER=django`
- no SSL fields required

## Validation

1. Dry-run the target switch first if you changed any env target files:
   - `./server/scripts/switch_db_target.sh cloud --dry-run`
   - `./server/scripts/switch_db_target.sh local --dry-run`
2. Run the real switch.
3. Verify the backend:
   - `docker compose exec -T server python manage.py check`
   - `curl -sSf http://localhost:8888/api/player/Mebuki/`

## Maintenance Notes

- Keep `server/.env.secrets.cloud` and `server/.env.secrets.local` machine-local and rotated when passwords change.
- If the managed DB CA certificate changes, update `server/ca-certificate.crt` and keep `DB_SSLROOTCERT=ca-certificate.crt` in the cloud target file.
- If you retire the local Postgres path, remove `server/.env.local`, `server/.env.secrets.local`, and the `local-db` Compose profile.