"use client";

import React from 'react';
import Link from 'next/link';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faMedal } from '@fortawesome/free-solid-svg-icons';
import { buildShipPath } from '../lib/entityRoutes';

// Profile banner for a player's weekly top-3 finishes in a Tier-10 ship. One
// card per badge, stacked, placed above the Battle History card. Each card is
// ~the sparkline's height and links to that ship's standings page. Fed by the
// player payload's `ship_badges` (data.get_player_ship_badges).
// See agents/runbooks/runbook-ship-top-player-badges-2026-06-05.md.

export interface ShipBadge {
    ship_id: number;
    ship_name: string;
    rank: number;
    win_rate: number;
    battles: number;
    avg_damage: number;
    window_days: number;
}

const RANK_COLOR: Record<number, string> = {
    1: 'text-amber-500',   // gold
    2: 'text-zinc-400',    // silver
    3: 'text-orange-700',  // bronze
};

interface ShipTopPlayerBannerProps {
    badges: ShipBadge[];
    realm?: string;
}

const ShipTopPlayerBanner: React.FC<ShipTopPlayerBannerProps> = ({ badges, realm }) => {
    if (!badges || badges.length === 0) return null;

    return (
        <div className="mt-6 flex flex-wrap gap-2" aria-label="Top ship rankings">
            {badges.map((b) => {
                const color = RANK_COLOR[b.rank] ?? 'text-amber-500';
                return (
                    <Link
                        key={`${b.ship_id}-${b.rank}`}
                        href={buildShipPath(b.ship_id, b.ship_name, realm)}
                        title={`#${b.rank} in ${b.ship_name}${realm ? ` on ${realm.toUpperCase()}` : ''} over the last ${b.window_days} days — ${b.win_rate.toFixed(1)}% win rate`}
                        className="flex min-h-16 items-center gap-4 rounded-md border border-[var(--accent-faint)] bg-[var(--bg-card)] px-4 py-2 transition-colors hover:border-[var(--accent-mid)]"
                    >
                        <div className="flex shrink-0 flex-col items-center gap-0.5">
                            <FontAwesomeIcon icon={faMedal} className={`${color} text-2xl`} aria-hidden="true" />
                            {realm && (
                                <span className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                                    {realm.toUpperCase()}
                                </span>
                            )}
                        </div>
                        <div className="min-w-0">
                            <div className="truncate text-sm">
                                <span className={`font-semibold ${color}`}>#{b.rank}</span>{' '}
                                <span className="font-semibold text-[var(--text-strong)]">{b.ship_name}</span>{' '}
                                <span className="text-[var(--text-muted)]">last {b.window_days} days</span>
                            </div>
                            <div className="mt-0.5 truncate text-xs tabular-nums text-[var(--text-muted)]">
                                {b.avg_damage.toLocaleString()} avg dmg
                            </div>
                        </div>
                    </Link>
                );
            })}
        </div>
    );
};

export default ShipTopPlayerBanner;
