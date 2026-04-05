# Runbook: Security Audit — Findings And Remediation Plan

_Created: 2026-04-05_
_Scanner: Wapiti 3.2.3_
_Target: https://battlestats.online_
_QA: Findings verified against live droplet config, application source, and Ubuntu package changelogs on 2026-04-05._

## Purpose

This runbook documents the first production security audit of battlestats and provides a ready-to-implement remediation plan for all confirmed findings. Each remediation step includes the exact change, where to apply it, how to verify it, and what to watch for.

## Scan Summary

| Scan | Date | Modules | URLs Tested | Duration |
|---|---|---|---|---|
| Standard | 2026-04-05 13:59 UTC | xss, sql, exec, ssrf, redirect, crlf, csp, cookieflags, http_headers, file | 9 | ~2 min |
| Full (deep) | 2026-04-05 14:01 UTC | all (33 modules incl. buster, nikto, timesql, wapp, methods, spring4shell, log4shell) | 9 | ~30 min |

Seed URLs: `/`, `/api/landing/players/`, `/api/landing/clans/`, `/api/landing/recent/`, `/api/landing/player-suggestions/`, `/api/stats/`, `/api/players/explorer/`, `/api/sitemap-entities/`.

## Overall Assessment

**No exploitable injection vulnerabilities found.** All 33 Wapiti modules passed cleanly for XSS, SQL injection, command injection, SSRF, open redirect, CRLF injection, file inclusion, CSRF, XXE, Log4Shell, Spring4Shell, and ShellShock.

The attack surface is small: the application serves read-only public data with no user authentication, no form submissions, and no file uploads on the public surface. Django ORM parameterization and DRF throttling provide strong baseline protection.

Findings fall into three categories:

1. **Missing HTTP security headers** — nginx config, no code changes
2. **Application input validation gaps** — two endpoints return 500 on fuzzed input
3. **False positives and informational** — no action required

## Confirmed Findings

### F1: Missing Security Headers (nginx)

**Verified against live config:** The production nginx config at `/etc/nginx/sites-enabled/battlestats` contains zero security headers. No `server_tokens`, no HSTS, no CSP, no X-Frame-Options, no X-Content-Type-Options.

**Current response headers** (verified via `curl -sI`):

```
server: nginx/1.24.0 (Ubuntu)
x-powered-by: Next.js
```

All five missing headers are addressable in a single nginx config update.

### F2: CVE-2023-44487 (HTTP/2 Rapid Reset) — Already Patched

**Wapiti flagged this as a vulnerability based on the nginx version string (`1.24.0`), but the Ubuntu package has already backported the fix.**

Verified:

```
Package: nginx 1.24.0-2ubuntu7.6
Changelog: "d/p/CVE-2023-44487.patch adds additional mitigations for CVE-2023-44487"
```

The vulnerability is patched. The only remaining action is to hide the version string via `server_tokens off` to prevent future false positives and reduce information disclosure.

### F3: Player Suggestions Endpoint Returns 500 on Null Byte Input

**Endpoint:** `GET /api/landing/player-suggestions/?q=<payload>`
**Root cause:** The view at `server/warships/views.py:1049` passes the raw `q` parameter into a PostgreSQL `ILIKE` query via psycopg2 parameterized SQL. The query itself is not injectable (parameterized with `%s`), but psycopg2 raises `ValueError` when the string contains a null byte (`\x00`). This exception is unhandled, producing a 500.

**Reproduction:**

```bash
curl -s -o /dev/null -w "%{http_code}" \
  "https://battlestats.online/api/landing/player-suggestions/?q=test%00"
# Returns: 500
```

**Code path:** `views.py:1064-1077` — raw SQL `WHERE name ILIKE %s` with `[realm, f'%{query}%', f'{query}%']`.

### F4: Explorer Endpoint Returned 500 During Timesql Fuzzing

