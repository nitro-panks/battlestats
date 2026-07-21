import React, { useEffect, useMemo, useState } from 'react';
import { badgeClassColor, chartColors, type ChartColors, type ChartTheme } from '../lib/chartTheme';
import wrColor from '../lib/wrColor';

// One badged ship, normalized from an efficiency row (see normalizeBadgeDots in
// PlayerEfficiencyBadges). shipType is the short class label (BB/CA/DD/CV/Sub);
// badgeClass is the quality grade 1..4 (1 = Expert, best).
export interface EfficiencyBadgeDot {
    shipId: number;
    shipName: string;
    shipType: string;
    shipTier: number;
    badgeClass: number;
    badgeLabel: string;
    // Career random battles + win ratio (0..1) the player logged in this ship,
    // joined server-side from battles_json; null when the ship is absent there.
    battles: number | null;
    winRatio: number | null;
}

interface EfficiencyBadgeTableProps {
    dots: EfficiencyBadgeDot[];
    theme: ChartTheme;
}

type SortKey = 'name' | 'tier' | 'type' | 'award' | 'battles' | 'wr';
type SortDir = 'asc' | 'desc';

// Award grades, best → worst, for the summary line above the table.
const GRADES: Array<{ badgeClass: number; label: string }> = [
    { badgeClass: 1, label: 'Expert' },
    { badgeClass: 2, label: 'I' },
    { badgeClass: 3, label: 'II' },
    { badgeClass: 4, label: 'III' },
];

// Canonical class order so the Type filter lists BB→CA→DD→CV→Sub; unknown
// types sort after, alphabetically.
const SHIP_TYPE_ORDER = ['BB', 'CA', 'DD', 'CV', 'Sub'];
const typeRank = (type: string): number => {
    const index = SHIP_TYPE_ORDER.indexOf(type);
    return index === -1 ? SHIP_TYPE_ORDER.length : index;
};

// The same per-class colors the battle-history table uses (BattleHistoryCard's
// shipTypeColor), keyed here off the short label instead of the full type name.
const typeColor = (colors: ChartColors, shipType: string): string => {
    switch (shipType) {
        case 'DD': return colors.shipDD;
        case 'CA': return colors.shipCA;
        case 'BB': return colors.shipBB;
        case 'CV': return colors.shipCV;
        case 'Sub': return colors.shipSS;
        default: return colors.shipDefault;
    }
};

const COLUMNS: Array<{ key: SortKey; label: string; align: 'left' | 'center' }> = [
    { key: 'name', label: 'Name', align: 'left' },
    { key: 'tier', label: 'Tier', align: 'center' },
    { key: 'type', label: 'Type', align: 'center' },
    { key: 'award', label: 'Award', align: 'center' },
    { key: 'battles', label: 'Battles', align: 'center' },
    { key: 'wr', label: 'WR%', align: 'center' },
];

// Each column's natural first direction: names/types read best A→Z, tier/award
// best-first (highest tier, Expert grade), battles/WR biggest-first.
const DEFAULT_DIR: Record<SortKey, SortDir> = {
    name: 'asc',
    tier: 'desc',
    award: 'asc',
    type: 'asc',
    battles: 'desc',
    wr: 'desc',
};

// Missing battles/WR (ship absent from battles_json) always sort to the bottom,
// regardless of direction, so a dash never outranks a real number.
const nullsLast = (av: number | null, bv: number | null): number | null => {
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return null;
};

const compareRows = (a: EfficiencyBadgeDot, b: EfficiencyBadgeDot, key: SortKey, dir: SortDir): number => {
    switch (key) {
        case 'tier':
            return a.shipTier - b.shipTier;
        case 'award':
            // badgeClass 1 (Expert) is best, so ascending == best-first.
            return a.badgeClass - b.badgeClass;
        case 'type':
            return a.shipType.localeCompare(b.shipType);
        case 'battles': {
            const sink = nullsLast(a.battles, b.battles);
            // Un-negate the sink offset so nulls stay last after the caller flips
            // the sign for a descending sort.
            if (sink !== null) return dir === 'asc' ? sink : -sink;
            return (a.battles as number) - (b.battles as number);
        }
        case 'wr': {
            const sink = nullsLast(a.winRatio, b.winRatio);
            if (sink !== null) return dir === 'asc' ? sink : -sink;
            return (a.winRatio as number) - (b.winRatio as number);
        }
        case 'name':
        default:
            return a.shipName.localeCompare(b.shipName);
    }
};

