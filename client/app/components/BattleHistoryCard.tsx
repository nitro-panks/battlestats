'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import wrColor from '../lib/wrColor';
import { chartColors } from '../lib/chartTheme';
import { useTheme } from '../context/ThemeContext';

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
    lifetime_battles?: number | null;
    lifetime_win_rate?: number | null;
    delta_win_rate?: number | null;
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
    lifetime_battles?: number | null;
    lifetime_win_rate?: number | null;
    delta_win_rate?: number | null;
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

const tierBlue = (tier: number | null | undefined): string => {
    if (tier == null) return 'var(--text-muted)';
    const clamped = Math.max(1, Math.min(11, tier));
    // Saturation ramps 25% (T1, pale) → 95% (T11, deep). Lightness held at
    // 50% so the color reads on both light and dark themes.
    const sat = 25 + ((clamped - 1) / 10) * 70;
    return `hsl(215, ${sat}%, 50%)`;
};

type SortKey = 'ship_name' | 'ship_tier' | 'ship_type' | 'battles' | 'win_rate'
    | 'avg_damage' | 'kdr';

const computeKdr = (frags: number, battles: number, survived: number): number => {
    const deaths = Math.max(0, battles - survived);
    if (deaths <= 0) return frags;
    return frags / deaths;
};

// Format KDR for the per-ship table: trim trailing zeros so 1.50 → "1.5"
// and 1.00 → "1". The totals tile keeps full toFixed(2) for column alignment.
const formatTableKdr = (v: number): string => v.toFixed(2).replace(/\.?0+$/, '');

const SHIP_TYPE_LABEL: Record<string, string> = {
    Destroyer: 'DD',
    Cruiser: 'CA',
    Battleship: 'BB',
    AirCarrier: 'CV',
    Submarine: 'SS',
};

const shipTypeShort = (type: string | null | undefined): string => {
    if (!type) return '—';
    return SHIP_TYPE_LABEL[type] ?? type.slice(0, 2).toUpperCase();
};
type SortDirection = 'asc' | 'desc';

const DEFAULT_DIRECTION: Record<SortKey, SortDirection> = {
    ship_name: 'asc', ship_tier: 'asc', ship_type: 'asc',
    battles: 'desc', win_rate: 'desc', avg_damage: 'desc',
    kdr: 'desc',
};

interface SortableThProps {
    sortKey: SortKey;
    activeKey: SortKey;
    direction: SortDirection;
    onSortClick: (key: SortKey) => void;
    children: React.ReactNode;
}

const SortableTh: React.FC<SortableThProps> = ({
    sortKey, activeKey, direction, onSortClick, children,
}) => {
    const active = activeKey === sortKey;
    const arrow = active ? (direction === 'asc' ? '▲' : '▼') : '';
    return (
        <th
            scope="col"
            className="py-1 px-2 cursor-pointer select-none hover:text-[var(--text-strong)] text-center"
            onClick={() => onSortClick(sortKey)}
            aria-sort={active ? (direction === 'asc' ? 'ascending' : 'descending') : 'none'}
        >
            <span>{children}</span>
            <span className="ml-1 text-[10px]" aria-hidden="true">{arrow || '↕'}</span>
        </th>
    );
};

interface WrCellProps {
    periodWinRate: number;
    lifetimeWinRate: number | null | undefined;
    deltaWinRate: number | null | undefined;
    stacked?: boolean;
}

