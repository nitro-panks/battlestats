# Scope: prod nginx (+ gunicorn) proxy timeouts — re-apply Tier 2a hardening (2026-06-15)

_Created: 2026-06-15_
_Author role: Ops / deploy_
_Status: **SCOPING ONLY — no code written, no deploy.** Concern G of the DB-ops followup pass._
_Parent: `agents/runbooks/runbook-player-refresh-latency-2026-06-10.md` ("PROD NGINX TODO" / Tier 2a)._
_Sibling: `agents/work-items/scope-db-autovacuum-rollup-2026-06-15.md` (concern F)._

## TL;DR

- This is an **ops change, not a feature PR**: re-introduce the connect/read proxy timeouts and the gunicorn request timeout that harden the cold-path 502 failure mode. Scope it as config + a deploy step, not a code review.
- **The verify step overturned the runbook.** The runbook presents Tier 2a as *shipped* — "gunicorn `timeout=25` … ships now," "APPLIED in `bootstrap_droplet.sh`." **Both are false today.** A single bundled commit, **`bcfe232`** ("perf(ship-leaderboard): … Also lands in-progress latency/infra WIP staged on the work branch"), silently **reverted all three** Tier 2a settings while its stated purpose was unrelated:
  - gunicorn `timeout = int(os.getenv("GUNICORN_TIMEOUT_SECONDS","25"))` — **removed** from `gunicorn.conf.py`.
  - dev `server/nginx.conf` `proxy_connect_timeout`/`proxy_read_timeout` — **removed**.
  - prod `client/deploy/bootstrap_droplet.sh` `/api/` block timeouts — **removed**.
- **Live prod state confirmed (read-only `nginx -T` on the droplet):** the running `location /api/` block has **no proxy timeouts**. And the gunicorn systemd `ExecStart` passes **no `--timeout` flag** (`bootstrap_droplet.sh:242`), so with the conf-file value gone, prod gunicorn is back on the implicit **30s** default. **Tier 2a is wholly un-live.**
- **Genuinely outstanding work** = re-add the three settings (gunicorn primary, nginx secondary) **and** make them survive deploy + actually reach the running box. The nginx half has a structural gap the runbook itself names: `bootstrap_droplet.sh` is one-time provisioning that a normal deploy never re-runs — so even when those lines existed, they were **never reloaded onto the live box**. This scope fixes that, not just the revert.

## Verify-before-scope findings (the corrected picture)

| Runbook claim | Verdict | Evidence (file:line / commit) |
|---|---|---|
| gunicorn `timeout=25` "ships now / shipped via the backend deploy" (lines 3-13, 234, 246) | **FALSE — reverted, now on implicit 30s** | Added in `2afb280`; **removed in `bcfe232`** (`git show bcfe232 -- server/gunicorn.conf.py` deletes the `timeout = …` line). Current `gunicorn.conf.py` has no `timeout`. `ExecStart` has no `--timeout` (`bootstrap_droplet.sh:242`). |
| prod nginx timeouts "APPLIED in `bootstrap_droplet.sh`" (line 230) | **FALSE — reverted** | Added in `25018de`; **removed in `bcfe232`** (`git show bcfe232 -- client/deploy/bootstrap_droplet.sh` deletes the 2 lines + comment). Current `/api/` block (`bootstrap_droplet.sh:88-95`) has neither. |
| dev `server/nginx.conf` carries the timeouts (Tier 2a, line 108-110) | **FALSE — reverted** | Added in `2afb280`; removed in `bcfe232` (`-7` lines on `server/nginx.conf`). Current `server/nginx.conf:15-21` `/api/` block has neither. |
| Live prod nginx is running without the timeouts | **TRUE (confirmed live)** | `ssh root@battlestats.online 'nginx -T \| grep -iE proxy_.*timeout'` → no timeout lines under `location /api/`. (Independently expected: bootstrap is one-time, never re-run on deploy.) |
| repo-wide: any `proxy_read_timeout`/`proxy_connect_timeout` survive anywhere | **NO** | `grep -rn "proxy_read_timeout\|proxy_connect_timeout" --include=*.sh --include=*.conf .` → empty. |

