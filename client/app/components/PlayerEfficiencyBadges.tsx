import React, { useState } from 'react';
import SectionHeadingWithTooltip from './SectionHeadingWithTooltip';

interface EfficiencyRowInput {
    ship_id?: number | null;
    top_grade_class?: number | null;
    top_grade_label?: string | null;
    badge_label?: string | null;
    ship_name?: string | null;
    ship_chart_name?: string | null;
    ship_type?: string | null;
    ship_tier?: number | null;
    nation?: string | null;
}

interface PlayerEfficiencyBadgesProps {
    efficiencyRows?: EfficiencyRowInput[] | null;
}

type SortKey = 'badge' | 'ship' | 'type' | 'tier';

interface NormalizedBadgeRow {
    shipId: number;
    shipName: string;
    shipChartName: string;
    shipType: string | null;
    shipTier: number | null;
    badgeClass: number;
    badgeLabel: string;
}

const BADGE_LABELS: Record<number, string> = {
    1: 'E',
    2: 'I',
    3: 'II',
    4: 'III',
};

const BADGE_CHIP_CLASSNAMES: Record<number, string> = {
    1: 'border-[#d4af37] bg-[#fff8db] text-[#8a5b00]',
    2: 'border-[#94a3b8] bg-[#f8fafc] text-[#475569]',
    3: 'border-[#d97706] bg-[#fff7ed] text-[#9a3412]',
    4: 'border-[#cbd5e1] bg-[#f8fafc] text-[#64748b]',
};

const BADGE_TABLE_VISIBLE_ROWS = 10;
const BADGE_TABLE_HEADER_HEIGHT_REM = 2.5;
const BADGE_TABLE_ROW_HEIGHT_REM = 2.9;

const SHIP_TYPE_LABELS: Record<string, string> = {
    battleship: 'BB',
    cruiser: 'CA',
    destroyer: 'DD',
    carrier: 'CV',
    submarine: 'Sub',
    sub: 'Sub',
};

const getShipTypeLabel = (shipType: string | null | undefined): string => {
    if (!shipType) {
        return 'Unknown';
    }

    return SHIP_TYPE_LABELS[shipType.trim().toLowerCase()] || shipType;
};
const getBadgeScore = (badgeClass: number): number => {
    if (badgeClass < 1 || badgeClass > 4) {
        return 0;
    }

    return 5 - badgeClass;
};

const getTierBand = (tier: number | null): string | null => {
    if (tier == null) {
        return null;
    }

    if (tier <= 7) {
        return 'V-VII';
    }

    if (tier === 8) {
        return 'VIII';
    }

    return 'IX-X';
};

const getDefaultSortDirection = (sortKey: SortKey): 'asc' | 'desc' => {
    if (sortKey === 'badge' || sortKey === 'tier') {
        return 'asc';
    }

    return 'asc';
};

const isRomanBadgeLabel = (badgeLabel: string): boolean => {
    return badgeLabel === 'I' || badgeLabel === 'II' || badgeLabel === 'III';
};

const normalizeBadgeRows = (
    efficiencyRows?: EfficiencyRowInput[] | null,
): NormalizedBadgeRow[] => {
    const rows: NormalizedBadgeRow[] = [];
    if (!Array.isArray(efficiencyRows)) {
        return rows;
    }

    for (const row of efficiencyRows) {
        if (!row || typeof row !== 'object') {
            continue;
        }

        const badgeClass = Number(row.top_grade_class || 0);
        const shipId = Number(row.ship_id || 0);
        if (!shipId || badgeClass < 1 || badgeClass > 4) {
            continue;
        }

        const shipName = (row.ship_name || '').trim();
        const shipChartName = (row.ship_chart_name || shipName).trim();

        rows.push({
            shipId,
            shipName: shipName || `Ship ${shipId}`,
            shipChartName: shipChartName || shipName || `Ship ${shipId}`,
            shipType: getShipTypeLabel(row.ship_type || null),
            shipTier: row.ship_tier == null ? null : Number(row.ship_tier),
            badgeClass,
            badgeLabel: BADGE_LABELS[badgeClass] || row.top_grade_label || row.badge_label || `Class ${badgeClass}`,
        });
    }

    rows.sort((left, right) => {
        if (left.badgeClass !== right.badgeClass) {
            return left.badgeClass - right.badgeClass;
        }
        return left.shipName.localeCompare(right.shipName);
    });

    return rows;
};

