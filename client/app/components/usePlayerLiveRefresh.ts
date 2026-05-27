import { useEffect, useRef, useState } from 'react';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { withRealm } from '../lib/realmParams';
import type { PlayerData } from './entityTypes';

// Live-update contract surfaced by the player-detail endpoint (see
// server/warships/views.py _player_refresh_signals + runbook
// runbook-live-update-cooldown-2026-05-27.md).
export const PLAYER_REFRESH_PENDING_HEADER = 'X-Player-Refresh-Pending';
export const PLAYER_NEXT_REFRESH_HEADER = 'X-Player-Next-Refresh';

const POLL_INTERVAL_MS = 5_000;
const POLL_LIMIT = 24; // ~2 min ceiling so a slow/failed refresh can't poll forever

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
 *    (every visitor sees the same target). At 0 a new pull becomes possible on
 *    the next visit — we never auto-fire, matching the server-side lock.
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

                onRehydrateRef.current(data);
                setRefreshNonce((nonce) => nonce + 1);

                const stillPending = parsePendingHeader(headers[PLAYER_REFRESH_PENDING_HEADER]);
                const next = parseNextRefreshHeader(headers[PLAYER_NEXT_REFRESH_HEADER]);
                if (next !== null) {
                    setNextRefresh(next);
                }

                if (stillPending && attempt < POLL_LIMIT) {
                    timer = setTimeout(poll, POLL_INTERVAL_MS);
                } else {
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

        timer = setTimeout(poll, POLL_INTERVAL_MS);
        return () => {
            cancelled = true;
            if (timer) clearTimeout(timer);
        };
    }, [pending, playerName, realm]);

    // Countdown tick during cooldown (recompute from the absolute target each
    // tick, so it's correct after tab sleep / throttling).
    useEffect(() => {
        if (pending) {
            return;
        }
        const intervalId = window.setInterval(() => {
            setSecondsRemaining(computeSecondsRemaining(nextRefresh));
        }, 1_000);
        return () => window.clearInterval(intervalId);
    }, [pending, nextRefresh]);

    return {
        phase: pending ? 'loading' : 'cooldown',
        secondsRemaining,
        refreshNonce,
    };
};

export default usePlayerLiveRefresh;