const WrCell: React.FC<WrCellProps> = ({
    periodWinRate, lifetimeWinRate, deltaWinRate, stacked = false,
}) => {
    const tone = deltaWinRate == null
        ? 'var(--text-muted)'
        : deltaWinRate > 0
            ? '#74c476'
            : deltaWinRate < 0
                ? '#a50f15'
                : 'var(--text-muted)';
    const signedDelta = deltaWinRate == null
        ? null
        : `${deltaWinRate > 0 ? '+' : ''}${deltaWinRate.toFixed(1)}%`;
    const tooltip = lifetimeWinRate != null
        ? `Period ${formatPercent(periodWinRate)} · Lifetime ${formatPercent(lifetimeWinRate)}${signedDelta != null ? ` (Δ${signedDelta})` : ''}`
        : `Period ${formatPercent(periodWinRate)}`;
    const periodEl = (
        <span style={{ color: wrColor(periodWinRate) }} className="font-semibold">
            {formatPercent(periodWinRate)}
        </span>
    );
    const lifetimeEl = lifetimeWinRate != null ? (
        <span className="text-xs" style={{ color: wrColor(lifetimeWinRate) }}>
            {formatPercent(lifetimeWinRate)}
        </span>
    ) : null;
    const deltaEl = signedDelta != null ? (
        <span className="text-xs font-medium" style={{ color: tone }}>
            Δ{signedDelta}
        </span>
    ) : null;

    if (stacked) {
        return (
            <span className="tabular-nums flex flex-col items-start" title={tooltip}>
                {periodEl}
                {(lifetimeEl || deltaEl) ? (
                    <span className="inline-flex items-baseline gap-2 whitespace-nowrap">
                        {lifetimeEl}
                        {deltaEl}
                    </span>
                ) : null}
            </span>
        );
    }
    return (
        <span
            className="tabular-nums inline-flex items-baseline gap-2 justify-end whitespace-nowrap"
            title={tooltip}
        >
            {periodEl}
            {lifetimeEl}
            {deltaEl}
        </span>
    );
};

const buildWindowedDays = (
    days: BattleHistoryByDay[],
    windowDays: number,
): BattleHistoryByDay[] => {
    const byDate = new Map(days.map((d) => [d.date, d]));
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const padded: BattleHistoryByDay[] = [];
    for (let i = windowDays - 1; i >= 0; i -= 1) {
        const d = new Date(today);
        d.setDate(today.getDate() - i);
        const iso = d.toISOString().slice(0, 10);
        const existing = byDate.get(iso);
        padded.push(existing ?? {
            date: iso,
            battles: 0, wins: 0, damage: 0, frags: 0,
        });
    }
    return padded;
};

interface SparklinePoint {
    date: string;
    value: number;
    color: string;
    tooltip: string;
}

const Sparkline: React.FC<{ points: SparklinePoint[]; ariaLabel: string }> = ({
    points, ariaLabel,
}) => {
    if (points.length === 0) return null;
    const width = 240;
    const height = 36;
    const pad = 2;
    const values = points.map((p) => p.value);
    const minV = Math.min(...values);
    const maxV = Math.max(...values);
    // Auto-scale: pad the range so a flat line still has visual room.
    const range = Math.max(maxV - minV, 0.0001);
    const padding = range * 0.15 + 0.0001;
    const yMin = minV - padding;
    const yMax = maxV + padding;
    const span = yMax - yMin;
    const xy = (idx: number, value: number): [number, number] => {
        const x = pad + (idx * (width - 2 * pad)) / Math.max(1, points.length - 1);
        const y = height - pad - ((value - yMin) / span) * (height - 2 * pad);
        return [x, y];
    };
    const polyline = points
        .map((p, i) => {
            const [x, y] = xy(i, p.value);
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        })
        .join(' ');
    return (
        <svg
            viewBox={`0 0 ${width} ${height}`}
            width="100%"
            height={height}
            preserveAspectRatio="none"
            className="block"
            aria-label={ariaLabel}
            role="img"
        >
            <polyline
                fill="none"
                stroke="var(--accent-mid)"
                strokeWidth="1.5"
                points={polyline}
            />
            {points.map((p, i) => {
                const [x, y] = xy(i, p.value);
                return (
                    <circle key={p.date} cx={x} cy={y} r={2.5} fill={p.color}>
                        <title>{p.tooltip}</title>
                    </circle>
                );
            })}
        </svg>
    );
};

