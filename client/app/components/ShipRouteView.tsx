"use client";

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { buildPlayerPath, parseShipIdFromRouteSegment } from '../lib/entityRoutes';
import { useRealm } from '../context/RealmContext';
import wrColor from '../lib/wrColor';


const SHIP_LEADERBOARD_FETCH_TTL_MS = 900_000; // 15 min — mirrors the backend cache


interface ShipLeaderboardPlayer {
    rank: number;
    player_name: string;
    win_rate: number;
    battles: number;
}

interface ShipLeaderboard {
    realm: string;
    window_days: number;
    captured_on: string | null;
    ship: {
        ship_id: number;
        name: string;
        tier: number | null;
        ship_type: string | null;
        nation: string;
        is_premium: boolean;
    };
    players: ShipLeaderboardPlayer[];
}


const LoadingPanel: React.FC<{ label: string }> = ({ label }) => (
    <div
        className="flex animate-pulse items-center justify-center rounded-md border border-[var(--border)] bg-[var(--bg-surface)] text-sm text-[var(--accent-light)]"
        style={{ minHeight: 220 }}
    >
        {label}
    </div>
);


interface ShipRouteViewProps {
    shipSlug: string;
}


const ShipRouteView: React.FC<ShipRouteViewProps> = ({ shipSlug }) => {
    const { realm } = useRealm();
    const shipId = parseShipIdFromRouteSegment(shipSlug);
    const [data, setData] = useState<ShipLeaderboard | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState('');

    useEffect(() => {
        if (shipId == null) {
            setData(null);
            setIsLoading(false);
            setError('Ship not found.');
            return;
        }

        let cancelled = false;
        setIsLoading(true);
        setError('');

        fetchSharedJson<ShipLeaderboard>(
            `/api/realm/${realm}/ship/${shipId}/leaderboard`,
            {
                label: `ShipLeaderboard:${realm}:${shipId}`,
                ttlMs: SHIP_LEADERBOARD_FETCH_TTL_MS,
                cacheKey: `ship-lb:${realm}:${shipId}`,
            },
        )
            .then(({ data: payload }) => {
                if (!cancelled) {
                    setData(payload);
                    setIsLoading(false);
                }
            })
            .catch(() => {
                if (!cancelled) {
                    setData(null);
                    setError('Ship standings not found.');
                    setIsLoading(false);
                }
            });

        return () => { cancelled = true; };
    }, [shipId, realm]);

    if (isLoading) {
        return <LoadingPanel label="Loading ship standings…" />;
    }

    if (error || !data) {
        return (
            <div className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] p-6 text-sm text-[var(--text-muted)]">
                {error || 'Ship standings not found.'}
            </div>
        );
    }

    const { ship, players } = data;
    const tierLabel = ship.tier ? `Tier ${ship.tier}` : '';
    const subtitle = [tierLabel, ship.ship_type, ship.nation].filter(Boolean).join(' · ');

    return (
        <section className="mx-auto max-w-3xl">
            <header className="mb-4 border-b border-[var(--border)] pb-3">
                <div className="flex flex-wrap items-baseline gap-3">
                    <h1 className="text-3xl font-semibold tracking-tight text-[var(--accent-dark)]">
                        {ship.name}
                    </h1>
                    <span className="text-sm text-[var(--text-muted)]">{subtitle}</span>
                </div>
                <p className="mt-2 text-xs uppercase tracking-wide text-[var(--text-muted)]">
                    {realm.toUpperCase()} · best players · last {data.window_days} days
                </p>
            </header>

            {players.length === 0 ? (
                <div className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] p-6 text-sm text-[var(--text-muted)]">
                    Not enough players ranked this ship in the last {data.window_days} days yet. Check back soon.
                </div>
            ) : (
                <table className="text-sm">
                    <thead>
                        <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wide text-[var(--text-muted)]">
                            <th className="py-2 pr-3">#</th>
                            <th className="py-2 pr-8">Player</th>
                            <th className="py-2 pr-8">Win rate</th>
                            <th className="py-2">Battles</th>
                        </tr>
                    </thead>
                    <tbody>
                        {players.map((p) => (
                            <tr key={p.rank}>
                                <td className="py-1.5 pr-3 tabular-nums text-[var(--text-muted)]">{p.rank}</td>
                                <td className="py-1.5 pr-8">
                                    <Link
                                        href={buildPlayerPath(p.player_name, realm)}
                                        className="text-[var(--accent-mid)] hover:underline"
                                    >
                                        {p.player_name}
                                    </Link>
                                </td>
                                <td className="py-1.5 pr-8 tabular-nums font-semibold" style={{ color: wrColor(p.win_rate) }}>
                                    {p.win_rate.toFixed(1)}%
                                </td>
                                <td className="py-1.5 tabular-nums text-[var(--text-muted)]">
                                    {p.battles.toLocaleString()}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}
        </section>
    );
};

export default ShipRouteView;
