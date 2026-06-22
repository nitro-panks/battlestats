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
import ShipToolLink from './ShipToolLink';
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
    window_start?: string | null;
    ship: {
        ship_id: number;
        name: string;
        tier: number | null;
        ship_type: string | null;
        nation: string;
        is_premium: boolean;
        shiptool_code?: string | null;
    };
    players: ShipLeaderboardPlayer[];
}


const SkeletonBar: React.FC<{ className?: string }> = ({ className = '' }) => (
    <div className={`animate-pulse rounded bg-[var(--bg-hover)] ${className}`} />
);

// Loading skeleton that mirrors the real masthead + table shape (glyph, name,
// chips, a few rows) so the page's structure is visible while data arrives,
// rather than a single grey box that reads as "broken".
const ShipSkeleton: React.FC = () => (
    <section className="mx-auto max-w-3xl" aria-busy="true" aria-label="Loading ship standings">
        <div className="mb-5">
            <div className="flex items-center gap-2.5">
                <SkeletonBar className="h-6 w-9" />
                <SkeletonBar className="h-9 w-52" />
            </div>
            <div className="mt-2 flex gap-1.5">
                <SkeletonBar className="h-5 w-12" />
                <SkeletonBar className="h-5 w-24" />
                <SkeletonBar className="h-5 w-16" />
            </div>
            <SkeletonBar className="mt-3 h-3 w-64" />
        </div>
        <div className="space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
                <SkeletonBar key={i} className="h-9 w-full" />
            ))}
        </div>
    </section>
);

// Derive a display label from the route slug (strip the leading "<id>-"), the
// same shape the page's generateMetadata() uses — so the error state can still
// name the ship the user was looking for.
const slugToLabel = (slug: string): string => {
    const decoded = decodeURIComponent(slug).replace(/^\d+-?/, '').replace(/-/g, ' ').trim();
    return decoded ? decoded.replace(/\b\w/g, (c) => c.toUpperCase()) : '';
};

// Shared player-link styling — adds a visible keyboard focus ring on top of the
// existing hover underline (used in both the desktop table and mobile cards).
const PLAYER_LINK_CLASS =
    'rounded-sm text-[var(--accent-mid)] hover:underline focus-visible:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-mid)] focus-visible:ring-offset-1';


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
        return <ShipSkeleton />;
    }

    if (error || !data) {
        const label = slugToLabel(shipSlug);
        return (
            <section className="mx-auto max-w-3xl">
                {label && (
                    <h1 className="mb-3 break-words text-3xl font-semibold tracking-tight text-[var(--accent-dark)]">
                        {label}
                    </h1>
                )}
                <div className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] p-6 text-sm text-[var(--text-muted)]">
                    {error || 'Ship standings not found.'} The {realm.toUpperCase()} board may not have ranked this ship yet — check back as battles come in.
                </div>
            </section>
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

    // Provenance — when this board was captured. Hidden when the payload omits
    // it (no "as of —"). Date-only ISO parses as UTC midnight; render in UTC.
    const capturedMs = data.captured_on ? Date.parse(data.captured_on) : null;
    const capturedLabel = capturedMs !== null && !Number.isNaN(capturedMs)
        ? new Date(capturedMs).toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' })
        : null;

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
                    <ShipToolLink
                        code={ship.shiptool_code}
                        shipName={ship.name}
                        realm={realm}
                        shipId={ship.ship_id}
                        size="md"
                    />
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
                    {realm.toUpperCase()} · best players · trailing {data.window_days} days · updated daily ·
                    <button
                        type="button"
                        title={RANKING_TOOLTIP}
                        aria-label={RANKING_TOOLTIP}
                        className="inline-flex cursor-help rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-mid)]"
                    >
                        <FontAwesomeIcon icon={faCircleInfo} aria-hidden="true" />
                    </button>
                </p>
                {capturedLabel && (
                    <p className="mt-1 text-xs text-[var(--text-muted)]">Standings captured {capturedLabel} UTC · recomputed nightly</p>
                )}
            </header>

            {players.length === 0 ? (
                <div className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] p-6 text-sm text-[var(--text-muted)]">
                    No ranked standings for this ship yet — check back as battles come in.
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
                                                    className={`${PLAYER_LINK_CLASS} ${isChampion ? 'font-semibold' : ''}`}
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
                                                className={`${PLAYER_LINK_CLASS} truncate ${isChampion ? 'font-semibold' : ''}`}
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
