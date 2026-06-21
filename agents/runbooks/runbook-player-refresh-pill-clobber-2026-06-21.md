# Runbook: Player-page "Updating…" pill lingers on cold visits (`battles_updated_at` clobber)

_Created: 2026-06-21_
_Context: A live report that `https://battlestats.online/player/HMSHOOD06?realm=na` "hung on Updating…" — traced to the FE pill staying lit ~65s while the backend had the data ready in ~2s._
_Status: FIX IMPLEMENTED 2026-06-21 — scoped `update_fields` save in `data.py` `update_ranked_data` (kills a concurrent read-modify-write clobber of `battles_updated_at`); regression test added._

## Purpose

Explain why the player-header "Updating…" pill can stay visible for up to ~3 minutes on a cold
visit even though the backend refresh completed in ~2 seconds, and document the fix. The backend is
**not** slow; the pill's freshness anchor (`battles_updated_at`) was being **reset backwards** by a
second writer's concurrent full-row save, re-arming the client poll loop. Read this when
investigating perceived player-page "slowness/hangs", before touching `battles_updated_at` /
`_player_refresh_signals`, or before adding a bare `player.save()` to any visit-dispatched task.

## Timeline (HMSHOOD06, realm na, 2026-06-21, operator visit)

Reconstructed from nginx access log + `battlestats-celery-*` journals.

- **20:37:17** — page load. Backend fires the cold fan-out on different fork workers concurrently:
  `update_battle_data_task` + `update_player_data_task` (ForkPoolWorker-47),
  `update_player_clan_battle_data_task` + `update_ranked_data_task` (ForkPoolWorker-46). **All
  succeed in <2s each.** `update_battle_data` does the EXPENSIVE ships/stats fetch and stamps
  `battles_updated_at = now()` at **20:37:18.288** via a scoped save.
- **20:37:17 → 20:38:22** — the FE polls `GET /api/player/HMSHOOD06?realm=na` **every 2–3s for
  ~65s** (~25 requests, all HTTP 200). The "Updating…" pill stays lit the entire time.
- **20:38:19** — a **second** `update_battle_data` round fires, logs *"Battles data empty or
  outdated: fetching new data"* (the freshness check found the anchor stale **again**, 62s after
  round 1 made it fresh), re-stamps `now()`, and polling stops ~3s later.

Health during the window: all Celery queues ~0 (default/hydration/background empty), **no clan
crawl active**, no 407s on this player. The backend was idle and fast; the latency was entirely the
client pill waiting on a `pending` header that would not clear.

## Root cause — concurrent read-modify-write race on `battles_updated_at`

The pill is driven by the `X-Player-Refresh-Pending` response header, computed by
`_player_refresh_signals` (`server/warships/views.py:94`), which anchors on **one field only**:

```python
pending = battles_updated_at is None or (now - battles_updated_at) > window   # window = 15 min
```

`update_battle_data` correctly stamps `battles_updated_at = now()` with a **scoped**
`save(update_fields=['battles_json', 'battles_updated_at'])` (`data.py:2225`, `:2278`). The problem
is a **second task that runs concurrently on the same visit and saves the whole row**:

- **`update_ranked_data`** (`data.py:4265`, via `update_ranked_data_task`) does
  `player = Player.objects.get(...)` at the top (snapshotting **all** fields, including the
  *pre-refresh* `battles_updated_at`), then makes a slow ranked WG fetch, then did a **bare
  `player.save()`** (`data.py:4281`, `:4311` — no `update_fields`). A bare save writes **every**
  field from the stale in-memory snapshot back to the row.

The interleaving from the trace (single player, two fork workers):

```
17,170  worker-47  update_battle_data: Player.get()            (battles_updated_at = OLD)
17,901  worker-46  update_ranked_data: Player.get()            (snapshots battles_updated_at = OLD)
18,288  worker-47  update_battle_data: save(update_fields=…)   battles_updated_at = now()  ✅ fresh
18,733  worker-46  update_ranked_data: save()  ← bare          battles_updated_at = OLD    ❌ REVERTED
```

`update_ranked_data` loaded the row **before** the now()-write landed and saved it **after**, so its
full save reverted `battles_updated_at` to the pre-refresh value. `pending` flipped back to `true`,
the client polled (2–3s cadence, ~3-min ceiling, v2.8.0 orchestration) until the 20:38:19
`update_battle_data` round happened to re-stamp `now()`. Classic lost-update race; nothing to do with
"slow backend."

