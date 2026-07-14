"use client";

import React, { useEffect, useRef, useState } from 'react';
import PlayerDetail from './PlayerDetail';
import LoadingPanel from './LoadingPanel';
import { prefetchBattleHistory } from './BattleHistoryCard';
import type { PlayerData } from './entityTypes';
import { PLAYER_ROUTE_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson, SharedJsonFetchError, isAbortError } from '../lib/sharedJsonFetch';
import { PlayerRequestScopeProvider } from '../context/PlayerRequestScopeContext';
import {
    PLAYER_NEXT_REFRESH_HEADER,
    PLAYER_REFRESH_PENDING_HEADER,
    parseNextRefreshHeader,
    parsePendingHeader,
    usePlayerLiveRefresh,
} from './usePlayerLiveRefresh';
import { trackEntityDetailView } from '../lib/visitAnalytics';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';


interface PlayerRouteViewProps {
    playerName: string;
}


const PlayerRouteView: React.FC<PlayerRouteViewProps> = ({ playerName }) => {
    const { realm } = useRealm();
    const [playerData, setPlayerData] = useState<PlayerData | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState('');
    const [initialPending, setInitialPending] = useState(false);
    const [initialNextRefresh, setInitialNextRefresh] = useState<number | null>(null);
    const trackedPlayerIdRef = useRef<number | null>(null);

    // One AbortController for the whole page, scoped to (playerName, realm).
    // Created during render so the signal is available synchronously to every
    // child fetch; aborted in the cleanup below when the scope changes or the
    // page unmounts — cancelling ALL of this player's in-flight + queued requests
    // at once (frees the queue for the page the user actually navigated to).
    const scopeKey = `${playerName}:${realm}`;
    const scopeRef = useRef<{ key: string; controller: AbortController } | null>(null);
    if (!scopeRef.current || scopeRef.current.key !== scopeKey) {
        scopeRef.current = { key: scopeKey, controller: new AbortController() };
    }
    const requestSignal = scopeRef.current.controller.signal;

    useEffect(() => {
        const controller = scopeRef.current!.controller;
        return () => controller.abort();
    }, [scopeKey]);

    useEffect(() => {
        let cancelled = false;

        const loadPlayer = async () => {
            setIsLoading(true);
            setError('');

            // Kick the battle-history fetch in PARALLEL with the profile fetch.
            // The card mounts only after the profile resolves + PlayerDetail
            // renders, so without this its fetch starts serially after the
            // profile; firing it here moves that round-trip off the critical
            // path. The card's own fetch dedupes onto this (shared cacheKey).
            prefetchBattleHistory(playerName, realm, requestSignal);

            try {
                const { data, headers } = await fetchSharedJson<PlayerData>(withRealm(`/api/player/${encodeURIComponent(playerName)}/`, realm), {
                    label: `Player ${playerName}`,
                    ttlMs: PLAYER_ROUTE_FETCH_TTL_MS,
                    signal: requestSignal,
                    priority: 'critical', // the page-blocking fetch — first in the queue
                    responseHeaders: [PLAYER_REFRESH_PENDING_HEADER, PLAYER_NEXT_REFRESH_HEADER],
                    // Short-backoff retry on a transient 5xx / network blip ONLY so a
                    // single stalled upstream (the 502-on-the-request-thread tail) no
                    // longer strands the page. A real 404 is NOT retried (see
                    // sharedJsonFetch isRetriable) — it falls straight through to the
                    // terminal "not found" branch below.
                    retry: { attempts: 2, backoffMs: 600 },
                });
                if (!cancelled) {
                    setPlayerData(data);
                    setInitialPending(parsePendingHeader(headers[PLAYER_REFRESH_PENDING_HEADER]));
                    setInitialNextRefresh(parseNextRefreshHeader(headers[PLAYER_NEXT_REFRESH_HEADER]));
                }
            } catch (fetchError) {
                // Navigated away / switched realm mid-flight — benign, leave state alone.
                if (isAbortError(fetchError)) {
                    return;
                }
                console.error('Error loading player route:', fetchError);
                if (!cancelled) {
                    setPlayerData(null);
                    // Distinguish a genuine 4xx (the player really is missing) from a
                    // transient server/network failure (5xx, dropped connection,
                    // non-JSON 5xx). Only the former is the terminal "not found"
                    // state; the latter, post-retry-exhaustion, gets non-terminal
                    // copy so a momentary backend stall doesn't masquerade as a
                    // deleted account.
                    const status = fetchError instanceof SharedJsonFetchError ? fetchError.status : undefined;
                    const isClientError = status !== undefined && status >= 400 && status < 500;
                    setError(isClientError
                        ? 'Player not found.'
                        : 'Temporarily unavailable. Please refresh to try again.');
                }
            } finally {
                if (!cancelled) {
                    setIsLoading(false);
                }
            }
        };

        void loadPlayer();
        return () => {
            cancelled = true;
        };
    }, [playerName, realm, requestSignal]);

    useEffect(() => {
        if (!playerData) {
            trackedPlayerIdRef.current = null;
            return;
        }

        if (trackedPlayerIdRef.current === playerData.player_id) {
            return;
        }

        trackedPlayerIdRef.current = playerData.player_id;
        void trackEntityDetailView({
            entityType: 'player',
            entityId: playerData.player_id,
            entityName: playerData.name,
            entitySlug: playerName,
        });
    }, [playerData, playerName]);

    const liveRefresh = usePlayerLiveRefresh({
        playerName,
        realm,
        initialPending,
        initialNextRefresh,
        onRehydrate: setPlayerData,
        signal: requestSignal,
    });

    if (isLoading) {
        return <LoadingPanel label="Loading player profile..." minHeight={280} />;
    }

    if (!playerData) {
        return <p className="p-6 text-sm text-red-600">{error || 'Player not found.'}</p>;
    }

    return (
        <PlayerRequestScopeProvider value={requestSignal}>
            <PlayerDetail
                player={playerData}
                isLoading={false}
                refreshStatus={{ phase: liveRefresh.phase, secondsRemaining: liveRefresh.secondsRemaining }}
                refreshNonce={liveRefresh.refreshNonce}
            />
        </PlayerRequestScopeProvider>
    );
};


export default PlayerRouteView;