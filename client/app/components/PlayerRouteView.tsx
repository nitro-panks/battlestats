"use client";

import React, { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import PlayerDetail from './PlayerDetail';
import type { PlayerData } from './entityTypes';
import { buildClanPath, buildPlayerPath } from '../lib/entityRoutes';
import { PLAYER_ROUTE_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { trackEntityDetailView } from '../lib/visitAnalytics';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';


const LoadingPanel: React.FC<{ label: string; minHeight?: number }> = ({ label, minHeight = 220 }) => (
    <div
        className="flex animate-pulse items-center justify-center rounded-md border border-[var(--border)] bg-[var(--bg-surface)] text-sm text-[var(--accent-light)]"
        style={{ minHeight }}
    >
        {label}
    </div>
);


interface PlayerRouteViewProps {
    playerName: string;
}


const PlayerRouteView: React.FC<PlayerRouteViewProps> = ({ playerName }) => {
    const router = useRouter();
    const { realm } = useRealm();
    const [playerData, setPlayerData] = useState<PlayerData | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState('');
    const trackedPlayerIdRef = useRef<number | null>(null);

    useEffect(() => {
        let cancelled = false;

        const loadPlayer = async () => {
            setIsLoading(true);
            setError('');

            try {
                const { data } = await fetchSharedJson<PlayerData>(withRealm(`/api/player/${encodeURIComponent(playerName)}/`, realm), {
                    label: `Player ${playerName}`,
                    ttlMs: PLAYER_ROUTE_FETCH_TTL_MS,
                });
                if (!cancelled) {
                    setPlayerData(data);
                }
            } catch (fetchError) {
                console.error('Error loading player route:', fetchError);
                if (!cancelled) {
                    setPlayerData(null);
                    setError('Player not found.');
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
    }, [playerName, realm]);

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

    if (isLoading) {
        return <LoadingPanel label="Loading player profile..." minHeight={280} />;
    }

    if (!playerData) {
        return <p className="p-6 text-sm text-red-600">{error || 'Player not found.'}</p>;
    }

    return (
        <PlayerDetail
            player={playerData}
            onBack={() => router.push('/')}
            onSelectMember={(memberName) => router.push(buildPlayerPath(memberName))}
            onSelectClan={(clanId, clanName) => router.push(buildClanPath(clanId, clanName))}
            isLoading={false}
        />
    );
};


export default PlayerRouteView;