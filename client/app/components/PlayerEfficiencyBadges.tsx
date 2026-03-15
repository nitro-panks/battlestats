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

interface RandomsRowInput {
    ship_name?: string | null;
    ship_chart_name?: string | null;
    ship_type?: string | null;
    ship_tier?: number | null;
    pvp_battles?: number | null;
    wins?: number | null;
    win_ratio?: number | null;
}

interface PlayerEfficiencyBadgesProps {
    efficiencyRows?: EfficiencyRowInput[] | null;
    randomsRows?: RandomsRowInput[] | null;
}

type SortKey = 'badge' | 'ship' | 'type' | 'tier' | 'topShips';

interface NormalizedBadgeRow {
    shipId: number;
    shipName: string;
    shipChartName: string;
    shipType: string | null;
    shipTier: number | null;
    badgeClass: number;
    badgeLabel: string;
    topShipOverlap: boolean;
    randomBattles: number;
}

const BADGE_LABELS: Record<number, string> = {
    1: 'Expert',
    2: 'Grade I',
    3: 'Grade II',
    4: 'Grade III',
};

const BADGE_CHIP_CLASSNAMES: Record<number, string> = {
    1: 'border-[#d4af37] bg-[#fff8db] text-[#8a5b00]',
    2: 'border-[#94a3b8] bg-[#f8fafc] text-[#475569]',
    3: 'border-[#d97706] bg-[#fff7ed] text-[#9a3412]',
    4: 'border-[#cbd5e1] bg-[#f8fafc] text-[#64748b]',
};

const normalizeShipKey = (value: string | null | undefined): string => {
    return (value || '').trim().toLowerCase();
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
        return 'Tier V-VII';
    }

    if (tier === 8) {
        return 'Tier VIII';
    }

    return 'Tier IX-X';
};

const getDefaultSortDirection = (sortKey: SortKey): 'asc' | 'desc' => {
    if (sortKey === 'badge' || sortKey === 'tier' || sortKey === 'topShips') {
        return 'asc';
    }

    return 'asc';
};

