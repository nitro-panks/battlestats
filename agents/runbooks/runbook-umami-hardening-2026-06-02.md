# Runbook: Umami analytics attack-surface hardening

_Created: 2026-06-02_
_Context: The user asked whether Umami needed to live on the droplet (idea: move it to a local server) out of attack-surface concern. Investigation showed a local move would not shrink the part that matters — the collection endpoint (`/umami/script.js` + `/umami/api/send`) must stay publicly reachable for tracking, on the droplet or anywhere. The real exposure was elsewhere. This runbook documents the four hardening fixes applied instead._
_Status: **DONE** — all four fixes applied and verified live on production 2026-06-02. No version-footer bump (umami is a separate app from the battlestats client; `release.sh`/client rebuild not involved)._

## What Umami is here

- Self-hosted Umami (Next.js, `output: standalone`) on the droplet at `/opt/umami`, systemd unit `umami.service`, bound to `127.0.0.1:3002`, proxied by nginx under `/umami/`.
- Uses the managed Postgres cluster (`db-postgresql-nyc3-11231`), separate `umami` database (~13 MB, 10 tables).
- Tracking script loaded same-origin from `client/app/layout.tsx`: `<script src="/umami/script.js" data-website-id=...>`; beacons POST to `/umami/api/send`.

## Why "move it local" was the wrong fix

