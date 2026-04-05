# Runbook: Security Audit — Wapiti Scan Results

_Created: 2026-04-05_
_Scanner: Wapiti 3.2.3_
_Target: https://battlestats.online_

## Purpose

Document the findings from the first Wapiti security audit of the production battlestats application and define concrete next steps for remediation. This runbook covers both the standard scan (focused modules) and the full deep scan (all modules including buster, nikto, timesql, wapp, methods).

## Scan Summary

| Scan | Date | Modules | URLs Tested | Duration |
|---|---|---|---|---|
| Standard | 2026-04-05 13:59 UTC | xss, sql, exec, ssrf, redirect, crlf, csp, cookieflags, http_headers, file | 9 | ~2 min |
| Full (deep) | 2026-04-05 14:01 UTC | all (33 modules) | 9 | ~30 min |

Seed URLs covered the landing page, all landing API endpoints (`/api/landing/players/`, `/api/landing/clans/`, `/api/landing/recent/`, `/api/landing/player-suggestions/`), the explorer (`/api/players/explorer/`), stats (`/api/stats/`), and sitemap entities (`/api/sitemap-entities/`).

## Findings

### Critical / High — None

No SQL injection, XSS, command injection, SSRF, open redirect, CRLF injection, file inclusion, CSRF, LDAP injection (confirmed), Log4Shell, Spring4Shell, ShellShock, or XXE vulnerabilities were detected.

### Medium — Remediation Required

#### F1: Missing Content-Security-Policy Header

| Field | Value |
|---|---|
| Severity | Medium |
| Location | All responses from nginx |
| Finding | `Content-Security-Policy` header is not set |
| Risk | Without CSP, the browser has no policy to restrict inline scripts, eval, or external resource loading. If an XSS vector were introduced, CSP would be the defense-in-depth layer that limits exploit scope. |

**Remediation:** Add a `Content-Security-Policy` header in the nginx `server` block. Start with a report-only policy to identify violations before enforcing:

```nginx
add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline' https://*.googletagmanager.com https://*.google-analytics.com https://cloud.umami.is; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://*.google-analytics.com; connect-src 'self' https://*.google-analytics.com https://*.analytics.google.com https://cloud.umami.is; font-src 'self'; frame-ancestors 'self';" always;
```

The `'unsafe-inline'` for scripts is needed while Next.js injects inline script tags. The Umami and GA domains must be whitelisted for analytics. Tighten `'unsafe-inline'` to nonce-based CSP once Next.js inline scripts are migrated.

#### F2: Missing Strict-Transport-Security (HSTS) Header

| Field | Value |
|---|---|
| Severity | Medium |
| Location | All HTTPS responses |
| Finding | `Strict-Transport-Security` header is not set |
| Risk | Without HSTS, browsers may attempt HTTP connections before upgrading. SSL stripping attacks are possible on first visit or after cache expiry. |

**Remediation:** Add to the nginx HTTPS `server` block:

```nginx
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
```

Start with a shorter `max-age` (e.g. `3600`) to validate, then increase to 2 years. Do not add `preload` until confident — HSTS preload is difficult to undo.

#### F3: Vulnerable Nginx Version (CVE-2023-44487 — HTTP/2 Rapid Reset)

| Field | Value |
|---|---|
| Severity | Medium |
| Location | Server header: `nginx/1.24.0` |
| Finding | nginx 1.24.0 is vulnerable to CVE-2023-44487 (HTTP/2 Rapid Reset DoS) |
| Risk | A remote attacker can exhaust server resources by rapidly creating and canceling HTTP/2 streams. This is a well-known DoS vector exploited in the wild. |
| CPE | `cpe:2.3:a:f5:nginx:*:*:*:*:*:*:*:*` |

**Remediation:**