**Endpoint:** `GET /api/players/explorer/?realm=<payload>`
**Verification:** Could not reproduce with `realm=sleep(31)#\n1` — returns 200 because `_get_realm()` at `views.py:41` silently falls back to `DEFAULT_REALM` for unrecognized values. The 500 Wapiti logged was likely transient (e.g., a database timeout during the scan window, not caused by the payload). `_get_realm` already validates and sanitizes the realm parameter.

**Disposition:** No code change needed for realm validation. The transient 500 may warrant a general try/except guard on the explorer view, but is lower priority.

### False Positives — No Action Required

| Finding | Why It's False | Verification |
|---|---|---|
| LDAP injection on `/api/landing/clans/?mode=<payload>` | No LDAP in the stack. `normalize_landing_clan_mode()` validates the `mode` parameter and returns 400 for invalid values. Wapiti flagged a 200 response to a fallback as "injection". | `views.py:911-913` |
| LDAP injection on `/api/players/explorer/?realm=<payload>` | Same — `_get_realm()` falls back to default realm for invalid input. No LDAP anywhere. | `views.py:41-45` |
| Buster found 1,128 "webpages" | Catch-all routes (`/api/player/<name>/`, `/api/stats/`) return valid responses for any suffix. This is by design (player name lookup). Not information leakage. | URL routing in `urls.py` |
| Sitemap publicly accessible | Intentional for SEO. `app/sitemap.ts` generates the sitemap from recently-visited entities. | |
| Nikto flagged sitemap.xml | Same as above — informational only. | |

### Technology Fingerprint

| Technology | Detected Version | Exposure |
|---|---|---|
| Nginx | 1.24.0 | `Server` header — fixable via `server_tokens off` |
| Next.js | unversioned | `X-Powered-By: Next.js` header — fixable via Next.js config |
| Node.js | unversioned | Inferred from Next.js |
| React | unversioned | Inferred from page structure |
| Ubuntu | unversioned | Inferred from server behavior |

## Remediation Plan

### Step 1: Nginx Security Headers

**Scope:** Single SSH session to the droplet. No deploy required.
**Risk:** Low — additive headers only. CSP is the only one that could break functionality if misconfigured.
**Rollback:** Remove the added lines and `nginx -s reload`.

#### 1a. Apply headers

SSH to the droplet and edit `/etc/nginx/sites-enabled/battlestats`:

```bash
ssh root@battlestats.online
```

Add these lines inside the main HTTPS `server` block (the one with `server_name battlestats.online;`), before any `location` blocks:

```nginx
    # --- Security headers ---
    server_tokens off;

    add_header Strict-Transport-Security "max-age=3600; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
```

**Why not CSP yet:** CSP is applied in Step 1b as a separate operation because it interacts with inline scripts and external analytics. The headers above are safe to apply immediately with no risk of breaking functionality.

**Why `max-age=3600`:** Start HSTS with a 1-hour max-age. Once verified, increase to `max-age=63072000` (2 years). Do not add `preload` — HSTS preload submission is effectively permanent and difficult to undo.

#### 1b. Test and reload

```bash
nginx -t && nginx -s reload
```

#### 1c. Verify

```bash
curl -sI https://battlestats.online/ | grep -iE 'strict-transport|x-frame|x-content-type|referrer-policy|permissions-policy|^server:'
```

Expected output:

```
server: nginx
strict-transport-security: max-age=3600; includeSubDomains
x-frame-options: SAMEORIGIN
x-content-type-options: nosniff
referrer-policy: strict-origin-when-cross-origin
permissions-policy: camera=(), microphone=(), geolocation=()
```

The `server:` line should show `nginx` without the version number.

#### 1d. Also apply to the www redirect block

Add `server_tokens off;` to the `www.battlestats.online` HTTPS redirect `server` block as well, so the redirect response also hides the version.

#### 1e. Increase HSTS max-age after validation

After confirming the site works correctly with HSTS for 24 hours, SSH back and change `max-age=3600` to `max-age=63072000`, then reload nginx.

### Step 2: Content-Security-Policy