Umami has two halves: **collection** (`script.js` + `api/send`, hit by every visitor's browser — must be public) and the **dashboard** (admin UI/login — has no reason to be public). A box behind home NAT can't receive collection beacons without re-exposing itself publicly, so relocation buys nothing for the surface that's actually internet-facing. The leverage is in (a) shrinking the dashboard's public exposure and (b) capping the blast radius of a umami compromise — neither of which depends on where the process runs.

## Findings & fixes (priority order)

### 1. Umami connected as `doadmin` — full-cluster blast radius (CRITICAL)

`/opt/umami/.env` `DATABASE_URL` used `doadmin`, the DO managed-PG cluster superuser. A umami breach (app CVE or weak login) would hand over the master DB credential with access to `defaultdb` — all ~194K players' data — not just the 13 MB analytics DB.

**Fix — least-privilege scoped role. Use the per-object reassignment below, NOT `REASSIGN OWNED` (see the hazard note):**
```sql
-- as doadmin (backup taken first: pg_dump -Fc, 474 KB, 10 table-data entries)
CREATE ROLE umami_app WITH LOGIN PASSWORD '<48-hex>';
GRANT CONNECT ON DATABASE umami TO umami_app;
ALTER DATABASE umami OWNER TO umami_app;          -- so it can run prisma migrations (DDL)
\c umami
-- hand the existing tables/sequences over WITHOUT touching shared objects:
DO $$ DECLARE r record; BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO umami_app', r.tablename);
  END LOOP;
  FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname='public' LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO umami_app', r.sequencename);
  END LOOP;
END $$;
```
- Verified `umami_app` can run DDL (`CREATE TABLE _privtest; DROP TABLE`) **before** the v2.20.2 upgrade, per advisor guidance.
- `.env` repointed to `umami_app`; old doadmin `.env` saved to `/opt/umami/.env.doadmin.bak`; rollback URL stashed at `/root/.umami_doadmin_url`.
- Scoped creds persisted to `/etc/battlestats-server.secrets.env` as `UMAMI_DB_USER` / `UMAMI_DB_PASSWORD` for bootstrap reuse.
- `doadmin` stays cluster superuser; it's only removed from umami's *connection string*.
- Note: PG18 server logged `MD5-encrypted password` deprecation warning on `CREATE ROLE` (cluster `password_encryption=md5`). Cosmetic for a 48-hex secret; revisit if the cluster moves to SCRAM.

> **HAZARD — what was actually run, and the leak it caused.** The first pass used `REASSIGN OWNED BY doadmin TO umami_app` connected to the `umami` DB, on the wrong assumption that it's connected-DB-scoped. It is **not** for *shared* objects: `REASSIGN OWNED` reassigns databases/tablespaces cluster-wide (unlike `DROP OWNED`, which deliberately skips them). The post-check
> ```sql
> SELECT datname, pg_get_userbyid(datdba) FROM pg_database WHERE NOT datistemplate;
> ```
> showed **`test_defaultdb` had been reassigned to `umami_app`** — a leak that would let a umami breach `DROP DATABASE test_defaultdb`. `defaultdb` (the ~23 GB player DB) was verified **still owned by `doadmin`** (not leaked), and there is no separate oturu database in this cluster, so the live blast-radius exposure was limited to the ephemeral pytest DB. Remediated with `ALTER DATABASE test_defaultdb OWNER TO doadmin;` and re-verified that **only `umami` is owned by `umami_app`**. Always run the cluster-wide ownership check after any `REASSIGN OWNED`, and prefer the per-object loop above.

### 2. Dashboard had no gate beyond umami's own login

The whole `/umami/` prefix (login form + admin API) was publicly reachable.

**Fix — nginx IP allowlist (final design).** The dashboard *and* admin API are restricted to the home IP (`130.44.131.215` as of 2026-06-02); only the collection endpoints stay public:
- `location = /umami/script.js` → PUBLIC (tracker)
- `location = /umami/api/send` → PUBLIC (collection beacon)
- `location /umami` → `allow 130.44.131.215; deny all;` (dashboard UI + admin API)

Rotate the `allow` line if the home IP changes (`UMAMI_ALLOW_IP` in the bootstrap). The home IP was confirmed against the droplet's nginx access log (312 recent `/umami` hits, all from that address; no IPv6) before applying, to avoid lockout.

Verified: from the home IP — `/umami/` 308 (umami redirect, reachable, no prompt), `/api/websites` + `/api/auth/login` reach umami (its own JSON 401); from any other IP — `/umami/` **403** and `/api/websites` **403**; `script.js` 200 and `/api/send` reachable from anywhere.

> **Why not HTTP Basic auth (failed first attempt).** The first cut used Basic auth on the `/umami` prefix. It broke login with `JSON.parse: unexpected character at line 1 column 1`. Cause: umami authenticates its API with an `Authorization: Bearer <token>` header; HTTP Basic auth **also** uses the `Authorization` header, and a request carries only one. After login, umami's `Bearer` header replaced the browser's stored Basic credential on every `/api` call, so nginx returned its **401 HTML page**, which umami's JS `JSON.parse()`d → the error (confirmed: `curl -H 'Authorization: Bearer x' /umami/api/auth/verify` → `text/html` `<html>…401`). An interim fix exempted `/umami/api/` from Basic auth (login worked, but the admin API was then ungated). The user chose the IP allowlist instead — it doesn't touch the `Authorization` header, so it cleanly protects the admin API too, at the cost of location independence (home-network only). The retired htpasswd is at `/root/nginx-bak-2026-06-02/htpasswd_umami.retired`.

**Latent bug found & fixed along the way:** `/etc/nginx/sites-enabled/battlestats-client.conf` was a stale **copy**, not a symlink to `sites-available/` (unlike `oturu.conf`/`metro.conf`). All nginx edits — including this script's own — had silently never gone live, and the copy had rolled-back CSP/HSTS (`Content-Security-Policy-Report-Only`, `max-age=3600`) vs the hardened live-enabled values. Resolved by applying the umami split to the hardened file, then converging `sites-available` to it and replacing `sites-enabled` with a symlink. **Always confirm `sites-enabled/*` are symlinks before editing `sites-available`.**

### 3. Default `admin/umami` credentials

Already rotated — `POST /umami/api/auth/login` with the defaults returns 401. No action needed.

### 4. Patch cadence: v2.16.1 → v2.20.2

Latest upstream is **v3.1.0** (Apr 2026), which carries IDOR fixes — but v3 needs Node 22 (droplet is Node 20) and a major Prisma migration. User chose the safe in-major bump to **v2.20.2** (dependency CVE fixes: tar/ajv/jws/brace-expansion/next; Node-20 compatible). The v3-only IDOR fixes are deferred; real risk is low on a single-admin instance with no share tokens (see the correction note in #2 — the API gate idea didn't survive the Bearer/Basic collision). v3 deferred as a separate, snapshot-backed task.

Upgrade ran via the existing git checkout in `/opt/umami` (`fetch --depth 1 origin tag v2.20.2` → checkout → `npm install --legacy-peer-deps` → `npm run build` → restart). Migrations ran as `umami_app` (13 applied, no permission errors) — confirming the scoped role's DDL works end to end.

Aside: installing the PG18 client (the managed server is 18.4; the droplet's stock `pg_dump` 16 refuses a newer server) triggered `needrestart` to auto-restart the battlestats services. They self-recovered (Restart=always + acks_late). For future apt on the droplet, prefix `NEEDRESTART_MODE=l` to avoid the surprise restart.

## Secrets / artifacts on the droplet

- `/root/umami_backup_2026-06-02.dump` — pre-change `pg_dump -Fc` of the umami DB.
- `/root/.umami_app_pw`, `/root/.umami_basicauth_pw`, `/root/.umami_doadmin_url` — chmod 600.
- `/opt/umami/.env.doadmin.bak`, `/root/nginx-bak-2026-06-02/` — rollback copies.
- `UMAMI_DB_USER` / `UMAMI_DB_PASSWORD` in `/etc/battlestats-server.secrets.env`.

## Rollback

- DB role: restore `DATABASE_URL` from `/opt/umami/.env.doadmin.bak`, `systemctl restart umami`. (Ownership change is harmless to leave; doadmin is still superuser.)
- nginx: `sites-available` backups in `/root/nginx-bak-2026-06-02/`; `nginx -t && systemctl reload nginx`.
- version: `git -C /opt/umami checkout v2.16.1 && npm install --legacy-peer-deps && npm run build && systemctl restart umami`.

## Repo reconciliation

`umami/deploy/bootstrap_umami.sh` updated to match reality: version pinned to v2.20.2 (overridable via `UMAMI_VERSION`), DB URL built from the scoped `UMAMI_DB_USER`/`UMAMI_DB_PASSWORD` (refuses to run if unset), nginx auth split + `openssl`-generated htpasswd, and a guard that warns if `sites-enabled` is a copy rather than a symlink.