const buildOverallWrSeries = (
    days: BattleHistoryByDay[],
    totals: BattleHistoryTotals,
): SparklinePoint[] | null => {
    // Need lifetime baseline to anchor the running overall WR.
    const lifetimeBattles = totals.lifetime_battles ?? null;
    const lifetimeWr = totals.lifetime_win_rate ?? null;
    if (lifetimeBattles == null || lifetimeWr == null || lifetimeBattles <= 0) {
        return null;
    }
    const lifetimeWins = Math.round(lifetimeBattles * (lifetimeWr / 100));
    // Walk forward through the window: cumulative includes everything up
    // to and including day i. Resulting overall WR at end of day i =
    // (priorWins + cumWins) / (priorBattles + cumBattles).
    const periodBattles = totals.battles;
    const periodWins = totals.wins;
    const priorBattles = Math.max(0, lifetimeBattles - periodBattles);
    const priorWins = Math.max(0, lifetimeWins - periodWins);

    let cumBattles = 0;
    let cumWins = 0;
    return days.map((d) => {
        cumBattles += d.battles;
        cumWins += d.wins;
        const denom = priorBattles + cumBattles;
        const overall = denom > 0 ? (100 * (priorWins + cumWins)) / denom : 0;
        const dayWr = d.battles ? (100 * d.wins) / d.battles : null;
        const dayWrText = dayWr == null ? 'no battles' : `${dayWr.toFixed(1)}% WR (${d.wins}/${d.battles})`;
        return {
            date: d.date,
            value: overall,
            color: wrColor(d.battles ? dayWr : overall),
            tooltip: `${d.date} — ${dayWrText} → overall ${overall.toFixed(2)}%`,
        };
    });
};

