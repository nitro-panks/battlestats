"use client";

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import PlayerDetail from './PlayerDetail';
import RealmTopShipsTreemapSVG from './RealmTopShipsTreemapSVG';
import ShipLeaderboard, { type ShipLeaderboardHandle } from './ShipLeaderboard';
import type { PlayerData } from './entityTypes';
import { buildPlayerPath } from '../lib/entityRoutes';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import useClanHydrationPoll from './useClanHydrationPoll';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';

const CLAN_HYDRATION_POLL_LIMIT = 6;
const CLAN_HYDRATION_POLL_INTERVAL_MS = 2500;
// Backend caches the player payload for 6 h, so a 60-second client-side TTL lets
// SPA back-navigations to the landing page hit the in-memory cache (no network,
// no empty-state flash) while still catching meaningful churn within a session.
const LANDING_FETCH_TTL_MS = 60_000;

const PlayerSearch: React.FC = () => {
    const { realm } = useRealm();
    const router = useRouter();
    const [playerData, setPlayerData] = useState<PlayerData | null>(null);
    const [error, setError] = useState('');
    const [isLoadingPlayer, setIsLoadingPlayer] = useState(false);
    const lastSubmittedSearchRef = useRef<string>('');
    const shipLeaderboardRef = useRef<ShipLeaderboardHandle>(null);

    const fetchPlayerByName = useCallback(async (playerName: string): Promise<PlayerData | null> => {
        const { data } = await fetchSharedJson<PlayerData>(
            withRealm(`/api/player/${encodeURIComponent(playerName)}/`, realm),
            {
                label: `Player ${playerName}`,
                ttlMs: LANDING_FETCH_TTL_MS,
            },
        );
        return data;
    }, [realm]);

    const executePlayerSearch = useCallback(async (playerName: string) => {
        const trimmedPlayerName = playerName.trim();
        if (!trimmedPlayerName) {
            return;
        }

        lastSubmittedSearchRef.current = trimmedPlayerName;
        setIsLoadingPlayer(true);
        setError('');
        try {
            const data = await fetchPlayerByName(trimmedPlayerName);
            setPlayerData(data);
        } catch (err) {
            setError('Player not found');
            setPlayerData(null);
        } finally {
            setIsLoadingPlayer(false);
        }
    }, [fetchPlayerByName]);

    const handleSelectMember = useCallback(async (memberName: string) => {
        router.push(buildPlayerPath(memberName, realm));
    }, [router, realm]);

    useEffect(() => {
        const query = typeof window !== 'undefined'
            ? (new URLSearchParams(window.location.search).get('q') || '').trim()
            : '';
        if (!query || query === lastSubmittedSearchRef.current) {
            return;
        }

        void executePlayerSearch(query);
    }, [executePlayerSearch]);

    useEffect(() => {
        const onNavSearch = (event: Event) => {
            const customEvent = event as CustomEvent<{ query?: string }>;
            const query = customEvent.detail?.query?.trim() || '';
            if (!query) {
                return;
            }

            void executePlayerSearch(query);
        };

        window.addEventListener('navSearch', onNavSearch as EventListener);
        return () => window.removeEventListener('navSearch', onNavSearch as EventListener);
    }, [executePlayerSearch]);

    const { resetAttempts: resetClanHydrationAttempts } = useClanHydrationPoll({
        playerData,
        fetchPlayerByName,
        setPlayerData,
        pollLimit: CLAN_HYDRATION_POLL_LIMIT,
        intervalMs: CLAN_HYDRATION_POLL_INTERVAL_MS,
    });

    const handleBack = useCallback(() => {
        setPlayerData(null);
        setError('');
        setIsLoadingPlayer(false);
        resetClanHydrationAttempts();
    }, [resetClanHydrationAttempts]);

    useEffect(() => {
        const onReset = () => handleBack();
        window.addEventListener('resetApp', onReset);
        return () => window.removeEventListener('resetApp', onReset);
    }, [handleBack]);

    return (
        <div className="p-4">
            {playerData ? (
                <PlayerDetail
                    player={playerData}
                    onBack={handleBack}
                    onSelectMember={handleSelectMember}
                    isLoading={isLoadingPlayer}
                />
            ) : (
                <div>
                    {error && <p className="text-red-600 dark:text-red-400">{error}</p>}

                    {/* Realm most-played-ships treemap. Stays within the landing's
                        max-w-5xl column (the xl+ full-bleed breakout was reverted —
                        it glued the strip to the viewport's left edge; see
                        runbook-audience-device-optimization). */}
                    <div className="mt-2 pt-6">
                        <RealmTopShipsTreemapSVG
                            onSelect={(sel) => shipLeaderboardRef.current?.selectShip(sel)}
                        />
                    </div>

                    {/* Inline ship leaderboard: filter by tier+type, rank ships by
                        win rate, drill into any ship's player board in place. A
                        treemap tile click hands off here via the ref (in place);
                        tiles the board can't represent fall back to /ship/<id>. */}
                    <ShipLeaderboard ref={shipLeaderboardRef} />
                </div>
            )}
        </div>
    );
};

export default PlayerSearch;
