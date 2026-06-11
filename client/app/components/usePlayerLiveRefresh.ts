import { useEffect, useRef, useState } from 'react';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { withRealm } from '../lib/realmParams';
import type { PlayerData } from './entityTypes';

// Live-update contract surfaced by the player-detail endpoint (see
// server/warships/views.py _player_refresh_signals + runbook
// runbook-live-update-cooldown-2026-05-27.md).
export const PLAYER_REFRESH_PENDING_HEADER = 'X-Player-Refresh-Pending';
export const PLAYER_NEXT_REFRESH_HEADER = 'X-Player-Next-Refresh';

// Poll cadence: FAST at first, then back off. A visit refresh typically lands
// in ~2s (the upstream WG fetch + DB write), so polling every 2s for the first
// few attempts clears the "Updating…" pill promptly once it's done — instead of
// leaving it up for the better part of a 6s tick after the data is already
// fresh (the "looks like it hung" complaint). After the fast window we settle to
// a steady 6s so a slow/queued/failed refresh doesn't hammer the endpoint.
const POLL_FAST_INTERVAL_MS = 2_000;
const POLL_SLOW_INTERVAL_MS = 3_000;
const POLL_FAST_ATTEMPTS = 6; // first ~12s polled at 2s spacing
const POLL_LIMIT = 62; // ~3 min ceiling (6×2s + 56×3s = 180s) — long enough to
// catch a queued refresh, bounded so a slow/failed one can't poll forever. The
// tighter slow cadence (3s, was 6s) and longer fast window (6 attempts, was 4)
// shave up to ~3s of pure waiting off each resolve once the refresh actually
// lands. Polls are lightweight header checks; the page only re-hydrates ONCE,
// when the refresh actually lands.

// Delay before the (attempt+1)-th poll. `attempt` is the number of polls already
// completed (0 before the first), so the initial delay uses pollDelayMs(0).
const pollDelayMs = (attempt: number): number =>
    attempt < POLL_FAST_ATTEMPTS ? POLL_FAST_INTERVAL_MS : POLL_SLOW_INTERVAL_MS;

export type LiveRefreshPhase = 'loading' | 'cooldown';

export interface LiveRefreshState {
    phase: LiveRefreshPhase;
    secondsRemaining: number;
    refreshNonce: number;
}

export const parsePendingHeader = (value: string | null | undefined): boolean => value === 'true';

export const parseNextRefreshHeader = (value: string | null | undefined): number | null => {
    if (!value) return null;
    const epoch = Number.parseInt(value, 10);
    return Number.isFinite(epoch) ? epoch : null;
};

export const computeSecondsRemaining = (nextRefreshEpoch: number | null): number => {
    if (!nextRefreshEpoch) return 0;
    return Math.max(0, Math.round(nextRefreshEpoch - Date.now() / 1000));
};

interface UsePlayerLiveRefreshParams {
    playerName: string;
    realm: string;
    initialPending: boolean;
    initialNextRefresh: number | null;
    // Called with the freshly-fetched player payload on each poll so the page
    // re-hydrates (header summary updates from the prop; charts re-fetch via the
    // bumped refreshNonce).
    onRehydrate: (data: PlayerData) => void;
}

/**
 * Drives the player page's visit-based live update:
 *  - phase "loading": a >15-min-stale visit triggered a server refresh; poll the
 *    player endpoint (cache-busted) until X-Player-Refresh-Pending clears,
 *    re-hydrating on each poll, then transition to cooldown.
 *  - phase "cooldown": tick a server-anchored countdown to X-Player-Next-Refresh
 *    (every visitor sees the same target). At 0 we auto-trigger an in-place
 *    refresh (re-enter the loading poll → rehydrate → reset the countdown, no
 *    page reload). The server-side cooldown lock + visit dedup still gate the
 *    actual upstream pull, so an open/idle tab can't over-refresh.
 */
