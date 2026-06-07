"use client";

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { buildPlayerPath, parseShipIdFromRouteSegment } from '../lib/entityRoutes';
import { useRealm } from '../context/RealmContext';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCircleInfo } from '@fortawesome/free-solid-svg-icons';
import wrColor from '../lib/wrColor';
import { shipClass, nationLabel } from '../lib/shipIdentity';
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
    // Ship identity — all from the payload already in hand (no new fetch). Each
    // mark is omitted cleanly when its field is absent, so the masthead stays
    // intentional with 1, 2, or 3 attributes present.
    const cls = shipClass(ship.ship_type);
    const nation = nationLabel(ship.nation);
    const tierLabel = ship.tier ? `T${ship.tier}` : null;
    const chips = [tierLabel, cls?.label, nation].filter(Boolean) as string[];
    const visible = players.slice(0, MAX_VISIBLE_PLAYERS);

    const medal = (rank: number) =>
        rank <= 3 ? <TopShipIcon rank={rank} shipName={ship.name} realm={data.realm} size="podium" /> : null;
    const onPlayerClick = (rank: number) =>
        trackEvent('ship-player', { ship_id: ship.ship_id, ship_name: ship.name, rank, realm });

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
            <header className="mb-5">
                <div className="flex flex-wrap items-center gap-2.5">
                    {cls && (
                        // Decorative: the full class name is already conveyed by the
                        // text chip below, so the glyph stays aria-hidden to avoid a
                        // double screen-reader announcement. `title` is a sighted hover.
                        <span
                            title={cls.label}
                            aria-hidden="true"
                            className="inline-flex h-6 min-w-[1.5rem] items-center justify-center rounded border border-[var(--border)] bg-[var(--accent-faint)] px-1 text-[11px] font-bold tracking-tight text-[var(--accent-mid)]"
                        >
                            {cls.abbr}
                        </span>
                    )}
                    <h1 className="break-words text-3xl font-semibold tracking-tight text-[var(--accent-dark)] sm:text-4xl">
                        {ship.name}
                    </h1>
                    {ship.is_premium && (
                        <span
                            title="Premium ship"
                            className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold"
                            style={{ color: 'var(--metal-gold)', borderColor: 'var(--metal-gold)' }}
                        >
                            <span aria-hidden="true">★</span>Premium
                        </span>
                    )}
                </div>
                {chips.length > 0 && (
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                        {chips.map((c) => (
                            <span
                                key={c}
                                className="inline-flex items-center rounded-full border border-[var(--border)] bg-[var(--accent-faint)] px-2 py-0.5 text-xs font-medium text-[var(--text-muted)]"
                            >
                                {c}
                            </span>
                        ))}
                    </div>
                )}
                <p className="mt-3 flex flex-wrap items-center gap-1.5 text-xs uppercase tracking-wide text-[var(--text-muted)]">
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
                <>
                    {/* Desktop: dense ranked table. Numeric columns are right-aligned
                        for clean decimal scanning; win rate keeps the only color, with
                        battles/avg-damage lifted above the quietest column. */}
                    <table className="hidden w-full text-sm sm:table">
                        <thead>
                            <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wide text-[var(--text-muted)]">
                                <th className="py-2 pl-2 pr-3 font-medium">#</th>
                                <th className="py-2 pr-8 font-medium">Player</th>
                                <th className="py-2 pr-8 text-right font-medium">Win rate</th>
                                <th className="py-2 pr-8 text-right font-medium">Battles</th>
                                <th className="py-2 pr-8 text-right font-medium">Avg dmg</th>
                                <th className="py-2 text-right font-medium">Kills/battle</th>
                            </tr>
                        </thead>
                        <tbody>
                            {visible.map((p) => {
                                const isChampion = p.rank === 1;
                                // Subtle podium/field divider: only the rank-3 row carries
                                // a border (when there's a field below it to separate from).
                                // Avoids per-row border noise and the fragile `/40` opacity
                                // modifier on a CSS var.
                                const podiumEdge = p.rank === 3 && players.length > 3;
                                return (
                                    <tr
                                        key={p.rank}
                                        className={`transition-colors hover:bg-[var(--bg-hover)] ${podiumEdge ? 'border-b border-[var(--border)]' : ''} ${isChampion ? 'bg-[var(--champion-tint)]' : ''}`}
                                        style={isChampion ? { boxShadow: 'inset 3px 0 0 var(--champion-edge)' } : undefined}
                                    >
                                        <td className="py-2 pl-2 pr-3 align-top tabular-nums text-[var(--text-muted)]">{p.rank}</td>
                                        <td className="py-2 pr-8">
                                            <span className="inline-flex items-center gap-2">
                                                <Link
                                                    href={buildPlayerPath(p.player_name, realm)}
                                                    className={`text-[var(--accent-mid)] hover:underline ${isChampion ? 'font-semibold' : ''}`}
                                                    onClick={() => onPlayerClick(p.rank)}
                                                >
                                                    {p.player_name}
                                                </Link>
                                                {medal(p.rank)}
                                            </span>
                                            {isChampion && (
                                                <span className="mt-0.5 block text-[10px] font-semibold uppercase tracking-wide" style={{ color: 'var(--metal-gold)' }}>
                                                    Reigning champion
                                                </span>
                                            )}
                                        </td>
                                        <td className="py-2 pr-8 text-right align-top tabular-nums font-semibold" style={{ color: wrColor(p.win_rate) }}>
                                            {p.win_rate.toFixed(1)}%
                                        </td>
                                        <td className="py-2 pr-8 text-right align-top tabular-nums text-[var(--text-primary)]">
                                            {p.battles.toLocaleString()}
                                        </td>
                                        <td className="py-2 pr-8 text-right align-top tabular-nums text-[var(--text-primary)]">
                                            {p.avg_damage.toLocaleString()}
                                        </td>
                                        <td className="py-2 text-right align-top tabular-nums text-[var(--text-muted)]">
                                            {p.kills_per_battle.toFixed(2)}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>

                    {/* Mobile: stacked cards. Rank + player + win rate stay primary;
                        battles/avg-damage/kills drop to a secondary line so six numeric
                        columns never force a horizontal scroll on a phone. */}
                    <ul className="space-y-2 sm:hidden">
                        {visible.map((p) => {
                            const isChampion = p.rank === 1;
                            return (
                                <li
                                    key={p.rank}
                                    className={`rounded-md border border-[var(--border)] p-3 ${isChampion ? 'bg-[var(--champion-tint)]' : 'bg-[var(--bg-surface)]'}`}
                                    style={isChampion ? { boxShadow: 'inset 3px 0 0 var(--champion-edge)' } : undefined}
                                >
                                    <div className="flex items-center justify-between gap-2">
                                        <span className="inline-flex min-w-0 items-center gap-2">
                                            <span className="w-5 shrink-0 text-right tabular-nums text-[var(--text-muted)]">{p.rank}</span>
                                            <Link
                                                href={buildPlayerPath(p.player_name, realm)}
                                                className={`truncate text-[var(--accent-mid)] hover:underline ${isChampion ? 'font-semibold' : ''}`}
                                                onClick={() => onPlayerClick(p.rank)}
                                            >
                                                {p.player_name}
                                            </Link>
                                            {medal(p.rank)}
                                        </span>
                                        <span className="shrink-0 tabular-nums font-semibold" style={{ color: wrColor(p.win_rate) }}>
                                            {p.win_rate.toFixed(1)}%
                                        </span>
                                    </div>
                                    {isChampion && (
                                        <span className="mt-1 block text-[10px] font-semibold uppercase tracking-wide" style={{ color: 'var(--metal-gold)' }}>
                                            Reigning champion
                                        </span>
                                    )}
                                    <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs tabular-nums text-[var(--text-muted)]">
                                        <span><span className="text-[var(--text-primary)]">{p.battles.toLocaleString()}</span> battles</span>
                                        <span><span className="text-[var(--text-primary)]">{p.avg_damage.toLocaleString()}</span> avg dmg</span>
                                        <span>{p.kills_per_battle.toFixed(2)} kills/battle</span>
                                    </div>
                                </li>
                            );
                        })}
                    </ul>
                </>
            )}
        </section>
    );
};

export default ShipRouteView;
