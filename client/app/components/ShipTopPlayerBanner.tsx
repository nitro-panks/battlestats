"use client";

import React from 'react';
import Link from 'next/link';
import MedalIcon, { RANK_COLOR } from './MedalIcon';
import { buildShipPath } from '../lib/entityRoutes';
import { formatWeek } from '../lib/shipSeason';

// Profile banner for a player's per-fortnight top-3 finishes in a Tier-10 ship.
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
    window_start?: string | null; // season-start date (for the WK<n> label)
}

interface ShipTopPlayerBannerProps {
    badges: ShipBadge[];
    realm?: string;
}

// Per-rank award accents: the medal-ribbon left edge (one step softer than the
// MedalIcon disc) and the colorblind-safe ordinal. Default covers rank > 3,
// which the top-3 feed never emits, but keeps the lookup total.
const RANK_META: Record<number, { borderL: string; ordinal: string }> = {
    1: { borderL: 'border-l-amber-400', ordinal: '1st' },
    2: { borderL: 'border-l-zinc-400', ordinal: '2nd' },
    3: { borderL: 'border-l-orange-600', ordinal: '3rd' },
};

const ShipTopPlayerBanner: React.FC<ShipTopPlayerBannerProps> = ({ badges, realm }) => {
    if (!badges || badges.length === 0) return null;

    return (
        <div className="mt-6 flex flex-wrap gap-2.5" aria-label="Top ship rankings">
            {badges.map((b) => {
                const rankColor = RANK_COLOR[b.rank] ?? 'text-amber-500';
                const meta = RANK_META[b.rank] ?? { borderL: 'border-l-amber-400', ordinal: `#${b.rank}` };
                const weekLabel = b.window_start ? formatWeek(Date.parse(b.window_start)) : 'this season';
                return (
                    <Link
                        key={`${b.ship_id}-${b.rank}`}
                        href={buildShipPath(b.ship_id, b.ship_name, realm)}
                        title={`#${b.rank} in ${b.ship_name}${realm ? ` on ${realm.toUpperCase()}` : ''} ${weekLabel} — ${b.win_rate.toFixed(1)}% win rate`}
                        className={`group flex w-full items-center gap-3.5 rounded-md border border-[var(--border)] border-l-4 ${meta.borderL} bg-[var(--bg-surface)] px-4 py-2.5 shadow-sm transition-all hover:bg-[var(--bg-hover)] hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-mid)] sm:w-[18rem]`}
                    >
                        {/* Medal + ordinal anchor — fixed width so every card aligns */}
                        <div className="flex w-12 shrink-0 flex-col items-center gap-1">
                            <MedalIcon rank={b.rank} className="text-[1.75rem]" />
                            <span className={`text-[10px] font-bold uppercase tracking-wider ${rankColor}`}>
                                {meta.ordinal}
                            </span>
                        </div>
                        <div className="flex min-w-0 flex-col gap-0.5">
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
                            {/* Meta: realm · season week */}
                            <div className="flex items-center gap-1.5 text-[11px] font-medium text-[var(--text-secondary)]">
                                {realm ? (
                                    <>
                                        <span className="uppercase tracking-wide">{realm.toUpperCase()}</span>
                                        <span aria-hidden className="text-[var(--border)]">·</span>
                                    </>
                                ) : null}
                                <span>{weekLabel}</span>
                            </div>
                            {/* Stat: win rate (emphasized) · avg damage */}
                            <div className="mt-0.5 flex items-center gap-1.5 text-xs tabular-nums text-[var(--text-secondary)]">
                                <span className="font-semibold text-[var(--text-primary)]">{b.win_rate.toFixed(1)}%</span>
                                <span>WR</span>
                                <span aria-hidden className="text-[var(--border)]">·</span>
                                <span>{b.avg_damage.toLocaleString()} dmg</span>
                            </div>
                        </div>
                    </Link>
                );
            })}
        </div>
    );
};

export default ShipTopPlayerBanner;
