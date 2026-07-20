# Runbook: Cross-Realm Player Fallback

_Created: 2026-07-20_
_Status: **IMPLEMENTED** on branch `cross-realm-fallback` (2026-07-20). Not yet released/deployed. See the Implementation Record at the end._
_Context: a deep-link like `/player/Ayanami_332` opened while NA is the selected realm renders "Player not found" when the player only exists on ASIA. Realm is a purely client-side concept today; the backend lookup is scoped to one WG regional host, so a name that lives in another realm is invisible._
_QA: all file:line anchors verified against the working tree at creation time (`views.py`, `RealmContext.tsx`, `PlayerRouteView.tsx`, `RealmSelector.tsx`, `ConnectionHint.tsx`). A full code-assumption QA pass (see Implementation Record) corrected four design details before implementation._

## Purpose

Make a player deep-link resolve to the realm the player actually lives on, instead of failing when the visitor's selected realm does not match. When the current realm misses, probe the other realms cheaply, switch the app to the realm where the player was found, and bring the user along with a visible cue (toast + realm-chip flash). This targets the reported path only: opening a `/player/<name>` link under the wrong realm. Cross-realm autocomplete/search is explicitly out of scope.

## Decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Where the fallback lives | **Backend probe** in `PlayerViewSet.get_object` | One HTTP round-trip; cheap `account/list` existence probe before any full-stats fetch; keeps the negative-cache fix where the bug is. |
| Post-resolution page state | **Switch to found realm** | Reality wins: the visitor is now "in" the realm where the player exists. Not a route redirect; the `?realm=` query param is rewritten via `replaceState` for reload/share safety. |
| Ambiguity tiebreak (name in >1 other realm) | **Deterministic order: ASIA → EU → NA** | Cheap, predictable, reload-stable. Ordered by player population. Never resolves based on a same-named stranger's activity ("most active" was rejected). |
| User feedback | **Toast + realm-chip flash** | Toast (with `aria-live`) explains the "why" in words and announces to AT; the chip flash shows "where to look." Both reuse existing primitives. |
| Rollout | **Kill switch `CROSS_REALM_FALLBACK_ENABLED`** (default on) | Per project convention; instant revert without redeploy. |

## Current flow (as-built anchors)

**Frontend**
- Route: `client/app/player/[playerName]/page.tsx` renders `<PlayerRouteView key={playerName} ... />` (key is name-only; a realm switch re-runs effects without remount).
- Realm state: `client/app/context/RealmContext.tsx` — precedence `?realm=` → `localStorage['bs-realm']` → `'na'`; `setRealm` persists at `RealmContext.tsx:78-85`. No cookie, no SSR realm knowledge (SSR assumes `'na'`).
- Fetch + not-found: `client/app/components/PlayerRouteView.tsx:69` builds `/api/player/<name>/?realm=<realm>`; 4xx → `setError('Player not found.')` (`:87-106`); rendered at `:152-153`. Scope/abort keyed `${playerName}:${realm}` (`:42-52`).
- Realm selector: `client/app/components/RealmSelector.tsx` — display via `useDisplayRealm()`; manual `handleRealmChange` calls `setRealm` **and** `router.replace('/?realm=')` (navigates to landing — the fallback must NOT go through this handler).
- Toast precedent: `client/app/components/ConnectionHint.tsx` — `role="status" aria-live="polite"`, subtle bordered pill, theme tokens.
- Glow precedent: `client/app/globals.css` `.tab-attention-glow*` / `@keyframes tab-attention-glow-fade` — **animates opacity, never box-shadow** (perf rule).

**Backend**
- `server/warships/views.py` `PlayerViewSet`:
  - `_get_realm(request)` (`:69-73`) reads/validates `?realm=`.
  - `get_object` (`:274-317`): DB lookup by `name_lower + realm` (`:281-284`); on `DoesNotExist` (`:286`) consults the negative-lookup cache (`:287-288`), else `_fetch_player_id_by_name(name, realm)` (`:290-291`); nothing found → **`Http404`** (`:292-295`); found → `get_or_create` + synchronous `update_player_data` (`:303-311`).
  - Negative cache key `_missing_player_lookup_cache_key` (`:189`, used `:278`) is **name-only** — a latent poison across realms.