const normalizeBadgeRows = (
    efficiencyRows?: EfficiencyRowInput[] | null,
    randomsRows?: RandomsRowInput[] | null,
): NormalizedBadgeRow[] => {
    const randomsByShipName = new Map<string, RandomsRowInput>();
    const randomsByChartName = new Map<string, RandomsRowInput>();

    if (Array.isArray(randomsRows)) {
        for (const row of randomsRows) {
            if (!row || typeof row !== 'object') {
                continue;
            }

            const shipNameKey = normalizeShipKey(row.ship_name);
            const chartNameKey = normalizeShipKey(row.ship_chart_name);

            if (shipNameKey && !randomsByShipName.has(shipNameKey)) {
                randomsByShipName.set(shipNameKey, row);
            }
            if (chartNameKey && !randomsByChartName.has(chartNameKey)) {
                randomsByChartName.set(chartNameKey, row);
            }
        }
    }

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
        const matchedRandomRow = randomsByShipName.get(normalizeShipKey(shipName))
            || randomsByChartName.get(normalizeShipKey(shipChartName));

        rows.push({
            shipId,
            shipName: shipName || `Ship ${shipId}`,
            shipChartName: shipChartName || shipName || `Ship ${shipId}`,
            shipType: row.ship_type || null,
            shipTier: row.ship_tier == null ? null : Number(row.ship_tier),
            badgeClass,
            badgeLabel: row.top_grade_label || row.badge_label || BADGE_LABELS[badgeClass] || `Class ${badgeClass}`,
            topShipOverlap: Boolean(matchedRandomRow),
            randomBattles: Number(matchedRandomRow?.pvp_battles || 0),
        });
    }

    rows.sort((left, right) => {
        if (left.badgeClass !== right.badgeClass) {
            return left.badgeClass - right.badgeClass;
        }
        if (left.topShipOverlap !== right.topShipOverlap) {
            return left.topShipOverlap ? -1 : 1;
        }
        if (left.randomBattles !== right.randomBattles) {
            return right.randomBattles - left.randomBattles;
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
        <span aria-hidden="true" className={active ? 'text-[#084594]' : 'text-[#6baed6]'}>{direction === 'asc' ? '↑' : '↓'}</span>
    </button>
);

const PlayerEfficiencyBadges: React.FC<PlayerEfficiencyBadgesProps> = ({
    efficiencyRows,
    randomsRows,
}) => {
    const [sortKey, setSortKey] = useState<SortKey>('badge');
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');
    const rows = normalizeBadgeRows(efficiencyRows, randomsRows);
    const rowsWithMetadata = rows.filter((row) => row.shipType || row.shipTier != null);
    const expertShips = rows.filter((row) => row.badgeClass === 1).length;
    const gradeIPlusShips = rows.filter((row) => row.badgeClass <= 2).length;
    const highestBadgeClass = rows[0]?.badgeClass || null;
    const highestBadgeLabel = highestBadgeClass ? (rows[0]?.badgeLabel || BADGE_LABELS[highestBadgeClass]) : '—';

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
    const badgeBreadthLabel = rows.length <= 3
        ? 'Concentrated'
        : classScores.size >= 3
            ? 'Broad'
            : 'Mixed';

    const sortedRows = [...rows].sort((left, right) => {
        const direction = sortDirection === 'asc' ? 1 : -1;

        if (sortKey === 'badge') {
            if (left.badgeClass !== right.badgeClass) {
                return (left.badgeClass - right.badgeClass) * direction;
            }
            if (left.topShipOverlap !== right.topShipOverlap) {
                return (left.topShipOverlap ? -1 : 1) * direction;
            }
            return (right.randomBattles - left.randomBattles) * direction;
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

        if (left.topShipOverlap !== right.topShipOverlap) {
            return (left.topShipOverlap ? -1 : 1) * direction;
        }
        return (left.randomBattles - right.randomBattles) * direction;
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
            <SectionHeadingWithTooltip
                title="Efficiency Badges"
                description="Efficiency badges mark a player's best qualifying ship performances in Tier V+ Random Battles. This section adds a peak-performance lens to the existing top-ships and tier or type views by surfacing which ships have earned the strongest badge classes."
                className="mb-3"
            />
            {rows.length === 0 ? (
                <div className="rounded-md border border-[#dbe9f6] bg-[#f7fbff] px-4 py-3 text-sm text-[#4292c6]">
                    No Efficiency Badge data is stored for this player yet, or no qualifying ships have earned a badge.
                </div>
            ) : (
                <>
                    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                        <div className="rounded-md border border-[#dbe9f6] bg-[#f7fbff] px-4 py-3">
                            <p className="text-xs uppercase tracking-wide text-[#4292c6]">Highest Badge</p>
                            <p className="mt-2 text-lg font-semibold text-[#084594]">{highestBadgeLabel}</p>
                            <p className="mt-1 text-xs text-[#6baed6]">{expertShips} expert ships, {gradeIPlusShips} Grade I+ ships</p>
                        </div>
                        <div className="rounded-md border border-[#dbe9f6] bg-[#f7fbff] px-4 py-3">
                            <p className="text-xs uppercase tracking-wide text-[#4292c6]">Strongest Class</p>
                            <p className="mt-2 text-lg font-semibold text-[#084594]">{bestClassByScore}</p>
                            <p className="mt-1 text-xs text-[#6baed6]">Weighted from badge strength, not ship volume</p>
                        </div>
                        <div className="rounded-md border border-[#dbe9f6] bg-[#f7fbff] px-4 py-3">
                            <p className="text-xs uppercase tracking-wide text-[#4292c6]">Strongest Tier Band</p>
                            <p className="mt-2 text-lg font-semibold text-[#084594]">{bestTierBandByScore}</p>
                            <p className="mt-1 text-xs text-[#6baed6]">Built from rows with usable tier metadata</p>
                        </div>
                        <div className="rounded-md border border-[#dbe9f6] bg-[#f7fbff] px-4 py-3">
                            <p className="text-xs uppercase tracking-wide text-[#4292c6]">Profile Shape</p>
                            <p className="mt-2 text-lg font-semibold text-[#084594]">{badgeBreadthLabel}</p>
                            <p className="mt-1 text-xs text-[#6baed6]">{rowsWithMetadata.length} of {rows.length} rows usable for class or tier rollups</p>
                        </div>
                    </div>
                    <div className="mt-4 max-h-[332px] overflow-auto rounded-md border border-[#dbe9f6] bg-white">
                        <table className="min-w-full divide-y divide-[#dbe9f6] text-sm">
                            <thead className="sticky top-0 bg-[#f7fbff] text-[#2171b5]">
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
                                    <th scope="col" className="px-3 py-2 text-left text-xs font-semibold tracking-wide">
                                        <SortButton label="Top Ships" active={sortKey === 'topShips'} direction={sortDirection} onClick={() => updateSort('topShips')} />
                                    </th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-[#eff3ff]">
                                {sortedRows.map((row) => (
                                    <tr key={`${row.shipId}-${row.badgeClass}`} className="align-top">
                                        <td className="px-3 py-3 text-[#084594]">
                                            <div className="font-medium">{row.shipName}</div>
                                        </td>
                                        <td className="px-3 py-3">
                                            <span className={`inline-flex rounded-full border px-2 py-1 text-xs font-semibold ${BADGE_CHIP_CLASSNAMES[row.badgeClass] || BADGE_CHIP_CLASSNAMES[4]}`}>
                                                {row.badgeLabel}
                                            </span>
                                        </td>
                                        <td className="px-3 py-3 text-[#084594]">{row.shipType || 'Unknown'}</td>
                                        <td className="px-3 py-3 text-[#084594]">{row.shipTier != null ? row.shipTier : '—'}</td>
                                        <td className="px-3 py-3 text-[#084594]">{row.topShipOverlap ? 'Yes' : 'No'}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </>
            )}
        </div>
    );
};

export default PlayerEfficiencyBadges;