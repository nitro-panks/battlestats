# Umami live click-test — findings & next steps (2026-06-18)

_Context: ran a live, authorized click-test by temporarily clearing Umami's `IGNORE_IP` for the operator home IP (`130.44.131.215`), navigating the live site while watching realtime, then re-blocking and deleting all test data. Purpose: confirm the events catalogued in `runbook-umami-event-reference-2026-06-18.md` actually fire end-to-end (operator clicks are normally dropped, so 🟡/💤 statuses could never be self-verified)._

## Outcome

**Every event in the catalog fired end-to-end with correct payloads.** All prior 🟡 PENDING and 💤 DEAD entries (`landing-player-click`, `landing-clan-click`, `clan-share`, `clan-chart-activity-filter`, `ship-stats-open/close`, `outbound-link`, `streamer-open`, `streamer-submit`) are **wired and working** — their zero-capture status was discoverability/recency, not a tracking bug.

## Next steps (ranked)

### 1. WG support footer link was dead — FIXED (this branch)
- `client/app/components/Footer.tsx:82` pointed at `https://www.support.wargaming.net/` (entire domain unreachable, conn-fail). Replaced with `https://wargaming.net/support/` (verified 200).
- **Remaining:** needs a patch release + **frontend rebuild/deploy** (`NEXT_PUBLIC_APP_VERSION` is build-time; footer also surfaces version). `./client/deploy/deploy_to_droplet.sh battlestats.online`.

### 2. Ships insights tab — tier/type filters are untracked (tracking blind spot)
- The "Ships" tab renders `RandomsSVG.tsx`, which has **no `trackEvent` import at all**. Tab-open (`player-insights-ships`) is tracked, but toggling the ship-**type** / **tier** filter pills inside it emits nothing.
- **Fix:** add a low-cardinality event on `toggleType` (~L407), `toggleTier` (L422), and the "All types"/"All tiers" buttons — e.g. `randoms-filter {realm, control:'type'|'tier', value}`, mirroring `ship-leaderboard-filter`. Add to the event-reference runbook + `umami.test.ts`-style coverage.

### 3. `clan-member-click` is the highest-volume event but only carries `{realm}`
- Can't distinguish which surface drives roster navigation (clan page vs. the clan section on a player page — both share one leaf attach in `ClanMembers.tsx:135`).
- **Fix:** add `source:'clan'|'player'` to the payload (pass a prop from each mount). Cheap, high signal.

### 4. `landing-best-sort` (and all multi-prop events) unreadable in the default event list
- Payload (`entity`, `sort`, `realm`) IS captured correctly in `event_data`, but Umami's default Events view groups by name+URL only, so every sort collapses to "landing-best-sort on /". You must open the event → **Properties** to see the split.
- **Fix:** documentation, not code — add a "how to read multi-prop events (Properties drill-down)" note to `runbook-umami-analytics-coverage-2026-06-17.md`. (Alternative: fold `entity` into the name as `landing-best-sort-player/-clan`; costs cardinality, only do if at-a-glance separation is worth it.)

### 5. First-party entity tracking does NOT respect the operator IP exclusion
- `IGNORE_IP` governs **Umami only**. The app's own first-party tracking (`EntityVisitEvent` / `EntityVisitDaily`, feeds Popular + hot-player promotion) recorded all 42 operator page views during the test. Future operator browsing of `/player` and `/clan` pages will taint those analytics.
- **Fix:** add an operator/internal exclusion to the first-party visit path (skip recording when the request IP matches an internal allowlist, or reuse the same ignore-list config). Until then, any live operator browsing of entity pages needs the same `rebuild_entity_visit_daily` cleanup (see cleanup recipe below).

### 6. (minor) `StreamerSubmission.submitter_ip` always logs `127.0.0.1`
- The form POST arrives via the nginx proxy and the view isn't reading `X-Forwarded-For`, so `submitter_ip` is useless for abuse-tracking. Low priority; fix when touching the streamer submit view.

### 7. Reconcile the event-reference runbook statuses
- `runbook-umami-event-reference-2026-06-18.md` still marks the above events 🟡/💤. Update to ✅ (live-verified 2026-06-18) and move the "can't self-trigger" caveat to a "verified via temporary IGNORE_IP lift" note.

## Cleanup recipe — a live click-test taints THREE stores, not just Umami
1. **Umami** (managed-PG `umami` DB): delete the operator's `session_id` — `event_data` (via `website_event_id`) → `website_event` → `session_data` → `session`. Isolate the session by geo/device fingerprint (operator = Somerville/US-MA, since `IGNORE_IP` had blocked prior sessions, the test session is brand-new).
2. **First-party** (app DB): delete `EntityVisitEvent` rows for the operator `visitor_key_hash` bounded by `occurred_at >= T0`, then `python manage.py rebuild_entity_visit_daily --start-date <day> --end-date <day>` to recompute `EntityVisitDaily` from the remaining events (operator-only entities drop out; shared entities recompute clean).
3. **Side-effect data**: a successful `streamer-submit` writes a real `StreamerSubmission` (status `pending`) — delete it too. Watch for any other write-path actions exercised during the test.
4. Restore `IGNORE_IP`, restart `umami.service`, and **functionally** verify (load a page, confirm 0 new events land), not just that the service restarted.