- `server/warships/api/players.py` `_fetch_player_id_by_name` (`:95-134`): fully realm-parameterized; local DB check by `name+realm` then WG `account/list` `type=exact` scoped to one realm host. **Reusable as-is.**
- `server/warships/api/client.py` `REALM_BASE_URLS` (`:18-23`) / `get_base_url(realm)` (`:55-56`) — realm → WG host.
- `server/warships/models.py`: `VALID_REALMS`/`DEFAULT_REALM='na'` (`:6-8`); `UniqueConstraint(['player_id','realm'])` (`:143-146`); **no** unique on `name`, so a name can exist independently per realm.

## Design

### Backend — resolution order in `get_object`

Gate the whole fallback behind `os.getenv('CROSS_REALM_FALLBACK_ENABLED', '1') == '1'` (the project's point-of-use kill-switch pattern; **not** a `settings.` attribute — corrected during QA). The DB fast path is unchanged; the fallback lives in the `except Player.DoesNotExist` branch of `get_object`.

1. **Current realm, DB** — existing fast path (unchanged).
2. **Fallback resolution** — `_resolve_player_across_realms(name, requested)` builds the probe list `[requested] + [asia, eu, na minus requested]` and, for each realm in order:
   - skips it when its **per-realm** negative-lookup key is cached;
   - calls `_fetch_player_id_by_name(name, realm)` — which is **itself DB-first then WG** (`players.py:101-115`), so the original design's separate "DB-first" and "WG-probe" steps collapse into this one loop (corrected during QA);
   - **first hit wins**; a miss records that realm's per-realm negative key.
3. **Winner commit** — `realm` is reassigned to the resolved realm; blocklist check preserved; `get_or_create(player_id, realm=winner)` + one `update_player_data` (the single expensive fetch) on the winner.
4. **All realms miss** — set the name-level **all-realms-miss** key (short TTL) and raise `Http404`. The existing "Player not found." message stands.

Ordering rationale: the visitor's current realm is always probed first, so a name present in the current realm never triggers fallback; the fixed ASIA → EU → NA order breaks ties only among the *other* realms. When the kill switch is off, the legacy single-realm path runs byte-for-byte (only the negative-cache key is realm-qualified).

### Backend — correctness fixes (block the feature)

- **Realm-qualify** `_missing_player_lookup_cache_key` — include realm in the key so an NA miss cannot suppress an ASIA lookup. Every current caller passes realm already.
- **Name-level all-realms-miss cache** — a distinct key (name-only, short TTL, e.g. reuse the negative-cache TTL) set only after step 6 exhausts every realm, so a genuinely nonexistent name does not re-probe 3 realms on every page load. Cleared implicitly by TTL.

### Backend — signaling the resolved realm

- **Corrected during QA:** the resolved realm travels as the **`X-Resolved-Realm` response header**, not a payload field. `fetchSharedJson` already exposes any header named in its `responseHeaders` allowlist (`PlayerRouteView.tsx`), so a header needs **no serializer/payload change** and works uniformly on both the cache-hit and get_object paths.
- `retrieve` sets `X-Resolved-Realm` on every player response: the requested realm on the fast cache-hit path; `getattr(self, '_resolved_realm', realm)` on the miss path (get_object stashes the realm it actually resolved to). The miss-path refresh signals (`_player_refresh_signals`) also use the resolved realm, not the requested one — otherwise they mis-look-up the just-created player.
- On the common in-realm hit, `X-Resolved-Realm == requested realm` (no client action).

### Frontend — consume the resolved realm

In `PlayerRouteView`, after a successful fetch, compare the `X-Resolved-Realm` header (added to the fetch's `responseHeaders` allowlist) to the requested `realm`:
- If different: `setRealm(resolved)` → `notifyRealmAutoSwitch()` → show the local toast → `history.replaceState` to rewrite `?realm=<resolved>` (reload/share-safe; not a route change) → `trackEvent('realm-fallback', { from, to })`.
- The scope/abort key is `${playerName}:${realm}`; calling `setRealm` re-runs the effect. Guard against a refetch loop: once `resolved_realm` matches the (new) realm, the branch is a no-op. Ensure the switch does not abort-then-refetch the already-loaded winner payload (the data for the resolved realm is in hand; treat the post-switch render as satisfied). Verify no infinite loop in tests.

### Frontend — user feedback

1. **Toast** — new `client/app/components/RealmFallbackNotice.tsx` mirroring `ConnectionHint` (`role="status" aria-live="polite"`, bordered pill, theme tokens). Copy: `"{name} isn't on {FROM} — showing {TO}."` Auto-dismiss ~6s; manual × to dismiss. Rendered by `PlayerRouteView` (it owns `from`/`to`/`name`), so no strings thread through context.
2. **Realm-chip flash** — a one-shot opacity glow on the `RealmSelector` button, armed only on fallback-driven switches (not ordinary manual switches). Add `@keyframes realm-selector-flash` + a `.realm-selector-flash` class in `globals.css` following the `.tab-attention-glow` opacity technique; honor `prefers-reduced-motion`.
3. **Wiring** — `RealmContext` gains `notifyRealmAutoSwitch()` that increments an integer `autoSwitchSignal`; `setRealm`'s signature stays unchanged (minimizes test churn). `RealmSelector` watches `autoSwitchSignal`: on change, arm the flash class, then disarm after the animation (~1.6s).

### Cost & safety

- Extra WG cost only on a current-realm miss: ≤ 2 `account/list` calls (id-only, cheap), under the global WG rate limiter. Zero extra cost on the common in-realm hit.
- Full-stats `update_player_data` runs exactly once, on the winner — no change to the expensive path's volume.
- Kill switch off → `get_object` reverts to today's single-realm behavior exactly.

## Out of scope (v1)

- Cross-realm autocomplete / search / suggestion endpoints (still single-realm).
- "Most active" tiebreak (rejected — may resolve to an unrelated same-named player).
- Realm-qualified route URLs like `/player/asia/<name>` (chosen "switch realm," not "redirect").

## Implementation plan

1. **Backend probe + fixes** (`views.py`, small helper): resolution order steps 3–6, realm-qualified negative cache, all-realms-miss cache, `resolved_realm` payload field + `X-Resolved-Realm` header, `CROSS_REALM_FALLBACK_ENABLED` setting.
2. **Backend tests**: found-in-other-realm (DB path + WG-probe path), ambiguity picks ASIA→EU→NA, all-realms miss → 404, kill-switch-off = legacy behavior, realm-qualified negative cache does not cross-poison, DB-before-WG (no WG call when the other-realm row already exists).
3. **Frontend context**: `notifyRealmAutoSwitch()` + `autoSwitchSignal` in `RealmContext`.
4. **Frontend consume + toast + flash**: `PlayerRouteView` mismatch handler, `RealmFallbackNotice`, `RealmSelector` flash, `globals.css` keyframes.
5. **Frontend tests**: `PlayerRouteView` switches realm + shows toast + `replaceState` on resolved mismatch, and does NOT loop; `RealmSelector` flashes on `autoSwitchSignal` bump; no flash on manual switch.
6. **Visual verify** (FE visual-verify recipe) — toast copy/placement, chip flash, reduced-motion, dark/light.
7. **Docs/doctrine**: update `CLAUDE.md` routing/caching notes as needed, `agents/doc_registry.json` entry for this runbook, env-var reference for the kill switch; run the release gate; version bump (**minor** — new user-facing behavior) + `deploy_to_droplet.sh` for both backend and frontend (frontend rebuild mandatory after the version bump).

## Validation checklist

- [ ] NA-selected `/player/<asia-only-name>` resolves, switches to ASIA, toast + flash fire, `?realm=asia` in URL after load.
- [ ] Reload of the rewritten URL loads directly on ASIA with no fallback probe.
- [ ] A name present in the current realm never triggers fallback (no extra WG calls).
- [ ] A name in EU + ASIA (not current) resolves to ASIA.
- [ ] A genuinely nonexistent name still shows "Player not found." and does not re-probe every load (all-realms-miss cache).
- [ ] `CROSS_REALM_FALLBACK_ENABLED=0` restores exact legacy behavior.
- [ ] Reduced-motion users get no flash animation; AT users hear the toast.

## Rollback

Set `CROSS_REALM_FALLBACK_ENABLED=0` (backend env) — instant revert to single-realm lookup. Frontend consume-side is inert when `resolved_realm` always equals the requested realm.

## Analytics / observability

Two independent counters answer "how often does a cross-realm redirect fire in prod":
- **Umami event `realm-fallback`** (preferred dashboard) — `trackEvent('realm-fallback', {from, to})` in `PlayerRouteView` on the resolved-realm mismatch. Kebab-case, low-cardinality realm props (≤6 combos). Registered in `runbook-umami-event-reference-2026-06-18.md`. Client-side, so ad-blockers undercount it.
- **Backend INFO log `cross-realm-redirect`** (ground truth) — `logger.info("cross-realm-redirect name=%s from=%s to=%s", ...)` in `get_object`, emitted only when the resolved realm differs from the requested one. Captures API/bot hits Umami never sees. Count in prod with: `journalctl -u battlestats-django | grep -c cross-realm-redirect` (or the Docker log equivalent).

## Implementation Record (2026-07-20)

Implemented on branch `cross-realm-fallback`. A code-assumption QA pass against the live source corrected four design details before any code was written:

| Design as drafted | Corrected to | Why |
|---|---|---|
| `settings.CROSS_REALM_FALLBACK_ENABLED` | `os.getenv('CROSS_REALM_FALLBACK_ENABLED', '1') == '1'` | Matches the project's point-of-use kill-switch pattern; no settings.py change. |
| `resolved_realm` payload field (authoritative) | `X-Resolved-Realm` **header** | `fetchSharedJson` already exposes allowlisted headers; no serializer/payload change. |
| Separate DB-first + WG-probe steps | One `_fetch_player_id_by_name` loop | That helper is already DB-first then WG internally (`players.py:101-115`). |
| Realm-qualify the negative cache | Realm-qualify **and restructure** | A per-realm miss must not short-circuit fallback; only the new name-level all-realms-miss key returns 404. |

**Accepted limitations (from advisor review):**
- **Error-vs-absence conflation.** `_fetch_player_id_by_name` returns `None` for both "not found" and a transient WG error, so a transient error in the requested realm can resolve to a *different* same-named player elsewhere. Accepted: `RETRY_TOTAL=2` + deterministic order keep it rare, the `realm-fallback` Umami event makes it observable, and the kill switch disables the path. Documented in the resolver docstring.
- **Worker occupancy on genuine misses.** A never-seen missing name now blocks a gunicorn worker on up to 3 sequential synchronous WG lookups (first request only — pass 2 after a switch is a DB cache hit). Bounded by the all-realms-miss cache to **once per name per 10-min TTL**, then a fast cached 404; the frontend bails at 15s; kill switch is the escape hatch. On the 2-vCPU box this was judged acceptable vs. threading a shorter probe timeout.

**Files changed:**
- `server/warships/views.py` — `RESOLVED_REALM_HEADER`, `CROSS_REALM_FALLBACK_ORDER`, `_cross_realm_fallback_enabled()`, realm-qualified `_missing_player_lookup_cache_key(name, realm)` (v1→v2), new `_all_realms_miss_cache_key(name)`, `PlayerViewSet._resolve_player_across_realms`, `get_object` except-branch rewrite + `self._resolved_realm`, `retrieve` header + resolved-realm refresh signals.
- `client/app/context/RealmContext.tsx` — `autoSwitchSignal` + `notifyRealmAutoSwitch` (both `useCallback`-stable); `setRealm` now `useCallback`.
- `client/app/components/PlayerRouteView.tsx` — reads `X-Resolved-Realm`, switches realm + notifies + `trackEvent('realm-fallback')` + `replaceState`; renders the notice above every branch so it survives the reload flash.
- `client/app/components/RealmFallbackNotice.tsx` — new toast (mirrors `ConnectionHint`; `role="status"`, auto-dismiss 6s, manual ✕).
- `client/app/components/RealmSelector.tsx` — one-shot flash armed on `autoSwitchSignal` (skips mount).
- `client/app/globals.css` — `.realm-selector-glow` opacity flash + reduced-motion guard.

**Tests (all green):**
- Backend `warships/tests/test_views.py`: rewrote `test_missing_player_lookup_uses_negative_cache_after_first_miss` for the all-realms-miss semantics; added `test_cross_realm_fallback_resolves_player_in_other_realm`, `_prefers_asia_over_eu`, `_disabled_stays_single_realm`, `test_cross_realm_negative_cache_not_cross_poisoned`. Full backend suite **802 passed, 2 skipped**.
- Frontend: new `PlayerRouteViewRealmFallback.test.tsx` (switch + notice + track + URL rewrite; no-switch when resolved==requested); added flash / no-flash-on-manual cases to `RealmSelector.test.tsx`. Full FE suite **343 passed**; `eslint` 0 errors; `tsc --noEmit` clean.

**Still open (pre-release):**
- [ ] Visual verify (FE visual-verify recipe): toast copy/placement, chip flash, dark/light, reduced-motion.
- [ ] Doctrine pre-commit; `CLAUDE.md` routing note; `agents/doc_registry.json` entry; kill switch in `ops-env-reference.md`.
- [ ] Release gate; **minor** version bump; deploy **both** backend and frontend (frontend rebuild mandatory after the bump).
- [x] `CROSS_REALM_FALLBACK_ENABLED` pinned on the droplet (default on) via `set_env_value` in `server/deploy/deploy_to_droplet.sh` (2026-07-20); catalogued in `ops-env-reference.md`. The switch is now operable in prod (set to `0` + redeploy/env-flip to disable).