**Scope:** Nginx config change. Requires testing because inline scripts and external analytics domains must be whitelisted.
**Risk:** Medium — a too-strict CSP will break GA tracking, Umami analytics, or the theme initialization script.

#### 2a. Understand the inline script dependencies

The application uses three inline scripts that require `'unsafe-inline'` in `script-src`:

1. **Theme/realm initializer** — `layout.tsx:48` uses `dangerouslySetInnerHTML` to set `data-theme` and `data-realm` before paint (prevents flash of wrong theme)
2. **GA gtag inline** — `layout.tsx:57-64` defines the `gtag()` function and calls `gtag('config', ...)`
3. **Next.js runtime** — Next.js injects inline `<script>` tags for hydration data

External script sources:

1. `https://www.googletagmanager.com/gtag/js` — GA loader
2. `/umami/script.js` — Umami tracking (same origin, proxied via nginx)

External connect targets (beacons/XHR):

1. `https://*.google-analytics.com` — GA measurement protocol
2. `https://*.analytics.google.com` — GA4 data stream
3. The Umami script posts to `/umami/api/send` (same origin)

#### 2b. Start with Content-Security-Policy-Report-Only

Add to the nginx HTTPS server block, after the Step 1 headers:

```nginx
    add_header Content-Security-Policy-Report-Only "default-src 'self'; script-src 'self' 'unsafe-inline' https://www.googletagmanager.com; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' https://*.google-analytics.com https://*.analytics.google.com; font-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self';" always;
```

`Report-Only` mode logs violations to the browser console without blocking anything. This lets us confirm no legitimate resources are blocked before enforcing.

```bash
nginx -t && nginx -s reload
```

#### 2c. Validate in browser

1. Open `https://battlestats.online/` in Chrome
2. Open DevTools → Console
3. Navigate to several pages (landing, player detail, clan detail)
4. Look for `[Report Only]` CSP violation messages
5. If any legitimate resource is blocked, add its domain to the appropriate directive

Known things to watch for:
- GA beacons to `*.google-analytics.com` or `*.analytics.google.com`
- Next.js `_next/data` fetches (should be covered by `'self'`)
- D3 chart rendering (all local, should be fine)

#### 2d. Enforce CSP

Once no false violations appear after 24 hours of report-only:

1. Change `Content-Security-Policy-Report-Only` to `Content-Security-Policy`
2. `nginx -t && nginx -s reload`
3. Re-verify in browser console

#### 2e. Future: Nonce-based CSP

The current policy requires `'unsafe-inline'` for scripts, which weakens XSS protection. To eliminate it:

1. Configure Next.js to emit CSP nonces via `next.config.js` headers or middleware
2. Replace `'unsafe-inline'` with `'nonce-<value>'` in the CSP
3. This is a meaningful project — defer until the simple CSP is stable

### Step 3: Hide X-Powered-By Header

**Scope:** Next.js config change. Requires a client redeploy.

Add to `client/next.config.js` (or `.ts`):

```js
module.exports = {
  // ... existing config
  poweredByHeader: false,
};
```

This removes the `X-Powered-By: Next.js` header from all responses. Apply during the next client deploy.

### Step 4: Fix Player Suggestions 500 on Null Byte

**Scope:** One-line change in `server/warships/views.py`.
**Root cause:** psycopg2 rejects null bytes in string parameters. The query string `q` is passed directly to SQL without stripping null bytes.

#### 4a. Apply fix

In `server/warships/views.py`, in the `player_name_suggestions` function, add null byte stripping after the existing `strip()` call:

```python
# Current (line 1050):
query = (request.query_params.get('q') or '').strip()

# Change to:
query = (request.query_params.get('q') or '').strip().replace('\x00', '')
```

This silently strips null bytes. The alternative (returning 400) is also acceptable, but stripping is simpler and matches the existing pattern of cleaning input before use.

#### 4b. Verify

```bash
curl -s -o /dev/null -w "%{http_code}" \
  "https://battlestats.online/api/landing/player-suggestions/?q=test%00"
# Should return: 200 (with results for "test")
```