1. Upgrade nginx on the droplet: `apt-get update && apt-get install --only-upgrade nginx`
2. Verify the installed version is >= 1.25.3 (which includes the fix) or has the Ubuntu/Debian backported patch
3. Alternatively, mitigate with `http2_max_concurrent_streams 100;` in the nginx config (limits the attack surface)

### Low — Hardening Recommended

#### F4: Missing X-Frame-Options Header

| Field | Value |
|---|---|
| Severity | Low |
| Location | All responses |
| Finding | `X-Frame-Options` is not set |
| Risk | The site can be embedded in iframes on other domains, enabling clickjacking attacks. |

**Remediation:**

```nginx
add_header X-Frame-Options "SAMEORIGIN" always;
```

Note: `Content-Security-Policy: frame-ancestors 'self'` (in F1) provides the same protection with better browser support. Both can coexist.

#### F5: Missing X-Content-Type-Options Header

| Field | Value |
|---|---|
| Severity | Low |
| Location | All responses |
| Finding | `X-Content-Type-Options` header is not set |
| Risk | Browsers may MIME-sniff responses and interpret JSON or text as HTML/script, enabling content-type confusion attacks. |

**Remediation:**

```nginx
add_header X-Content-Type-Options "nosniff" always;
```

#### F6: Server Version Disclosure

| Field | Value |
|---|---|
| Severity | Low |
| Location | `Server: nginx/1.24.0` response header |
| Finding | Exact nginx version exposed in response headers |
| Risk | Gives attackers version-specific exploit targeting information (e.g. CVE-2023-44487 above). |

**Remediation:**

```nginx
server_tokens off;
```

#### F7: Sitemap Publicly Accessible

| Field | Value |
|---|---|
| Severity | Informational |
| Location | `https://battlestats.online/sitemap.xml` |
| Finding | Sitemap is publicly enumerable |
| Risk | Negligible — this is intentional for SEO. Wapiti flags it because it aids content discovery. No action needed. |

### Anomalies — Application Errors Under Fuzzing

#### A1: `/api/landing/player-suggestions/` returns 500 on malformed `q` parameter

| Field | Value |
|---|---|
| Scan | Standard |
| Trigger | Path traversal payload in `q`: `test%2F..%2F..%2FWindows%2FSystem32%2Fdrivers%2Fetc%2Fservices%00` |
| Risk | Not exploitable (no file inclusion detected), but the 500 indicates unhandled input. The endpoint should return 400 or an empty result set for invalid queries. |