const BattleHistoryCard: React.FC<BattleHistoryCardProps> = ({
    playerName,
    realm,
    days = 7,
}) => {
    const [payload, setPayload] = useState<BattleHistoryPayload | null>(null);
    const [error, setError] = useState<Error | null>(null);
    const [loading, setLoading] = useState(true);
    const { theme } = useTheme();
    const palette = chartColors[theme];

    const shipTypeColor = (type: string | null | undefined): string => {
        switch (type) {
            case 'Destroyer': return palette.shipDD;
            case 'Cruiser': return palette.shipCA;
            case 'Battleship': return palette.shipBB;
            case 'AirCarrier': return palette.shipCV;
            case 'Submarine': return palette.shipSS;
            default: return palette.shipDefault;
        }
    };

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

    const [sort, setSort] = useState<{ key: SortKey; direction: SortDirection }>({
        key: 'battles', direction: 'desc',
    });

    const onSortClick = (key: SortKey) => {
        setSort((s) => s.key === key
            ? { key, direction: s.direction === 'asc' ? 'desc' : 'asc' }
            : { key, direction: DEFAULT_DIRECTION[key] });
    };

    const visibleByShip = useMemo(() => {
        const rows = (payload?.by_ship ?? []).map((r) => ({
            ...r,
            kdr: computeKdr(r.frags, r.battles, r.survived_battles),
        }));
        const sortVal = (row: typeof rows[number]): string | number => {
            const v = (row as Record<string, unknown>)[sort.key];
            if (v == null) return sort.direction === 'asc' ? Infinity : -Infinity;
            return typeof v === 'string' ? v.toLowerCase() : (v as number);
        };
        rows.sort((a, b) => {
            const av = sortVal(a);
            const bv = sortVal(b);
            if (av < bv) return sort.direction === 'asc' ? -1 : 1;
            if (av > bv) return sort.direction === 'asc' ? 1 : -1;
            return 0;
        });
        return rows.slice(0, 12);
    }, [payload?.by_ship, sort]);

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
            {(() => {
                const deaths = Math.max(0, totals.battles - totals.survived_battles);
                const kdr = deaths > 0 ? totals.frags / deaths : totals.frags;
                return (
                    <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5">
                        <div>
                            <div className="text-xs text-[var(--text-muted)]">Battles</div>
                            <div className="text-lg font-semibold text-[var(--text-strong)]">{formatInt(totals.battles)}</div>
                        </div>
                        <div>
                            <div className="text-xs text-[var(--text-muted)]">Win rate</div>
                            <div className="text-lg">
                                <WrCell
                                    periodWinRate={totals.win_rate}
                                    lifetimeWinRate={totals.lifetime_win_rate}
                                    deltaWinRate={totals.delta_win_rate}
                                    stacked
                                />
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
                        <div>
                            <div className="text-xs text-[var(--text-muted)]">KDR</div>
                            <div className="text-lg font-semibold text-[var(--text-strong)]">{kdr.toFixed(2)}</div>
                        </div>
                    </div>
                );
            })()}
            <div className="mt-4">
                {(() => {
                    const windowed = buildWindowedDays(payload.by_day, payload.window_days);
                    const wrSeries = buildOverallWrSeries(windowed, totals);
                    if (wrSeries) {
                        return (
                            <Sparkline
                                points={wrSeries}
                                ariaLabel="Overall win rate over the period"
                            />
                        );
                    }
                    // Fallback: battles-per-day shape when lifetime baseline is absent.
                    const fallback: SparklinePoint[] = windowed.map((d) => {
                        const dayWr = d.battles ? (100 * d.wins) / d.battles : null;
                        return {
                            date: d.date,
                            value: d.battles,
                            color: wrColor(d.battles ? dayWr : null),
                            tooltip: `${d.date}: ${d.battles} battles${dayWr != null ? `, ${dayWr.toFixed(1)}% WR` : ''}`,
                        };
                    });
                    return <Sparkline points={fallback} ariaLabel="Battles per day sparkline" />;
                })()}
            </div>
            <div className="mt-4 overflow-x-auto">
                <table className="w-full text-left text-sm">
                    <thead>
                        <tr className="border-b border-[var(--accent-faint)] text-xs uppercase tracking-wide text-[var(--text-muted)]">
                            <SortableTh sortKey="ship_name" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick}>Ship</SortableTh>
                            <SortableTh sortKey="ship_tier" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick}>Tier</SortableTh>
                            <SortableTh sortKey="ship_type" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick}>Type</SortableTh>
                            <SortableTh sortKey="battles" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick}>#</SortableTh>
                            <SortableTh sortKey="win_rate" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick}>Win Rate</SortableTh>
                            <SortableTh sortKey="avg_damage" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick}>Avg dmg</SortableTh>
                            <SortableTh sortKey="kdr" activeKey={sort.key} direction={sort.direction} onSortClick={onSortClick}>KDR</SortableTh>
                        </tr>
                    </thead>
                    <tbody>
                        {visibleByShip.map((row) => (
                            <tr
                                key={row.ship_id}
                                className="border-b border-[var(--accent-faint)] last:border-b-0"
                            >
                                <td className="py-1 pr-2 text-[var(--text-strong)]">
                                    {row.ship_name || `Ship ${row.ship_id}`}
                                </td>
                                <td className="py-1 px-2 text-center tabular-nums text-[var(--text-muted)]">
                                    {row.ship_tier ?? '—'}
                                </td>
                                <td
                                    className="py-1 px-2 text-center font-semibold"
                                    style={{ color: shipTypeColor(row.ship_type) }}
                                    title={row.ship_type ?? ''}
                                >
                                    {shipTypeShort(row.ship_type)}
                                </td>
                                <td className="py-1 px-2 text-center tabular-nums text-[var(--text-strong)]">{formatInt(row.battles)}</td>
                                <td className="py-1 pr-2 text-right">
                                    <WrCell
                                        periodWinRate={row.win_rate}
                                        lifetimeWinRate={row.lifetime_win_rate}
                                        deltaWinRate={row.delta_win_rate}
                                    />
                                </td>
                                <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-strong)]">{formatInt(row.avg_damage)}</td>
                                <td
                                    className="py-1 px-2 text-center tabular-nums text-[var(--text-strong)]"
                                    title={`${row.frags} frags / ${Math.max(0, row.battles - row.survived_battles)} deaths`}
                                >
                                    {formatTableKdr(row.kdr)}
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
