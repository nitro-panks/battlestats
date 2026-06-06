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
//
// Presented as an "honor roll": a real --bg-surface panel with a medal-emblem
// header, then one row per ship — rank-colored medal + hero ship name + tier
// pill + a podium-count chip (gold-tinted when a #1 was ever held) + the muted
// season-week history. Shares the medal/tier/gold language with
// ShipTopPlayerBanner so the two award surfaces read as a set.
// See agents/runbooks/runbook-ship-banner-ux-pass-2026-06-05.md
// and agents/runbooks/runbook-ship-award-ledger-2026-06-05.md.

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
            className="mt-6 rounded-md border border-[var(--border)] bg-[var(--bg-surface)] p-5 shadow-sm"
            aria-label="Ship honors"
        >
            <div className="mb-3 flex items-center justify-between gap-2 border-b border-[var(--border)] pb-2.5">
                <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
                    <MedalIcon rank={1} className="text-lg" />
                    Ship Honors
                </h2>
                <span className="text-xs tabular-nums text-[var(--text-secondary)]">
                    {awards.length} {awards.length === 1 ? 'ship' : 'ships'}
                </span>
            </div>
            <ul className="flex flex-col">
                {visible.map((a) => {
                    // Season weeks (with year) for every placement, oldest → newest.
                    const weeks = (a.seasons ?? [])
                        .slice()
                        .sort((x, y) => x.captured_on.localeCompare(y.captured_on))
                        .map((s) => formatWeek(Date.parse(s.captured_on), true))
                        .join(', ');
                    const heldFirst = a.times_first > 0 || a.best_rank === 1;
                    return (
                        <li
                            key={a.ship_id}
                            className="group -mx-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 rounded px-2 py-2 transition-colors hover:bg-[var(--bg-hover)]"
                        >
                            <MedalIcon rank={a.current_rank ?? a.best_rank} className="shrink-0 text-lg" />
                            <Link
                                href={buildShipPath(a.ship_id, a.ship_name, realm)}
                                className="font-semibold text-[var(--text-primary)] hover:underline"
                            >
                                {a.ship_name}
                            </Link>
                            {a.tier ? (
                                <span className="rounded bg-[var(--accent-faint)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--accent-dark)]">
                                    T{a.tier}
                                </span>
                            ) : null}
                            <span
                                className={`rounded px-1.5 py-0.5 text-[11px] font-semibold tabular-nums ${
                                    heldFirst
                                        ? 'bg-amber-500/10 text-amber-500'
                                        : 'bg-[var(--accent-faint)] text-[var(--text-secondary)]'
                                }`}
                                title={`${a.times_top3} top-3 finish${a.times_top3 === 1 ? '' : 'es'}${a.times_first > 0 ? `, ${a.times_first} first` : ''}`}
                            >
                                ×{a.times_top3}
                            </span>
                            {weeks ? (
                                <span className="min-w-0 truncate text-xs text-[var(--text-secondary)]">{weeks}</span>
                            ) : null}
                        </li>
                    );
                })}
            </ul>
            {overflow > 0 ? (
                <p className="mt-2 text-xs text-[var(--text-secondary)]">+{overflow} more</p>
            ) : null}
        </section>
    );
};

export default ShipHonors;
