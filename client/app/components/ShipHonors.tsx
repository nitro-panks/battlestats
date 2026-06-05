"use client";

import React from 'react';
import Link from 'next/link';
import MedalIcon from './MedalIcon';
import { buildShipPath } from '../lib/entityRoutes';
import { formatWeek } from '../lib/shipSeason';

// Durable per-ship career record, accreted from the append-only ShipAward
// ledger (data.get_player_ship_awards). Unlike the live ShipTopPlayerBanner
// (current standing only), this persists through inactivity — a player who
// stops playing keeps "Shimakaze ×2: WK20'26, WK22'26" instead of a vanished
// badge. The year disambiguates the same week number across years.
// See agents/runbooks/runbook-ship-award-ledger-2026-06-05.md.

export interface ShipAward {
    ship_id: number;
    ship_name: string;
    tier?: number | null; // ship tier (standings span T8–T10)
    times_first: number;
    times_top3: number;
    best_rank: number;
    current_rank: number | null;
    first_on: string | null;
    last_on: string | null;
    // Each placement as {season-start date, rank}, newest first.
    seasons?: { captured_on: string; rank: number }[];
}

const MAX_VISIBLE = 12;

interface ShipHonorsProps {
    awards: ShipAward[];
    realm?: string;
}

const ShipHonors: React.FC<ShipHonorsProps> = ({ awards, realm }) => {
    if (!awards || awards.length === 0) return null;
    const visible = awards.slice(0, MAX_VISIBLE);
    const overflow = awards.length - visible.length;

    return (
        <section
            className="mt-6 rounded-md border border-[var(--accent-faint)] bg-[var(--bg-card)] p-5"
            aria-label="Ship honors"
        >
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                Ship Honors
            </h2>
            <ul className="flex flex-col gap-1.5">
                {visible.map((a) => {
                    // Season weeks (with year) for every placement, oldest → newest.
                    const weeks = (a.seasons ?? [])
                        .slice()
                        .sort((x, y) => x.captured_on.localeCompare(y.captured_on))
                        .map((s) => formatWeek(Date.parse(s.captured_on), true))
                        .join(', ');
                    return (
                        <li key={a.ship_id} className="flex flex-wrap items-baseline gap-x-2 text-sm">
                            <MedalIcon rank={a.current_rank ?? a.best_rank} className="shrink-0" />
                            {a.tier ? (
                                <span className="rounded bg-[var(--accent-faint)] px-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                                    T{a.tier}
                                </span>
                            ) : null}
                            <Link
                                href={buildShipPath(a.ship_id, a.ship_name, realm)}
                                className="font-semibold text-[var(--text-strong)] hover:underline"
                            >
                                {a.ship_name}
                            </Link>
                            <span className="text-[var(--text-muted)]">
                                ×{a.times_top3}{weeks ? `: ${weeks}` : ''}
                            </span>
                        </li>
                    );
                })}
            </ul>
            {overflow > 0 ? (
                <p className="mt-2 text-xs text-[var(--text-muted)]">+{overflow} more</p>
            ) : null}
        </section>
    );
};

export default ShipHonors;