**Remediation:** Add input validation in the player suggestions view — reject or sanitize queries containing path traversal characters (`/`, `\`, `%00`) before passing to the database query. Minimum: wrap the query in a try/except and return an empty list on error.

#### A2: `/api/players/explorer/` returns 500 on malformed `realm` parameter

| Field | Value |
|---|---|
| Scan | Full (deep) |
| Trigger | Time-based SQL injection payload in `realm`: `sleep(31)#\n1` |
| Risk | Not exploitable (no actual SQL injection — Django ORM parameterizes queries), but the 500 indicates the view doesn't validate the `realm` parameter before using it. |

**Remediation:** Validate `realm` against the known realm list (`na`, `eu`, `asia`) at the top of the view. Return 400 for unrecognized values.

#### A3: `/api/landing/clans/` LDAP injection false positive

| Field | Value |
|---|---|
| Scan | Full (deep) |
| Trigger | LDAP injection payload in `mode` parameter |
| Risk | **False positive.** The application uses PostgreSQL, not LDAP. The 200 response to the payload is because Django silently ignores unknown query parameters or falls back to defaults. No LDAP directory is involved. |

**No remediation needed**, but the `mode` parameter should be validated against known values (`best`, `random`, `sigma`, `popular`) to prevent this class of false positive and improve input hygiene.

#### A4: `/api/players/explorer/` LDAP injection false positive

| Field | Value |
|---|---|
| Scan | Full (deep) |
| Trigger | LDAP injection payload in `realm` parameter |
| Risk | **False positive.** Same as A3 — no LDAP involved. The application returned a response to the malformed realm value, which Wapiti interpreted as a potential injection. |

**No remediation needed** beyond the realm validation fix in A2.

### Additional Findings — Informational

#### Technology Fingerprint

Wapiti identified the following technologies:

| Technology | Version | Notes |
|---|---|---|
| Nginx | 1.24.0 | Exposed via `Server` header |
| Next.js | — | Detected from response patterns |
| Node.js | — | Detected from Next.js runtime |
| React | — | Detected from page structure |
| Ubuntu | — | Detected from server behavior |
| Webpack | — | Detected from bundle patterns |

**Recommendation:** `server_tokens off;` in nginx hides the version. Next.js/React/Node detection is inherent to the tech stack and not actionable.

#### HTTP Methods (OPTIONS)

All API endpoints return 429 (rate-limited) for OPTIONS requests. This is acceptable — DRF's throttling is working correctly. No dangerous methods (PUT, DELETE, TRACE) are exposed.

#### Buster (Directory Enumeration)

The buster module found 1,128 "webpages" by probing common directory names against `/api/stats/`, `/api/player/`, and `/api/clan/`. These are all false positives — the endpoints return valid responses for any path suffix because they are catch-all routes (player name lookups, stats views). This is expected behavior, not information leakage.

## Remediation Priority

| Priority | Finding | Effort | Impact |
|---|---|---|---|
| 1 | F3: Upgrade nginx (CVE-2023-44487) | Low — apt upgrade | Eliminates known DoS vector |
| 2 | F2: Add HSTS header | Low — nginx config | Prevents SSL stripping |
| 3 | F1: Add CSP header | Medium — requires testing with GA/Umami | Defense-in-depth against XSS |
| 4 | F4+F5+F6: X-Frame-Options, X-Content-Type-Options, server_tokens | Low — nginx config | Standard hardening |
| 5 | A1+A2: Fix 500s on malformed input | Low — view-level validation | Prevents error-based info leakage |

## Recommended Nginx Security Headers Block

All header fixes can be applied in a single nginx config update:

```nginx
# --- Security headers (add to server block) ---
server_tokens off;
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline' https://*.googletagmanager.com https://*.google-analytics.com https://cloud.umami.is; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://*.google-analytics.com; connect-src 'self' https://*.google-analytics.com https://*.analytics.google.com https://cloud.umami.is; font-src 'self'; frame-ancestors 'self';" always;
```

After applying, verify with:

```bash
curl -sI https://battlestats.online/ | grep -iE 'strict-transport|x-frame|x-content-type|content-security|server:'
```

## Scan Infrastructure

| Component | Location |
|---|---|
| Wapiti install | `/home/august/.local/share/wapiti-venv/bin/wapiti` |
| Audit script | `scripts/security_audit.sh` |
| Reports directory | `server/logs/security/` (gitignored) |
| Weekly cron | Mondays 9:23 AM local — `scripts/security_audit.sh https://battlestats.online` |
| Standard scan report | `server/logs/security/wapiti-standard-20260405-135911.{html,json,log}` |
| Full scan report | `server/logs/security/wapiti-full-20260405-140157.{html,json,log}` |

## Modules Not Tested

The following are out of scope for Wapiti but should be considered for a complete security posture:

1. **Authentication/authorization testing** — no authenticated endpoints were scanned (admin, Umami dashboard)
2. **API rate limiting validation** — DRF throttling is in place but not stress-tested
3. **Dependency vulnerability scanning** — GitHub Dependabot reports 5 vulnerabilities (2 moderate, 3 low) on the default branch
4. **Infrastructure scanning** — SSH hardening, firewall rules, TLS cipher suite are not covered by Wapiti
5. **Secret management audit** — env files, API keys, database credentials on the droplet

## Next Audit

The weekly cron runs the standard scan automatically. Run `./scripts/security_audit.sh --full` manually after applying the nginx hardening to verify the fixes took effect.
