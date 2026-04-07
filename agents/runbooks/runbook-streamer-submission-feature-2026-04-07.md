# Feature Runbook: Streamer Submission Queue

_Created: 2026-04-07_
_Status: Implemented (submit + queue + admin list slice). Approval-side `Player.is_streamer` promotion is intentionally deferred — see Follow-ups._

## Why

`Player.is_streamer` exists in the model and is filterable in Django admin, but there is no community path to flag a player as a Twitch streamer. Today the field is set by hand. This feature adds a footer link → modal → form so visitors can submit `(IGN, twitch_handle, twitch_url)` into a moderated queue. An admin reviews the queue and approves/rejects.

## Architecture

```
Footer button
   ↓ (click)
StreamerSubmissionModal  ──fetch POST──▶  /api/streamer-submissions/
   ↓                                            ↓
honeypot + form_loaded_at                StreamerSubmissionSerializer
                                                ↓
                                         StreamerSubmission row (status=pending)
                                                ↓
                                         Django admin queue
                                                ↓
                                         approve_selected / reject_selected
```

Files:
- `client/app/components/Footer.tsx` — opens modal
- `client/app/components/StreamerSubmissionModal.tsx` — form, honeypot, time-gate, success/error UX
- `server/warships/models.py` — `StreamerSubmission`
- `server/warships/serializers.py` — `StreamerSubmissionSerializer`
- `server/warships/views.py` — `streamer_submission_view`
- `server/battlestats/urls.py` — `/api/streamer-submissions/`
- `server/warships/admin.py` — `StreamerSubmissionAdmin` with approve/reject actions
- `server/warships/migrations/0043_streamersubmission.py`

## Anti-abuse layers (in order of cheapness)

1. **DRF throttle** — `PUBLIC_API_THROTTLES` (anon 120/min) caps volume per IP. Already configured site-wide in `settings.py`.
2. **Honeypot field** (`website`) — hidden via off-screen CSS, `aria-hidden`, `tabIndex=-1`. Real users never see it. Naive form-stuffing bots fill every input and trip the 400.
3. **Time-gate** (`form_loaded_at`) — client stamps epoch ms when modal mounts; server rejects if `< 2s` elapsed. Bots that submit instantly fail.
4. **Strict regex validation** — IGN, handle, URL each constrained to narrow patterns (`IGN_RE`, `TWITCH_HANDLE_RE`, `TWITCH_URL_RE`). Random garbage rejected.
5. **Cross-field check** — handle component of URL must equal the standalone handle. Stops mismatched/scammy entries.
6. **Approval queue** — every row starts `status=pending`. Nothing is published until an admin clicks Approve.

### No captcha by design

Layered cheap defenses give us most of the benefit at zero UX cost. Skip captcha unless we observe sustained abuse:

- > 5 submissions/minute from a single IP after the throttle is in place
- Honeypot trip-rate that suggests bots are learning around it
- Repeated obviously-spam approvals that admins are culling

**Escalation**: install `django-hcaptcha`, add a frontend widget, gate via a `STREAMER_SUBMISSION_CAPTCHA_ENABLED` env var. Sketch:
```python
# settings.py
HCAPTCHA_SITEKEY = os.environ['HCAPTCHA_SITEKEY']
HCAPTCHA_SECRET = os.environ['HCAPTCHA_SECRET']
# serializer
captcha = HCaptchaField()
```

## Security considerations

- **XSS** — All values are stored as plain text. React auto-escapes on render; Django admin templates auto-escape. The `twitch_url` is only ever a string in the admin until approval; if rendered as `<a href>` later, sanitize then.
- **SQL injection** — ORM-only writes; no raw SQL.
- **CSRF** — `@api_view` POST from same origin. DRF default `SessionAuthentication` enforces CSRF for session-authed users. Anonymous submitters bypass session auth (correct for an anonymous public endpoint, consistent with `analytics_entity_view`).
- **PII** — `submitter_ip` + `submitter_ua` are stored for abuse forensics. **Recommended (deferred)**: scheduled purge of `submitter_ip` for rows older than 90 days. Implement as a Celery beat task or `manage.py` command.
- **Open redirect** — `twitch_url` is regex-locked to `https://(www.)?twitch.tv/<handle>`. No way to use it as a redirect target before approval.
- **Length caps** — All CharFields have explicit `max_length`. UA truncated to 300 chars at write time.

## Approval workflow (current)

1. Admin opens `/admin/warships/streamersubmission/`
2. Filters by `status=pending`
3. Selects rows, runs **Approve selected submissions** (or **Reject**)
4. Action stamps `reviewed_at`, `reviewed_by`, and updates `status`

**Currently the approve action does NOT promote `Player.is_streamer = True` or persist the channel URL.** That is the deferred follow-up below.

## Follow-ups (not in this slice)

1. **Promote on approval.** Decide where `twitch_url` lives:
   - **(a) Add `twitch_url` field to `Player`** — simple, single source of truth. Approve action looks up `Player.objects.filter(name__iexact=ign, realm=realm).first()`, sets `is_streamer=True` and `twitch_url=...`. Recommended.
   - **(b) New `PlayerStreamProfile` table** — supports multiple platforms and history. Heavier; defer until needed.
2. **Frontend display.** Once `Player.twitch_url` exists, surface a Twitch icon on player cards/headers linking to the channel.
3. **PII purge job.** 90-day `submitter_ip` scrub.
4. **Rate-limit the endpoint specifically** if we ever see abuse — add a dedicated `ScopedRateThrottle` (e.g. 10/hour anon) without lowering the global anon limit.

## Verification

### Automated tests

`server/warships/tests/test_views.py::StreamerSubmissionViewTests` covers:
- `test_happy_path_creates_pending_submission` — 201, row exists, status=pending
- `test_honeypot_trips` — 400 when `website` is non-empty, no row created
- `test_url_handle_mismatch_rejected` — 400 when handle ≠ URL handle
- `test_too_fast_submission_rejected` — 400 when `form_loaded_at` is < 2s ago

Run:
```bash
cd server
DJANGO_SECRET_KEY=test-secret DB_ENGINE=sqlite3 DB_NAME=/tmp/test-streamer.sqlite3 \
DB_SSLMODE='' DB_SSLROOTCERT='' REDIS_URL='' \
CELERY_BROKER_URL=memory:// CELERY_RESULT_BACKEND=cache+memory:// \
python -m pytest --nomigrations \
  warships/tests/test_views.py::StreamerSubmissionViewTests --tb=short
```
All 4 pass as of 2026-04-07.

### Manual smoke (local)

1. Click footer "I'm a streamer!" — modal opens, focus lands on IGN input
2. Press Escape, click backdrop, click X — all close the modal
3. Submit `bfk_ferlyfe` / `bfk_fer1yfe` / `https://www.twitch.tv/bfk_fer1yfe` → success state, auto-close after 2s
4. Toggle dark mode mid-modal — colors update via CSS variables
5. Inspect DOM: honeypot input present, off-screen, `tabIndex=-1`, `aria-hidden`
6. Open Django admin → submission visible in queue, `status=pending`

### Smoke against droplet (post-deploy)

```bash
curl -X POST https://battlestats.online/api/streamer-submissions/ \
  -H 'Content-Type: application/json' \
  -d '{"ign":"x","twitch_handle":"y","twitch_url":"z","form_loaded_at":1}'
```
Should return 400 (validation). A real submission via the modal should appear in admin.

## Related

- `runbook-incident-bulk-enrichment-poison-batch-2026-04-07.md` — unrelated, same week
- `agents/knowledge/agentic-team-doctrine.json` — pre-commit doctrine governing this change