**Why this matters:** the runbook's own framing ("gunicorn `timeout=25` carries Tier 2a until the nginx reload happens") assumed the gunicorn backstop was live. **It is not.** So today the cold-path 502 hardening from the 2026-06-11 Tier-2 tranche is *entirely absent in prod* — both the primary (gunicorn) and the secondary (nginx) legs. This is a regression, not just an un-done TODO.

> Note: `bcfe232` also legitimately deleted Tier 3 (the hot-player freshness sweep — see the runbook's "DELETED 2026-06-15" block) and other WIP. The Tier 3 removal was intended; the Tier 2a timeout removal in the same commit reads as **collateral** (the commit message never mentions nginx/gunicorn timeouts). Treat the timeout removal as an accidental regression to repair.

## Genuinely-outstanding work

Three settings to restore, plus a delivery fix so the nginx half actually lands:

### A. gunicorn request timeout (PRIMARY — the real 502 fix)

- **Change:** restore one line to `server/gunicorn.conf.py`:
  ```python
  timeout = int(os.getenv("GUNICORN_TIMEOUT_SECONDS", "25"))
  ```
  (verbatim revert of what `bcfe232` removed; the explanatory comment block too.)
- **Listener/file:** `server/gunicorn.conf.py` — loaded by `ExecStart=… gunicorn --config gunicorn.conf.py …` (`bootstrap_droplet.sh:242`).
- **How it lands on deploy:** `server/deploy/deploy_to_droplet.sh` rsyncs the working tree to the droplet and restarts `battlestats-gunicorn` → the conf is re-read on restart. **This one ships cleanly through a normal backend deploy** (no bootstrap dependency). This is why it's the primary fix.
- **Value choice:** 25s sits below the implicit 30s default and above the bounded request-thread WG budget (~13.5s worst case incl. adapter retries, per the runbook). Knob `GUNICORN_TIMEOUT_SECONDS` keeps it tunable.

### B. dev `server/nginx.conf` `/api/` timeouts (parity / Docker)

- **Change:** restore to the `location /api/` block (`server/nginx.conf:15-21`):
  ```nginx
  proxy_connect_timeout 5s;
  proxy_read_timeout 20s;
  ```
- **Listener/file:** `server/nginx.conf`, the Docker-compose dev edge. Lands via `docker compose up` (rebuild/restart of the nginx container). Keeps dev↔prod parity so the next person doesn't re-discover the gap.

### C. prod nginx `/api/` timeouts (SECONDARY — connect-stall hardening) + the delivery fix

- **Change:** restore to the templated `location /api/` block (`client/deploy/bootstrap_droplet.sh:88-95`):
  ```nginx
    proxy_connect_timeout 5s;
    proxy_read_timeout 20s;
  ```
- **Listener:** the prod nginx `server { listen 80 … }` `battlestats-client.conf`, `location /api/` (proxies `127.0.0.1:8888`). Only the `/api/` block needs it (the upstream WG-gated path); `/` (Next.js) and `/umami` do not.
- **THE DELIVERY GAP (must be solved, not just noted):** `bootstrap_droplet.sh` is **one-time provisioning**. `client/deploy/deploy_to_droplet.sh` does `rsync + npm build` and **never re-runs bootstrap**, so editing the template alone leaves the *running* nginx untouched (exactly why the live `nginx -T` still shows no timeouts even across the window when the lines existed in git). Pick one delivery mechanism:
  - **(c1) Operator one-shot (fastest, lowest blast radius):** edit the live `location /api/` server block on the droplet, `sudo nginx -t && sudo systemctl reload nginx`. Pairs with restoring the template (C) so a future re-provision keeps it. **Recommended** for an ops change of this size — but it is a manual prod step, so it must be logged in the runbook.
  - **(c2) Deploy-script idempotent patch:** add a small grep-or-append block to `client/deploy/deploy_to_droplet.sh` that ensures the two `proxy_*_timeout` lines are present in the live `/api/` block and reloads nginx if it patched. Durable (survives every deploy) but adds nginx-mutation to the frontend deploy path — heavier, needs care to be truly idempotent. This is the project's established pattern for prod-config knobs (per memory: "ops knobs live in deploy_to_droplet.sh grep-or-append blocks").

**Decision needed (delivery only):** c1 (manual reload now + template fix) vs c2 (deploy-script patch). The *settings* are not in question — only how they reach the box. Recommendation: **c1 now** (immediate hardening) **+ restore the template** so re-provision is correct; consider c2 later if manual reloads prove fragile.

## Smallest-safe slice

1. Restore the gunicorn `timeout` line (A) — ships via the next backend deploy, immediate primary 502 hardening.
2. Restore both nginx templates (B dev, C prod template) — parity + correct re-provision.
3. Apply the live prod nginx reload via c1 (logged) — makes the secondary leg actually live.

A, B, C-template are pure verbatim reverts of `bcfe232`'s collateral deletions — minimal, reviewable, no new behavior. The only judgment call is the delivery mechanism (c1/c2).

## Test-coverage plan

- This is config; there is no unit-testable contract. Coverage is **deploy-time verification**, not pytest:
  - After backend deploy: `ssh … 'systemctl show battlestats-gunicorn'` healthy + a stalled-upstream synthetic returns a fast clean error at ~25s, not a hung worker into a 502 (the runbook's synthetic-burst check, "zero 502s").
  - After nginx reload: `ssh root@battlestats.online 'nginx -T | grep -iE "proxy_(read|connect)_timeout"'` shows both lines under `location /api/` (this exact command was the verify probe for this scope — it currently returns nothing).
- Optional regression guard: a tiny repo test / CI grep asserting the three settings are present in `gunicorn.conf.py`, `server/nginx.conf`, and `bootstrap_droplet.sh` — cheap insurance against a future bundled commit silently reverting them again (which is precisely how this regressed).

## Risks & cross-subsystem interactions

- **Live nginx reload (c1) touches prod.** `nginx -t` first; `reload` (not restart) is zero-downtime. Per CLAUDE.md autonomy rules a config reload is allowed, but it is a manual prod mutation — log it in the runbook with the diff.
- **`apt`/`needrestart` caveat (memory `reference_droplet_needrestart_apt`):** if any package work is bundled, prefix `NEEDRESTART_MODE=l` — not expected here (pure nginx reload), but flagged since this is a droplet op.
- **Shared droplet (memory `shared_droplet_battlestats_oturu`):** `battlestats.online` also hosts oturu. A bad nginx config that fails `nginx -t` would block reload for the whole box — hence `nginx -t` before reload is mandatory.
- **Timeout interaction:** nginx `proxy_read_timeout 20s` is intentionally *below* gunicorn `timeout 25s` — nginx sheds the stalled connection before gunicorn recycles the worker. Keep that ordering if values are retuned (nginx read < gunicorn timeout < any longer background path). Background Celery WG calls use a separate 20s budget and are unaffected (these timeouts are on the gunicorn request path / `/api/` edge only).
- **No Beat / kill-switch interaction.** No queue routing, no periodic task, no DB.

## Out of scope

- Tier 1 (client poll cadence / 5xx retry) and Tier 2b/2c (cold-path WG bounding, queue routing) — separate tranches; the runbook marks them implemented.
- Tier 3 (hot-player freshness sweep) — deliberately DELETED 2026-06-15; re-introducing it is a full re-implementation, explicitly not this scope.
- Re-running full `bootstrap_droplet.sh` (re-provisioning) — out of scope; use c1/c2 instead.
- Retuning the timeout *values* (25s / 20s / 5s) — restore the documented values; a retune is a follow-up if metrics warrant.

## Open questions for the user

1. **Delivery mechanism: c1 (manual reload now + template fix) or c2 (idempotent deploy-script patch)?** (Settings are settled; only how they land.)
2. Want the **regression-guard grep test** added so a future bundled commit can't silently revert these a third time?
3. Should the **runbook's Tier-2a "APPLIED/ships now" language be corrected** as part of this slice (recommended — it currently overstates live state), or tracked separately?

## Related

- `agents/runbooks/runbook-player-refresh-latency-2026-06-10.md` — parent; "PROD NGINX (Tier 2a)" block (lines 230-249) is the now-stale claim this corrects.
- Commits: `2afb280` (added gunicorn + dev nginx timeouts), `25018de` (added prod bootstrap timeouts), **`bcfe232`** (reverted all three as WIP collateral).
- Memories: `reference_deploy_ships_working_tree` (deploy rsyncs the working tree), `project_hot_players_cap_cost_model` (ops knobs live in deploy_to_droplet.sh grep-or-append blocks), `reference_droplet_needrestart_apt`, `shared_droplet_battlestats_oturu`.
</content>