export const usePlayerLiveRefresh = ({
    playerName,
    realm,
    initialPending,
    initialNextRefresh,
    onRehydrate,
}: UsePlayerLiveRefreshParams): LiveRefreshState => {
    const [pending, setPending] = useState(initialPending);
    const [nextRefresh, setNextRefresh] = useState<number | null>(initialNextRefresh);
    const [secondsRemaining, setSecondsRemaining] = useState(() => computeSecondsRemaining(initialNextRefresh));
    const [refreshNonce, setRefreshNonce] = useState(0);

    const onRehydrateRef = useRef(onRehydrate);
    useEffect(() => {
        onRehydrateRef.current = onRehydrate;
    }, [onRehydrate]);

    // Reset when the initial signals change (new player / realm / fresh mount fetch).
    useEffect(() => {
        setPending(initialPending);
        setNextRefresh(initialNextRefresh);
        setSecondsRemaining(computeSecondsRemaining(initialNextRefresh));
    }, [playerName, realm, initialPending, initialNextRefresh]);

    // Poll-to-rehydrate while a refresh is in flight.
    useEffect(() => {
        if (!pending) {
            return;
        }
        let cancelled = false;
        let attempt = 0;
        let timer: ReturnType<typeof setTimeout> | null = null;

        const poll = async () => {
            attempt += 1;
            try {
                const { data, headers } = await fetchSharedJson<PlayerData>(
                    withRealm(`/api/player/${encodeURIComponent(playerName)}/`, realm),
                    {
                        label: `PlayerLiveRefresh ${playerName}`,
                        cacheKey: `player-live:${playerName}:${realm}:${attempt}`,
                        responseHeaders: [PLAYER_REFRESH_PENDING_HEADER, PLAYER_NEXT_REFRESH_HEADER],
                    },
                );
                if (cancelled) return;

                const stillPending = parsePendingHeader(headers[PLAYER_REFRESH_PENDING_HEADER]);
                const next = parseNextRefreshHeader(headers[PLAYER_NEXT_REFRESH_HEADER]);

                if (!stillPending) {
                    // Refresh landed — re-hydrate exactly ONCE (swap in fresh data,
                    // bump the nonce so charts re-fetch a single time) and settle
                    // into the cooldown countdown. Re-hydrating on every interim
                    // poll is what caused the "loads repeatedly" loop.
                    onRehydrateRef.current(data);
                    setRefreshNonce((nonce) => nonce + 1);
                    if (next !== null) setNextRefresh(next);
                    setPending(false);
                    setSecondsRemaining(computeSecondsRemaining(next));
                    return;
                }

                if (attempt < POLL_LIMIT) {
                    // Still refreshing — keep checking the header, but do NOT touch
                    // playerData/refreshNonce (no re-render storm, no chart reloads).
                    timer = setTimeout(poll, pollDelayMs(attempt));
                } else {
                    // Gave up waiting — stop the spinner without forcing a reload;
                    // the countdown reflects whatever freshness we have.
                    if (next !== null) setNextRefresh(next);
                    setPending(false);
                    setSecondsRemaining(computeSecondsRemaining(next));
                }
            } catch {
                if (!cancelled) {
                    // Fail open into cooldown rather than spin on a broken refresh.
                    setPending(false);
                }
            }
        };

        timer = setTimeout(poll, pollDelayMs(0));
        return () => {
            cancelled = true;
            if (timer) clearTimeout(timer);
        };
    }, [pending, playerName, realm]);

    // Countdown tick during cooldown (recompute from the absolute target each
    // tick, so it's correct after tab sleep / throttling). When the cooldown
    // elapses we AUTO-TRIGGER an in-place refresh instead of parking on "Update
    // available": flipping `pending` re-enters the poll loop above, which
    // re-fetches the (cache-busted) endpoint — the server dispatches the
    // visit-driven refresh when stale (views.py) — then rehydrates via
    // onRehydrate + a single refreshNonce bump and resets the countdown, all
    // WITHOUT a page reload (SPA-style; reloading is disruptive).
    //
    // Fire only on the >0 → 0 down-crossing. The detector re-arms on any tick
    // where `remaining > 0` (a "fresh" anchor), so:
    //  - a successful refresh sets a future anchor (~15 min) → arms → fires again
    //    at the next expiry (continuous in-place updates while the page is open);
    //  - a refresh that fails to land leaves us parked at 0 (prev stays 0, never
    //    >0) → does NOT hot-loop re-firing every tick; it waits for a real anchor.
    useEffect(() => {
        if (pending) {
            return;
        }
        let prevRemaining = secondsRemaining;
        const intervalId = window.setInterval(() => {
            const remaining = computeSecondsRemaining(nextRefresh);
            setSecondsRemaining(remaining);
            if (nextRefresh !== null && prevRemaining > 0 && remaining <= 0) {
                setPending(true);
            }
            prevRemaining = remaining;
        }, 1_000);
        return () => window.clearInterval(intervalId);
        // `secondsRemaining` is read ONCE at effect setup to seed the crossing
        // detector; later values are tracked via the local `prevRemaining`
        // closure, so omitting it from deps does NOT cause a stale read — and
        // including it would re-create the interval every tick (churn).
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pending, nextRefresh]);

    return {
        phase: pending ? 'loading' : 'cooldown',
        secondsRemaining,
        refreshNonce,
    };
};

export default usePlayerLiveRefresh;
