"use client";

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { buildPlayerPath, parseShipIdFromRouteSegment } from '../lib/entityRoutes';
import { useRealm } from '../context/RealmContext';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCircleInfo } from '@fortawesome/free-solid-svg-icons';
import wrColor from '../lib/wrColor';
import TopShipIcon from './TopShipIcon';
import { nextWindowOpenMs, formatCountdown, formatSeasonLabel } from '../lib/shipSeason';
import { trackEvent } from '../lib/umami';

const RANKING_TOOLTIP = "Ranked by a blend of win rate, average damage, and kills per battle (win rate weighted most), each tempered for games played (empirical-Bayes shrinkage) so a short hot streak doesn't outrank a high-volume player. Shows the top 15 for the window.";


const SHIP_LEADERBOARD_FETCH_TTL_MS = 900_000; // 15 min — mirrors the backend cache
// Display cap. The backend already limits each board to SHIP_BADGE_LIST_SIZE
// (15), but this guarantees the page never shows more than the top 15 even if a
// payload was cached when the limit was higher.
const MAX_VISIBLE_PLAYERS = 15;


interface ShipLeaderboardPlayer {
    rank: number;
    player_name: string;
    win_rate: number;
    battles: number;
    avg_damage: number;
    kills_per_battle: number;
}

interface ShipLeaderboard {
    realm: string;
    window_days: number;
    captured_on: string | null;
    // Fixed-season boundaries (authoritative; older cached payloads may omit them,
    // in which case the client falls back to lib/shipSeason.ts).
    season_start?: string | null;
    season_end?: string | null;
    next_window_open?: string | null;
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
    // Client-only clock for the "next window opens" countdown. Starts null so the
    // server and first client render agree (no hydration mismatch); the effect
    // fills it in and ticks once a minute.
    const [nowMs, setNowMs] = useState<number | null>(null);

    useEffect(() => {
        setNowMs(Date.now());
        const id = setInterval(() => setNowMs(Date.now()), 60_000);
        return () => clearInterval(id);
    }, []);

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
                    trackEvent('ship-page-view', {
                        ship_id: shipId,
                        ship_name: payload.ship.name,
                        realm,
                    });
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

    // Fixed-season boundaries from the payload (authoritative); ISO date-only
    // strings parse as UTC midnight. Fall back to lib/shipSeason.ts when an older
    // cached payload omits them.
    const seasonStartMs = data.season_start ? Date.parse(data.season_start) : null;
    const seasonEndMs = data.season_end ? Date.parse(data.season_end) : null;
    const seasonLabel = seasonStartMs != null && seasonEndMs != null
        ? formatSeasonLabel(seasonStartMs, seasonEndMs) : null;
    const nextOpenMs = data.next_window_open
        ? Date.parse(data.next_window_open)
        : (nowMs !== null ? nextWindowOpenMs(nowMs) : null);

    return (
        <section className="mx-auto max-w-3xl">
            <header className="mb-4">
                <div className="flex flex-wrap items-baseline gap-3">
                    <h1 className="text-3xl font-semibold tracking-tight text-[var(--accent-dark)]">
                        {ship.name}
                    </h1>
                    <span className="text-sm text-[var(--text-muted)]">{subtitle}</span>
                </div>
                <p className="mt-2 flex flex-wrap items-center gap-1.5 text-xs uppercase tracking-wide text-[var(--text-muted)]">
                    {realm.toUpperCase()} · best players · {seasonLabel ? `season ${seasonLabel}` : `last ${data.window_days} days`} ·
                    <span
                        title={RANKING_TOOLTIP}
                        aria-label={RANKING_TOOLTIP}
                        className="inline-flex cursor-help"
                    >
                        <FontAwesomeIcon icon={faCircleInfo} aria-hidden="true" />
                    </span>
                </p>
                {nowMs !== null && nextOpenMs !== null && (
                    <p className="mt-1 text-xs text-[var(--text-muted)]">
                        Next standings window opens in{' '}
                        <span className="font-semibold text-[var(--accent-mid)] tabular-nums">
                            {formatCountdown(nextOpenMs - nowMs)}
                        </span>
                        {' '}·{' '}
                        {new Date(nextOpenMs).toLocaleDateString(undefined, {
                            month: 'short', day: 'numeric', timeZone: 'UTC',
                        })} UTC
                    </p>
                )}
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
                            <th className="py-2 pr-8">Battles</th>
                            <th className="py-2 pr-8">Avg dmg</th>
                            <th className="py-2">Kills/battle</th>
                        </tr>
                    </thead>
                    <tbody>
                        {players.slice(0, MAX_VISIBLE_PLAYERS).map((p) => (
                            <tr key={p.rank}>
                                <td className="py-1.5 pr-3 tabular-nums text-[var(--text-muted)]">{p.rank}</td>
                                <td className="py-1.5 pr-8">
                                    <span className="inline-flex items-center gap-1.5">
                                        <Link
                                            href={buildPlayerPath(p.player_name, realm)}
                                            className="text-[var(--accent-mid)] hover:underline"
                                            onClick={() => trackEvent('ship-player', { ship_id: ship.ship_id, ship_name: ship.name, rank: p.rank, realm })}
                                        >
                                            {p.player_name}
                                        </Link>
                                        {p.rank <= 3 && (
                                            <TopShipIcon rank={p.rank} shipName={ship.name} realm={data.realm} size="header" />
                                        )}
                                    </span>
                                </td>
                                <td className="py-1.5 pr-8 tabular-nums font-semibold" style={{ color: wrColor(p.win_rate) }}>
                                    {p.win_rate.toFixed(1)}%
                                </td>
                                <td className="py-1.5 pr-8 tabular-nums text-[var(--text-muted)]">
                                    {p.battles.toLocaleString()}
                                </td>
                                <td className="py-1.5 pr-8 tabular-nums text-[var(--text-muted)]">
                                    {p.avg_damage.toLocaleString()}
                                </td>
                                <td className="py-1.5 tabular-nums text-[var(--text-muted)]">
                                    {p.kills_per_battle.toFixed(2)}
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
