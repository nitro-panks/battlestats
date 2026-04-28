'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import wrColor from '../lib/wrColor';

export interface BattleHistoryByShip {
    ship_id: number;
    ship_name: string;
    ship_tier: number | null;
    ship_type: string | null;
    battles: number;
    wins: number;
    losses: number;
    win_rate: number;
    damage: number;
    avg_damage: number;
    frags: number;
    xp: number;
    planes_killed: number;
    survived_battles: number;
}

export interface BattleHistoryByDay {
    date: string;
    battles: number;
    wins: number;
    damage: number;
    frags: number;
}

export interface BattleHistoryTotals {
    battles: number;
    wins: number;
    losses: number;
    win_rate: number;
    damage: number;
    avg_damage: number;
    frags: number;
    xp: number;
    planes_killed: number;
    survived_battles: number;
    survival_rate: number;
}

export interface BattleHistoryPayload {
    window_days: number;
    as_of: string;
    totals: BattleHistoryTotals;
    by_ship: BattleHistoryByShip[];
    by_day: BattleHistoryByDay[];
}

interface BattleHistoryCardProps {
    playerName: string;
    realm: string;
    days?: number;
}

const formatInt = (n: number): string => n.toLocaleString();
const formatPercent = (n: number): string => `${n.toFixed(1)}%`;

const Sparkline: React.FC<{ days: BattleHistoryByDay[] }> = ({ days }) => {
    if (days.length === 0) return null;
    const width = 240;
    const height = 36;
    const pad = 2;
    const maxBattles = Math.max(1, ...days.map((d) => d.battles));
    const points = days.map((d, idx) => {
        const x = pad + (idx * (width - 2 * pad)) / Math.max(1, days.length - 1);
        const y = height - pad - ((d.battles / maxBattles) * (height - 2 * pad));
        return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    return (
        <svg
            viewBox={`0 0 ${width} ${height}`}
            width={width}
            height={height}
            aria-label="Battles per day sparkline"
            role="img"
        >
            <polyline
                fill="none"
                stroke="var(--accent-mid)"
                strokeWidth="1.5"
                points={points.join(' ')}
            />
            {days.map((d, idx) => {
                const x = pad + (idx * (width - 2 * pad)) / Math.max(1, days.length - 1);
                const y = height - pad - ((d.battles / maxBattles) * (height - 2 * pad));
                const winRate = d.battles ? (100 * d.wins) / d.battles : 0;
                return (
                    <circle
                        key={d.date}
                        cx={x}
                        cy={y}
                        r={2.5}
                        fill={wrColor(d.battles ? winRate : null)}
                    >
                        <title>
                            {d.date}: {d.battles} battles, {formatPercent(winRate)} WR, {formatInt(d.damage)} damage
                        </title>
                    </circle>
                );
            })}
        </svg>
    );
};

const BattleHistoryCard: React.FC<BattleHistoryCardProps> = ({
    playerName,
    realm,
    days = 7,
}) => {
    const [payload, setPayload] = useState<BattleHistoryPayload | null>(null);
    const [error, setError] = useState<Error | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        const url = `/api/player/${encodeURIComponent(playerName)}/battle-history/?days=${days}&realm=${encodeURIComponent(realm)}`;
        fetchSharedJson<BattleHistoryPayload>(url, {
            label: 'BattleHistoryCard',
            ttlMs: 60_000,
        })
            .then(({ data }) => {
                if (!cancelled) {
                    setPayload(data);
                    setError(null);
                }
            })
            .catch((e: unknown) => {
                if (!cancelled) {
                    setError(e instanceof Error ? e : new Error(String(e)));
                    setPayload(null);
                }
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, [playerName, realm, days]);

    const visibleByShip = useMemo(
        () => (payload?.by_ship ?? []).slice(0, 12),
        [payload?.by_ship],
    );

    if (loading || error) {
        return null;
    }
    const totals = payload?.totals;
    if (!payload || !totals || typeof totals.battles !== 'number' || totals.battles <= 0) {
        return null;
    }

    return (
        <section
            data-testid="battle-history-card"
            className="mt-6 rounded-md border border-[var(--accent-faint)] bg-[var(--bg-card)] p-4"
            aria-label="Recent battles"
        >
            <header className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                    Last {payload.window_days} days
                </h2>
                <span className="text-xs text-[var(--text-muted)]">
                    {formatInt(totals.battles)} battles · {formatPercent(totals.win_rate)} WR · {formatInt(totals.avg_damage)} avg dmg
                </span>
            </header>
            <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <div>
                    <div className="text-xs text-[var(--text-muted)]">Battles</div>
                    <div className="text-lg font-semibold text-[var(--text-strong)]">{formatInt(totals.battles)}</div>
                </div>
                <div>
                    <div className="text-xs text-[var(--text-muted)]">Win rate</div>
                    <div
                        className="text-lg font-semibold"
                        style={{ color: wrColor(totals.win_rate) }}
                    >
                        {formatPercent(totals.win_rate)}
                    </div>
                </div>
                <div>
                    <div className="text-xs text-[var(--text-muted)]">Avg damage</div>
                    <div className="text-lg font-semibold text-[var(--text-strong)]">{formatInt(totals.avg_damage)}</div>
                </div>
                <div>
                    <div className="text-xs text-[var(--text-muted)]">Frags</div>
                    <div className="text-lg font-semibold text-[var(--text-strong)]">{formatInt(totals.frags)}</div>
                </div>
            </div>
            <div className="mt-4">
                <Sparkline days={payload.by_day} />
            </div>
            <div className="mt-4 overflow-x-auto">
                <table className="w-full text-left text-sm">
                    <thead>
                        <tr className="border-b border-[var(--accent-faint)] text-xs uppercase tracking-wide text-[var(--text-muted)]">
                            <th scope="col" className="py-1 pr-2">Ship</th>
                            <th scope="col" className="py-1 pr-2 text-right">Battles</th>
                            <th scope="col" className="py-1 pr-2 text-right">WR</th>
                            <th scope="col" className="py-1 pr-2 text-right">Avg dmg</th>
                            <th scope="col" className="py-1 pr-2 text-right">Frags</th>
                            <th scope="col" className="py-1 pr-2 text-right">Survived</th>
                        </tr>
                    </thead>
                    <tbody>
                        {visibleByShip.map((row) => (
                            <tr
                                key={row.ship_id}
                                className="border-b border-[var(--accent-faint)] last:border-b-0"
                            >
                                <td className="py-1 pr-2">
                                    <span className="text-[var(--text-strong)]">{row.ship_name || `Ship ${row.ship_id}`}</span>
                                    {row.ship_tier ? (
                                        <span className="ml-1 text-xs text-[var(--text-muted)]">T{row.ship_tier}</span>
                                    ) : null}
                                </td>
                                <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-strong)]">{formatInt(row.battles)}</td>
                                <td
                                    className="py-1 pr-2 text-right tabular-nums font-semibold"
                                    style={{ color: wrColor(row.win_rate) }}
                                >
                                    {formatPercent(row.win_rate)}
                                </td>
                                <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-strong)]">{formatInt(row.avg_damage)}</td>
                                <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-strong)]">{formatInt(row.frags)}</td>
                                <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-strong)]">
                                    {formatInt(row.survived_battles)}/{formatInt(row.battles)}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </section>
    );
};

export default BattleHistoryCard;