Ruled out during diagnosis (don't re-chase these):

- **`update_player_data` is NOT the culprit here.** Its WG-stats write at `data.py:4821` is real
  (it sets `battles_updated_at` from WG's `account/info.stats_updated_at`, which lags), **but** the
  function early-returns at `data.py:4774` (`last_fetch` within ~23h) for any recently-fetched
  player — and the trace shows it returning in 0.017s (no WG fetch, no save). It never reached the
  write. (It *can* clobber on a genuinely-cold >23h player; that is a **separate, secondary** path —
  see Follow-ups.)
- **`update_player_clan_battle_data`** (`fetch_player_clan_battle_seasons`, `data.py:3925`) does
  **no** full `player.save()`, so it is not a clobber source.
- **`save_player(core_only=True)`** (floor/snapshot path) writes the stats block only `if not
  core_only`, so it does not touch the anchor.

### Why `battles_updated_at` is overloaded (context for any future fix)

`battles_updated_at` is read as a **source-of-truth clock** beyond the pill — `data.py:224–228`
(derived `tiers/type/randoms_json` regeneration via `_has_newer_source_timestamp`), `data.py:844`/
`:857` (efficiency-rank staleness `max()`), enrichment (`enrich_player_data.py` +
`retry_empty_enrichments.py` as "last attempt time"), and the `X-Battles-Updated-At` header
(`views.py:557`). So the field must keep advancing — the bug is purely that a **concurrent full-save
moved it backwards**, which the scoped-save fix eliminates without changing any of those semantics.

## Fix (implemented 2026-06-21)

Scope `update_ranked_data`'s two saves to the columns it actually owns:

```python
player.save(update_fields=['ranked_json', 'ranked_updated_at', 'ranked_last_season_id'])
```

at both `data.py:4281` (no-rank_info branch) and `data.py:4311` (main branch). The task now writes
only its ranked columns, so a concurrent `update_battle_data` now()-write on `battles_updated_at`
survives and the pill clears as soon as the ~2s battle refresh lands. Minimal, behavior-preserving:
the ranked payload is still written exactly as before.

## Validation

- **Regression test** — `server/warships/tests/test_ranked_data_scoped_save.py` reproduces the race
  deterministically: a player starts with a stale `battles_updated_at`; the patched ranked WG fetch
  has a **side effect that writes a fresh `battles_updated_at` straight to the DB row** (standing in
  for the concurrent `update_battle_data`); then `update_ranked_data` runs. Asserts the fresh value
  **survives** (was reverted before the fix) while the ranked payload is still persisted. Covers both
  save sites (no-rank_info and main branches). Verified to **fail on the unfixed code** (reverts to
  the stale value) and **pass with the fix**.
- **Backend suite**: `DB_ENGINE=sqlite3 DJANGO_SECRET_KEY=test-key python -m pytest warships/tests/ --nomigrations`.
- **Live re-verify after deploy**: cold-load a stale long-tail player; confirm the "Updating…" pill
  clears within a couple seconds (one poll cycle after the ~2s `update_battle_data`), not ~60s, and
  that `GET /api/player/<name>/?realm=na` returns `x-player-refresh-pending: false` promptly. The
  nginx access log should show the poll loop stop after a few requests, not ~25.
- **Backend test suite**: `DB_ENGINE=sqlite3 python -m pytest warships/tests/ --nomigrations`.
- **Live re-verify after deploy**: cold-load a stale long-tail player; confirm the "Updating…" pill
  clears within a couple seconds (one poll cycle after the ~2s `update_battle_data`), not ~60s, and
  that `GET /api/player/<name>/?realm=na` returns `x-player-refresh-pending: false` promptly. The
  nginx access log should show the poll loop stop after a few requests, not ~25.

## Next steps / follow-ups

1. **Ship + deploy** this patch (backend); re-verify live per Validation.
2. **Audit the other full `player.save()` sites for the same race (recommended).** Any task
   dispatched concurrently with a visit refresh that does a bare `player.save()` after a slow fetch
   can lost-update *any* field, not just `battles_updated_at`. `grep -n "player\.save()"
   server/warships/data.py` shows ~9 sites (e.g. `4342`, `4607`, `4668`, `4760`); each should be
   scoped to `update_fields` or re-read-before-save. Treat as a small follow-up sweep, not part of
   this slice.
3. **Secondary clobber path — genuinely-cold (>23h) players.** When `update_player_data` does NOT
   early-return (`data.py:4774`), its full save at `data.py:4879` writes `battles_updated_at` from
   WG's `account/info.stats_updated_at` (`data.py:4821`) — an older, **tz-aware**
   `datetime.fromtimestamp(ts, tz=utc)` value (the rest of the codebase uses naive `datetime.now()`;
   `USE_TZ=False`). That can move the anchor backwards for a cold player even without the ranked race.
   Not triggered in this trace (the player was <23h fresh). If it surfaces, the fix is a monotonic +
   naive guard at `4821` (only advance `battles_updated_at`; build the WG value with
   `datetime.utcfromtimestamp`). Deferred — out of this slice.
4. **Consider a dedicated pill anchor (deeper, deferred).** The design smell is that one column means
   both "visit battle-refresh clock" and "upstream stats source clock". A separate monotonic
   `last_visit_refresh_at` the pill owns would end the whole class of clobbers. Only revisit if the
   dual-meaning bites again.
5. Relation: this is the persistent-cause sequel to the FE-pill *artifact* first framed as a
   poll-cadence issue — see `runbook-player-refresh-latency-2026-06-10.md` and the
   `project_player_page_loading_pill_diagnosis` memory.

## Related

- `runbook-player-fetch-orchestration-2026-06-21.md` — the client poll loop (`usePlayerLiveRefresh`)
  and the 2–3s degradation-aware cadence that does the polling.
- `runbook-player-refresh-latency-2026-06-10.md` — earlier latency tiers; the pill-artifact framing.
- `server/warships/views.py:_player_refresh_signals` — the pending-header contract (anchor field).
- `server/warships/data.py` — `update_battle_data` (scoped `now()` writer) and `update_ranked_data`
  (the fixed task; was a bare-save clobberer).