const EfficiencyBadgeTable: React.FC<EfficiencyBadgeTableProps> = ({ dots, theme }) => {
    const colors = chartColors[theme];
    const [sortKey, setSortKey] = useState<SortKey>('award');
    const [sortDir, setSortDir] = useState<SortDir>('asc');
    // 'all' = no filter on that facet.
    const [filterTier, setFilterTier] = useState<string>('all');
    const [filterType, setFilterType] = useState<string>('all');
    const [filterAward, setFilterAward] = useState<string>('all');

    const onSort = (key: SortKey) => {
        if (key === sortKey) {
            setSortDir((dir) => (dir === 'asc' ? 'desc' : 'asc'));
        } else {
            setSortKey(key);
            setSortDir(DEFAULT_DIR[key]);
        }
    };

    // A new player's badges arrive as a fresh `dots` array; clear any active
    // filter so a prior player's tier/type/award choice never hides the new set.
    useEffect(() => {
        setFilterTier('all');
        setFilterType('all');
        setFilterAward('all');
    }, [dots]);

    // The rows surviving the tier/type/award filters. Both the summary counts
    // and the sorted table read from this so the counts track the active filter.
    const filteredRows = useMemo(() => (
        dots.filter((dot) => (
            (filterTier === 'all' || dot.shipTier === Number(filterTier))
            && (filterType === 'all' || dot.shipType === filterType)
            && (filterAward === 'all' || dot.badgeClass === Number(filterAward))
        ))
    ), [dots, filterTier, filterType, filterAward]);

    const gradeCounts = useMemo(() => {
        const counts: Record<number, number> = { 1: 0, 2: 0, 3: 0, 4: 0 };
        for (const dot of filteredRows) {
            counts[dot.badgeClass] = (counts[dot.badgeClass] ?? 0) + 1;
        }
        return counts;
    }, [filteredRows]);

    // Filter dropdowns only offer facet values the player actually has, so a
    // choice can never empty the table by accident.
    const { tierOptions, typeOptions, awardOptions } = useMemo(() => {
        const tiers = new Set<number>();
        const types = new Set<string>();
        const awards = new Set<number>();
        for (const dot of dots) {
            tiers.add(dot.shipTier);
            types.add(dot.shipType);
            awards.add(dot.badgeClass);
        }
        return {
            tierOptions: Array.from(tiers).sort((a, b) => b - a),
            typeOptions: Array.from(types).sort((a, b) => typeRank(a) - typeRank(b) || a.localeCompare(b)),
            awardOptions: Array.from(awards).sort((a, b) => a - b),
        };
    }, [dots]);

    const sortedRows = useMemo(() => {
        const rows = [...filteredRows];
        rows.sort((a, b) => {
            const primary = compareRows(a, b, sortKey, sortDir);
            if (primary !== 0) {
                return sortDir === 'asc' ? primary : -primary;
            }
            // Ship name is the stable tiebreaker (always ascending, so the
            // direction toggle never scrambles equal-key rows) — except when
            // name IS the sort key, where primary already settled it.
            return sortKey === 'name' ? 0 : a.shipName.localeCompare(b.shipName);
        });
        return rows;
    }, [filteredRows, sortKey, sortDir]);

    return (
        <div className="mt-8 overflow-x-auto px-[15px]">
            <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
                <label className="inline-flex items-center gap-1.5 text-[var(--text-secondary)]">
                    <span className="text-xs font-semibold uppercase tracking-wide">Tier</span>
                    <select
                        value={filterTier}
                        onChange={(event) => setFilterTier(event.target.value)}
                        className="rounded border border-[var(--border)] bg-[var(--bg-surface)] px-2 py-1 text-[var(--text-primary)]"
                    >
                        <option value="all">All</option>
                        {tierOptions.map((tier) => (
                            <option key={tier} value={String(tier)}>T{tier}</option>
                        ))}
                    </select>
                </label>
                <label className="inline-flex items-center gap-1.5 text-[var(--text-secondary)]">
                    <span className="text-xs font-semibold uppercase tracking-wide">Type</span>
                    <select
                        value={filterType}
                        onChange={(event) => setFilterType(event.target.value)}
                        className="rounded border border-[var(--border)] bg-[var(--bg-surface)] px-2 py-1 text-[var(--text-primary)]"
                    >
                        <option value="all">All</option>
                        {typeOptions.map((type) => (
                            <option key={type} value={type}>{type}</option>
                        ))}
                    </select>
                </label>
                <label className="inline-flex items-center gap-1.5 text-[var(--text-secondary)]">
                    <span className="text-xs font-semibold uppercase tracking-wide">Award</span>
                    <select
                        value={filterAward}
                        onChange={(event) => setFilterAward(event.target.value)}
                        className="rounded border border-[var(--border)] bg-[var(--bg-surface)] px-2 py-1 text-[var(--text-primary)]"
                    >
                        <option value="all">All</option>
                        {awardOptions.map((badgeClass) => (
                            <option key={badgeClass} value={String(badgeClass)}>
                                {GRADES.find((grade) => grade.badgeClass === badgeClass)?.label ?? `Class ${badgeClass}`}
                            </option>
                        ))}
                    </select>
                </label>
            </div>
            <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-[var(--text-secondary)]" aria-label="Award totals">
                {GRADES.map((grade) => {
                    const count = gradeCounts[grade.badgeClass] ?? 0;
                    return (
                        <span key={grade.badgeClass} className={`inline-flex items-center gap-1.5 ${count === 0 ? 'text-[var(--text-muted)]' : ''}`}>
                            <span
                                aria-hidden="true"
                                className="inline-block h-2.5 w-2.5 rounded-sm"
                                style={{ backgroundColor: badgeClassColor(colors, grade.badgeClass) }}
                            />
                            {grade.label}: <span className="font-semibold tabular-nums text-[var(--text-primary)]">{count}</span>
                        </span>
                    );
                })}
            </div>
            <table className="w-full border-collapse text-sm text-[var(--text-primary)]" aria-label="Efficiency badges by ship">
                <thead>
                    <tr>
                        {COLUMNS.map((column) => {
                            const active = column.key === sortKey;
                            const alignClass = column.align === 'left' ? 'text-left' : 'text-center';
                            return (
                                <th
                                    key={column.key}
                                    scope="col"
                                    aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
                                    className={`border-b border-[var(--border)] px-3 py-2 text-xs font-semibold uppercase tracking-wide text-[var(--text-secondary)] ${alignClass}`}
                                >
                                    <button
                                        type="button"
                                        onClick={() => onSort(column.key)}
                                        className={`inline-flex items-center gap-1 uppercase tracking-wide transition-colors hover:text-[var(--text-primary)] ${active ? 'text-[var(--text-primary)]' : ''}`}
                                    >
                                        {column.label}
                                        <span aria-hidden="true" className="text-[0.65rem] leading-none">
                                            {active ? (sortDir === 'asc' ? '▲' : '▼') : ''}
                                        </span>
                                    </button>
                                </th>
                            );
                        })}
                    </tr>
                </thead>
                <tbody>
                    {sortedRows.map((row) => (
                        <tr key={row.shipId} className="border-b border-[var(--border)]">
                            <td className="px-3 py-1.5 text-left">{row.shipName}</td>
                            <td className="px-3 py-1.5 text-center tabular-nums">{row.shipTier}</td>
                            <td className="px-3 py-1.5 text-center font-semibold" style={{ color: typeColor(colors, row.shipType) }}>{row.shipType}</td>
                            <td className="px-3 py-1.5 text-center">{row.badgeLabel}</td>
                            <td className="px-3 py-1.5 text-center tabular-nums">
                                {row.battles == null ? <span className="text-[var(--text-muted)]">—</span> : row.battles.toLocaleString()}
                            </td>
                            <td className="px-3 py-1.5 text-center tabular-nums">
                                {row.winRatio == null ? (
                                    <span className="text-[var(--text-muted)]">—</span>
                                ) : (
                                    <span className="font-semibold" style={{ color: wrColor(row.winRatio * 100) }}>
                                        {`${(row.winRatio * 100).toFixed(1)}%`}
                                    </span>
                                )}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
};

export default EfficiencyBadgeTable;