const SortButton: React.FC<{
    label: string;
    active: boolean;
    direction: 'asc' | 'desc';
    onClick: () => void;
}> = ({ label, active, direction, onClick }) => (
    <button
        type="button"
        onClick={onClick}
        className="inline-flex items-center gap-1"
    >
        <span>{label}</span>
        <span aria-hidden="true" className={active ? 'text-[var(--accent-dark)]' : 'text-[var(--text-secondary)]'}>{direction === 'asc' ? '↑' : '↓'}</span>
    </button>
);

const PlayerEfficiencyBadges: React.FC<PlayerEfficiencyBadgesProps> = ({
    efficiencyRows,
}) => {
    const [sortKey, setSortKey] = useState<SortKey>('badge');
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');
    const rows = normalizeBadgeRows(efficiencyRows);
    const rowsWithMetadata = rows.filter((row) => row.shipType || row.shipTier != null);
    const highestBadgeClass = rows[0]?.badgeClass || null;
    const highestBadgeLabel = highestBadgeClass ? (rows[0]?.badgeLabel || BADGE_LABELS[highestBadgeClass]) : '—';
    const badgeCounts = {
        1: rows.filter((row) => row.badgeClass === 1).length,
        2: rows.filter((row) => row.badgeClass === 2).length,
        3: rows.filter((row) => row.badgeClass === 3).length,
        4: rows.filter((row) => row.badgeClass === 4).length,
    };

    const classScores = new Map<string, number>();
    const tierScores = new Map<string, number>();
    for (const row of rowsWithMetadata) {
        const score = getBadgeScore(row.badgeClass);
        if (row.shipType) {
            classScores.set(row.shipType, (classScores.get(row.shipType) || 0) + score);
        }
        const tierBand = getTierBand(row.shipTier);
        if (tierBand) {
            tierScores.set(tierBand, (tierScores.get(tierBand) || 0) + score);
        }
    }

    const bestClassByScore = Array.from(classScores.entries()).sort((left, right) => right[1] - left[1])[0]?.[0] || '—';
    const bestTierBandByScore = Array.from(tierScores.entries()).sort((left, right) => right[1] - left[1])[0]?.[0] || '—';
    const sortedRows = [...rows].sort((left, right) => {
        const direction = sortDirection === 'asc' ? 1 : -1;

        if (sortKey === 'badge') {
            if (left.badgeClass !== right.badgeClass) {
                return (left.badgeClass - right.badgeClass) * direction;
            }
            return left.shipName.localeCompare(right.shipName) * direction;
        }

        if (sortKey === 'ship') {
            return left.shipName.localeCompare(right.shipName) * direction;
        }

        if (sortKey === 'type') {
            return ((left.shipType || 'Unknown').localeCompare(right.shipType || 'Unknown')) * direction;
        }

        if (sortKey === 'tier') {
            return (((left.shipTier || 0) - (right.shipTier || 0)) || left.shipName.localeCompare(right.shipName)) * direction;
        }

        return left.shipName.localeCompare(right.shipName) * direction;
    });

    const updateSort = (nextSortKey: SortKey) => {
        if (sortKey === nextSortKey) {
            setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
            return;
        }

        setSortKey(nextSortKey);
        setSortDirection(getDefaultSortDirection(nextSortKey));
    };

    return (
        <div>
            <div className="mb-3 flex items-center gap-3">
                <SectionHeadingWithTooltip
                    title="Efficiency Badges"
                    description="Efficiency badges mark a player's best qualifying ship performances in Tier V+ Random Battles. This section adds a peak-performance lens to the broader ship, tier, and class views by surfacing which ships have earned the strongest badge classes."
                />
                {rows.length > 0 ? (
                    <div className="ml-auto flex items-center gap-1 text-[11px] font-semibold">
                        <span
                            className={`inline-flex rounded-full border px-2 py-0.5 ${BADGE_CHIP_CLASSNAMES[1]}`}
                            title={`Expert badges: ${badgeCounts[1]}`}
                            aria-label={`Expert badges ${badgeCounts[1]}`}
                        >
                            {badgeCounts[1]}
                        </span>
                        <span className="text-[#9ca3af]">-</span>
                        <span
                            className={`inline-flex rounded-full border px-2 py-0.5 ${BADGE_CHIP_CLASSNAMES[2]}`}
                            title={`Grade I badges: ${badgeCounts[2]}`}
                            aria-label={`Grade I badges ${badgeCounts[2]}`}
                        >
                            {badgeCounts[2]}
                        </span>
                        <span className="text-[#9ca3af]">-</span>
                        <span
                            className={`inline-flex rounded-full border px-2 py-0.5 ${BADGE_CHIP_CLASSNAMES[3]}`}
                            title={`Grade II badges: ${badgeCounts[3]}`}
                            aria-label={`Grade II badges ${badgeCounts[3]}`}
                        >
                            {badgeCounts[3]}
                        </span>
                        <span className="text-[#9ca3af]">-</span>
                        <span
                            className={`inline-flex rounded-full border px-2 py-0.5 ${BADGE_CHIP_CLASSNAMES[4]}`}
                            title={`Grade III badges: ${badgeCounts[4]}`}
                            aria-label={`Grade III badges ${badgeCounts[4]}`}
                        >
                            {badgeCounts[4]}
                        </span>
                    </div>
                ) : null}
            </div>
            {rows.length === 0 ? (
                <div className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-4 py-3 text-sm text-[var(--accent-light)]">
                    No Efficiency Badge data is stored for this player yet, or no qualifying ships have earned a badge.
                </div>
            ) : (
                <>
                    <div className="grid gap-3 md:grid-cols-3">
                        <div className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-4 py-3">
                            <p className="text-xs uppercase tracking-wide text-[var(--accent-light)]">Highest Badge</p>
                            <p className="mt-2 text-lg font-semibold text-[var(--accent-dark)]">{highestBadgeLabel}</p>
                        </div>
                        <div className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-4 py-3">
                            <p className="text-xs uppercase tracking-wide text-[var(--accent-light)]">Strongest Class</p>
                            <p className="mt-2 text-lg font-semibold text-[var(--accent-dark)]">{bestClassByScore}</p>
                        </div>
                        <div className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-4 py-3">
                            <p className="text-xs uppercase tracking-wide text-[var(--accent-light)]">Strongest Tier Band</p>
                            <p className="mt-2 text-lg font-semibold text-[var(--accent-dark)]">{bestTierBandByScore}</p>
                        </div>
                    </div>
                    <div className="mt-4 overflow-x-auto rounded-md border border-[var(--border)] bg-[var(--bg-surface)]">
                        <div
                            className="overflow-y-auto"
                            style={{
                                maxHeight: `calc(${BADGE_TABLE_HEADER_HEIGHT_REM}rem + (${BADGE_TABLE_VISIBLE_ROWS} * ${BADGE_TABLE_ROW_HEIGHT_REM}rem))`,
                            }}
                        >
                        <table className="min-w-full divide-y divide-[var(--border)]">
                            <thead className="sticky top-0 bg-[var(--bg-surface)] text-[var(--accent-mid)]">
                                <tr>
                                    <th scope="col" className="px-3 py-2 text-left text-xs font-semibold tracking-wide">
                                        <SortButton label="Ship" active={sortKey === 'ship'} direction={sortDirection} onClick={() => updateSort('ship')} />
                                    </th>
                                    <th scope="col" className="px-3 py-2 text-left text-xs font-semibold tracking-wide">
                                        <SortButton label="Badge" active={sortKey === 'badge'} direction={sortDirection} onClick={() => updateSort('badge')} />
                                    </th>
                                    <th scope="col" className="px-3 py-2 text-left text-xs font-semibold tracking-wide">
                                        <SortButton label="Type" active={sortKey === 'type'} direction={sortDirection} onClick={() => updateSort('type')} />
                                    </th>
                                    <th scope="col" className="px-3 py-2 text-left text-xs font-semibold tracking-wide">
                                        <SortButton label="Tier" active={sortKey === 'tier'} direction={sortDirection} onClick={() => updateSort('tier')} />
                                    </th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-[var(--border)] text-xs">
                                {sortedRows.map((row) => (
                                    <tr key={`${row.shipId}-${row.badgeClass}`} className="align-top">
                                        <td className="px-3 py-3 text-[var(--accent-dark)]">
                                            <div className="font-medium">{row.shipName}</div>
                                        </td>
                                        <td className="px-3 py-3">
                                            <span className={`inline-flex rounded-full border px-2 py-1 text-[11px] font-semibold ${BADGE_CHIP_CLASSNAMES[row.badgeClass] || BADGE_CHIP_CLASSNAMES[4]}`}>
                                                <span style={isRomanBadgeLabel(row.badgeLabel) ? { fontFamily: 'Georgia, Times New Roman, serif' } : undefined}>
                                                    {row.badgeLabel}
                                                </span>
                                            </span>
                                        </td>
                                        <td className="px-3 py-3 text-[var(--accent-dark)]">{row.shipType || 'Unknown'}</td>
                                        <td className="px-3 py-3 text-[var(--accent-dark)]">{row.shipTier != null ? row.shipTier : '—'}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        </div>
                    </div>
                </>
            )}
        </div>
    );
};

export default PlayerEfficiencyBadges;