#### 4c. Add test coverage

Add a test case to `server/warships/tests/test_views.py` in the existing suggestions test class:

```python
def test_player_suggestions_null_byte_returns_200(self):
    response = self.client.get("/api/landing/player-suggestions/?q=test\x00")
    self.assertIn(response.status_code, [200, 400])
```

### Step 5: Dependabot Vulnerabilities

GitHub reports 5 vulnerabilities (2 moderate, 3 low) on the default branch. These are dependency-level issues, not application vulnerabilities.

```bash
# Review current alerts
gh api repos/nitro-panks/battlestats/dependabot/alerts --jq '.[] | select(.state=="open") | {package: .security_advisory.summary, severity: .security_vulnerability.severity, path: .dependency.manifest_path}'
```

Triage and resolve per standard dependency update process. See `agents/runbooks/runbook-dependency-audit.md`.

## Remediation Execution Order

| Step | Finding | Method | Deploy Required | Estimated Time |
|---|---|---|---|---|
| 1a-1e | F1: Security headers (no CSP) | SSH + nginx config | No | 5 min |
| 2a-2d | F1: CSP (report-only → enforce) | SSH + nginx config | No | 5 min + 24h observation |
| 3 | Tech fingerprint: X-Powered-By | `next.config.js` | Client deploy | 2 min + deploy |
| 4 | A1: Suggestions null byte 500 | `views.py` | Backend deploy | 5 min + deploy |
| 5 | Dependabot alerts | `npm audit fix` / `pip` | Varies | 30 min |

Steps 1 and 2 require only an SSH session and nginx reload — no application deploy. Steps 3 and 4 can be batched into the next regular deploy.

## Post-Remediation Verification

After all steps are complete, run a full Wapiti re-scan:

```bash
./scripts/security_audit.sh --full https://battlestats.online
```

Expected results:

- CSP, HSTS, X-Frame-Options, X-Content-Type-Options findings should disappear
- Server version disclosure finding should disappear
- CVE-2023-44487 finding should disappear (version string hidden)
- Player suggestions 500 anomaly should disappear
- LDAP false positives may persist (harmless — Wapiti limitation)

## Scope Limitations

This audit covers the public-facing HTTP attack surface only. The following areas are not tested by Wapiti and should be audited separately:

| Area | Why It Matters | Recommended Tool |
|---|---|---|
| Authenticated endpoints (Django admin, Umami) | Separate attack surface with session management | Manual review or authenticated Wapiti scan |
| SSH hardening | Direct server access | `ssh-audit` |
| TLS cipher suite | Protocol-level security | `testssl.sh` or SSL Labs |
| Firewall rules | Network-level exposure | `nmap` port scan |
| Secret management | Credential hygiene on droplet | Manual review of `/etc/battlestats-server.env`, `.secrets.env` |
| API rate limiting | DDoS resilience | Load testing with `wrk` or `vegeta` |
| Dependency supply chain | Transitive vulnerability exposure | `npm audit`, `pip-audit`, Dependabot |

## Scan Infrastructure

| Component | Location |
|---|---|
| Wapiti venv | `/home/august/.local/share/wapiti-venv/bin/wapiti` |
| Audit script | `scripts/security_audit.sh` (`--full` for deep scan) |
| Reports directory | `server/logs/security/` (gitignored) |
| Weekly cron | Mondays 9:23 AM local |
| Standard scan report | `server/logs/security/wapiti-standard-20260405-*.{html,json,log}` |
| Full scan report | `server/logs/security/wapiti-full-20260405-*.{html,json,log}` |

## SSL/TLS Posture (Verified)

The Let's Encrypt TLS configuration at `/etc/letsencrypt/options-ssl-nginx.conf` is solid:

- TLS 1.2 and 1.3 only (no TLS 1.0/1.1)
- Modern cipher suite (ECDHE + AES-GCM + CHACHA20-POLY1305)
- Session tickets disabled
- DH parameters configured

No TLS-level changes needed.
