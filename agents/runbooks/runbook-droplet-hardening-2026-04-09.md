# Runbook: Droplet Infrastructure Hardening

_Created: 2026-04-09_
_Context: Manual security audit of droplet infrastructure (complement to Wapiti web scan in `runbook-security-audit-2026-04-05.md`)_
_QA: Findings verified via SSH against live droplet on 2026-04-09._

## Purpose

Address four infrastructure-level security findings discovered during a manual droplet audit. These are outside the scope of the Wapiti web scanner and cover SSH hardening, TLS protocol cleanup, and service binding.

## Findings

### H1: SSH Password Authentication Not Disabled

**Risk: High**

`/etc/ssh/sshd_config` has `PermitRootLogin yes` with no explicit `PasswordAuthentication no`. Root is the only login user. **24,934 failed SSH attempts in the last 7 days** confirm active brute-force scanning.

Both authorized SSH keys are accounted for (`august@PowerSpec-3080`, `x@battlestats`). Key-based auth is already the intended access method — password auth is simply not disabled.

**Remediation:**

```bash
# In /etc/ssh/sshd_config, set:
PermitRootLogin prohibit-password
PasswordAuthentication no
PermitEmptyPasswords no
```

Then `systemctl restart ssh`.

**Verification:**

```bash
# From local machine — should be rejected immediately:
ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password root@battlestats.online
# Expected: Permission denied (publickey).

# Key-based auth should still work:
ssh root@battlestats.online 'echo ok'
```

**Rollback:** If locked out, use DigitalOcean console to revert sshd_config.

### H2: TLS 1.0/1.1 Enabled in Nginx Server Block

**Risk: Medium**

The Let's Encrypt snippet (`/etc/letsencrypt/options-ssl-nginx.conf`) correctly specifies `ssl_protocols TLSv1.2 TLSv1.3`. However, one `server` block in `/etc/nginx/sites-enabled/battlestats` contains an explicit override:

```nginx
ssl_protocols TLSv1 TLSv1.1 TLSv1.2 TLSv1.3;
```

This re-enables deprecated protocols for that block, weakening the TLS posture. TLS 1.0 and 1.1 are deprecated by RFC 8996 (2021) and rejected by all modern browsers.

**Remediation:** Remove the `ssl_protocols` line from the server block so it inherits the snippet's `TLSv1.2 TLSv1.3` setting.

**Verification:**

```bash
# Should fail:
curl --tlsv1.0 --tls-max 1.0 -sI https://battlestats.online/ 2>&1 | head -3
curl --tlsv1.1 --tls-max 1.1 -sI https://battlestats.online/ 2>&1 | head -3

# Should succeed:
curl --tlsv1.2 -sI https://battlestats.online/ 2>&1 | head -1
```

### H3: RabbitMQ EPMD Listening on All Interfaces (Port 4369)

**Risk: Low (mitigated by UFW)**

Erlang Port Mapper Daemon (EPMD) for RabbitMQ listens on `*:4369`. UFW blocks external access, but defense-in-depth says bind to localhost.

**Remediation (attempted, deferred):** Setting `ERL_EPMD_ADDRESS=127.0.0.1` in `/etc/rabbitmq/rabbitmq-env.conf` has no effect because EPMD is started via systemd socket activation (`epmd.socket`), not by RabbitMQ. Overriding `epmd.socket` to bind IPv4-only causes RabbitMQ startup failure (Erlang requires IPv6-capable EPMD). Since UFW blocks port 4369 externally, risk is mitigated. Revisit if RabbitMQ is upgraded or UFW rules change.

**Verification (current state):**

```bash
ss -tlnp | grep 4369
# Shows *:4369 — blocked by UFW, not reachable externally
ufw status | grep 4369
# No rule — default deny applies
```

### H4: Umami (Port 3002) Listening on All Interfaces

**Risk: Low (mitigated by UFW)**

The Umami Next.js process listens on `*:3002`. Nginx proxies it, so direct access is not needed. UFW blocks it, but it should be bound to localhost.

**Remediation:** In the Umami systemd service or startup script, set the host to `127.0.0.1`:

```bash
# In the Umami service environment or .env:
HOST=127.0.0.1
# Or in the start command:
next start -H 127.0.0.1 -p 3002
```

Then restart the Umami service.

**Verification:**

```bash
ss -tlnp | grep 3002
# Should show 127.0.0.1:3002, not *:3002
```

## Implementation Order

| Step | Finding | Risk if Skipped | Requires Restart |
|------|---------|-----------------|------------------|
| 1 | H1: SSH password auth | High — active brute-force | sshd |
| 2 | H2: TLS 1.0/1.1 | Medium — protocol downgrade | nginx reload |
| 3 | H3: EPMD binding | Low — behind UFW | rabbitmq |
| 4 | H4: Umami binding | Low — behind UFW | umami service |

## Implementation Status

- [x] H1: SSH hardening — Implemented 2026-04-09. `PermitRootLogin prohibit-password`, `PasswordAuthentication no`, `PermitEmptyPasswords no`. Backup at `/etc/ssh/sshd_config.bak.20260409`. Key auth verified working.
- [x] H2: TLS 1.0/1.1 removal — Implemented 2026-04-09. Removed TLSv1 and TLSv1.1 from `/etc/nginx/nginx.conf`. Backup at `/etc/nginx/nginx.conf.bak.20260409`. TLS 1.2+ verified working.
- [ ] H3: EPMD localhost binding — **Deferred.** EPMD is managed by systemd socket activation (`epmd.socket`). Overriding to IPv4-only breaks RabbitMQ (Erlang requires IPv6 EPMD). UFW blocks port 4369 externally, so risk is mitigated. Revisit if RabbitMQ is upgraded or if UFW rules change. **Incident note:** The RabbitMQ restart during this attempt cascaded — Gunicorn and all three Celery workers depend on the broker and went down. Required manual `systemctl start` of all four services. ~9 min outage (21:05–21:14 UTC).
- [x] H4: Umami localhost binding — Implemented 2026-04-09. Changed `umami.service` ExecStart to `next start -H 127.0.0.1 -p 3002`. Verified bound to `127.0.0.1:3002` and accessible via nginx proxy.
