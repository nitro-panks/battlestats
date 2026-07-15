"use client";

import React from 'react';
import Link from 'next/link';
import MedalIcon, { RANK_COLOR } from './MedalIcon';
import { buildShipPath } from '../lib/entityRoutes';
import { trackEvent } from '../lib/umami';

// Profile banner for a player's current top-3 finishes in a Tier-10 ship
// (rolling trailing window, recomputed nightly; the badge tracks the current
// board generation and drops the moment the player is displaced).
// One award card per badge, stacked above the Battle History card, each linking
// to that ship's standings page. Fed by the player payload's `ship_badges`
// (data.get_player_ship_badges).
//
// Visual intent (see agents/runbooks/runbook-ship-banner-ux-pass-2026-06-05.md):
// these read as *earned awards*, not list rows. The "won it" cue is a
// rank-colored (gold/silver/bronze) medal-ribbon left edge + the two-tone
// MedalIcon anchor; the rest is a disciplined three-tier type ladder
// (hero ship name → muted realm·week → win-rate·damage) on a real --bg-surface
// card. Restrained on purpose: no gradients/glow, modest medal, subtle shadow.
// See also agents/runbooks/runbook-ship-top-player-badges-2026-06-05.md.

export interface ShipBadge {
    ship_id: number;
    ship_name: string;
    tier?: number | null; // ship tier (standings span T8–T10); shown so tiers aren't conflated
    rank: number;
    win_rate: number;
    battles: number;
    avg_damage: number;
    window_days: number;
    window_start?: string | null; // run date of the snapshot (the trailing window's end)
}

interface ShipTopPlayerBannerProps {
    badges: ShipBadge[];
    realm?: string;
    // Award holder identity for the tile's top banner ("[TAG] Name").
    playerName?: string;
    clanTag?: string | null;
}

// Per-rank award accents: the medal-ribbon left edge (one step softer than the
// MedalIcon disc), the matching top-banner fill, and the colorblind-safe
// ordinal. Default covers rank > 3, which the top-3 feed never emits, but
// keeps the lookup total.
const RANK_META: Record<number, { borderL: string; banner: string; ordinal: string }> = {
    1: { borderL: 'border-l-amber-400', banner: 'bg-amber-400 text-amber-950', ordinal: '1st' },
    2: { borderL: 'border-l-zinc-400', banner: 'bg-zinc-400 text-zinc-950', ordinal: '2nd' },
    3: { borderL: 'border-l-orange-600', banner: 'bg-orange-600 text-black', ordinal: '3rd' },
};

const ShipTopPlayerBanner: React.FC<ShipTopPlayerBannerProps> = ({ badges, realm, playerName, clanTag }) => {
    if (!badges || badges.length === 0) return null;

    const holderLabel = playerName ? `${clanTag ? `[${clanTag}] ` : ''}${playerName}` : null;

    return (
        <div className="mt-6 grid grid-cols-1 gap-2.5 sm:grid-cols-2 md:grid-cols-3" aria-label="Top ship rankings">
            {badges.map((b) => {
                const rankColor = RANK_COLOR[b.rank] ?? 'text-amber-500';
                const meta = RANK_META[b.rank] ?? { borderL: 'border-l-amber-400', banner: 'bg-amber-400 text-amber-950', ordinal: `#${b.rank}` };
                const windowLabel = `${b.window_days} day window`;
                return (
                    <Link
                        key={`${b.ship_id}-${b.rank}`}
                        href={buildShipPath(b.ship_id, b.ship_name, realm)}
                        onClick={() => trackEvent('ship-banner-click', { ship_id: b.ship_id, ship_name: b.ship_name, rank: b.rank, realm: realm ?? '' })}
                        title={`#${b.rank} in ${b.ship_name}${realm ? ` on ${realm.toUpperCase()}` : ''} — ${b.win_rate.toFixed(1)}% win rate over the ${windowLabel}`}
                        className={`group flex flex-col overflow-hidden rounded-md border border-[var(--border)] border-l-4 ${meta.borderL} bg-[var(--bg-surface)] shadow-sm transition-all hover:bg-[var(--bg-hover)] hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-mid)]`}
                    >
                        {/* Holder banner: the ribbon color carried across the top,
                            naming the award holder. */}
                        {holderLabel ? (
                            <div className={`${meta.banner} w-[calc(100%-10px)] truncate rounded-br-md px-3 py-[1px] text-[11px] font-semibold tracking-wide`}>
                                {holderLabel}
                            </div>
                        ) : null}
                        {/* pr matches the banner's 10px right inset so the
                            right-aligned games/dmg figures line up with the
                            banner's right edge. */}
                        <div className="flex items-center gap-3 py-2.5 pl-3 pr-[10px]">
                        {/* Medal + ordinal + realm anchor — fixed width so every card aligns */}
                        <div className="flex w-12 shrink-0 flex-col items-center gap-1">
                            <MedalIcon rank={b.rank} className="text-[1.75rem]" />
                            <span className={`text-[10px] font-bold uppercase tracking-wider ${rankColor}`}>
                                {meta.ordinal}
                            </span>
                            {realm ? (
                                <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--text-secondary)]">
                                    {realm.toUpperCase()}
                                </span>
                            ) : null}
                        </div>
                        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                            {/* Hero: ship name (+ tier pill) */}
                            <div className="flex min-w-0 items-center gap-2">
                                <span className="truncate text-base font-bold tracking-tight text-[var(--text-primary)]">
                                    {b.ship_name}
                                </span>
                                {b.tier ? (
                                    <span className="shrink-0 rounded bg-[var(--accent-faint)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--accent-dark)]">
                                        T{b.tier}
                                    </span>
                                ) : null}
                            </div>
                            {/* Meta: window on the left, games right-aligned to the
                                stat line's damage figure below. */}
                            <div className="flex items-baseline justify-between gap-1.5 font-['Courier_New',Courier,monospace] text-[11px] font-medium text-[var(--text-secondary)]">
                                <span>{windowLabel}</span>
                                <span><span className="font-semibold text-[var(--text-primary)]">{b.battles.toLocaleString()}</span> games</span>
                            </div>
                            {/* Stat: win rate left, avg damage right — the games and
                                dmg segments share one right edge. items-baseline (not
                                center): mixed emphasis line boxes center 0.5px apart. */}
                            <div className="mt-0.5 flex items-baseline justify-between gap-1.5 font-['Courier_New',Courier,monospace] text-xs tabular-nums text-[var(--text-secondary)]">
                                <span><span className="font-semibold text-[var(--text-primary)]">{b.win_rate.toFixed(1)}%</span> WR</span>
                                <span><span className="font-semibold text-[var(--text-primary)]">{b.avg_damage.toLocaleString()}</span> dmg</span>
                            </div>
                        </div>
                        </div>
                    </Link>
                );
            })}
        </div>
    );
};

export default ShipTopPlayerBanner;
