"use client";

import React from 'react';
import Link from 'next/link';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faMedal } from '@fortawesome/free-solid-svg-icons';
import { buildShipPath } from '../lib/entityRoutes';

// Durable per-ship career record, accreted from the append-only ShipAward
// ledger (data.get_player_ship_awards). Unlike the live ShipTopPlayerBanner
// (current standing only), this persists through inactivity — a player who
// stops playing keeps "7× #1 · last held Apr 12" instead of a vanished badge.
// See agents/runbooks/runbook-ship-award-ledger-2026-06-05.md.

export interface ShipAward {
    ship_id: number;
    ship_name: string;
    times_first: number;
    times_top3: number;
    best_rank: number;
    current_rank: number | null;
    first_on: string | null;
    last_on: string | null;
}

const RANK_COLOR: Record<number, string> = {
    1: 'text-amber-500',   // gold
    2: 'text-zinc-400',    // silver
    3: 'text-orange-700',  // bronze
};
const MAX_VISIBLE = 12;

const formatDate = (iso: string | null): string => {
    if (!iso) return '';
    const d = new Date(`${iso}T00:00:00`);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
};

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
                    const color = RANK_COLOR[a.current_rank ?? a.best_rank] ?? 'text-amber-500';
                    const headline = a.times_first > 0 ? `${a.times_first}× #1` : `best #${a.best_rank}`;
                    const status = a.current_rank != null
                        ? `currently #${a.current_rank}`
                        : `last held ${formatDate(a.last_on)}`;
                    return (
                        <li key={a.ship_id} className="flex flex-wrap items-baseline gap-x-2 text-sm">
                            <FontAwesomeIcon icon={faMedal} className={`${color} shrink-0`} aria-hidden="true" />
                            <Link
                                href={buildShipPath(a.ship_id, a.ship_name, realm)}
                                className="font-semibold text-[var(--text-strong)] hover:underline"
                            >
                                {a.ship_name}
                            </Link>
                            <span className="text-[var(--text-muted)]">
                                {headline} · {a.times_top3} window{a.times_top3 === 1 ? '' : 's'} top-3 · {status}
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
