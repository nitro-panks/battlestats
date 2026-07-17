import React, { useEffect, useMemo, useState } from 'react';
import InfoTooltip from './InfoTooltip';
import EfficiencyStripPlotSVG, { badgeClassColor, type EfficiencyBadgeDot } from './EfficiencyStripPlotSVG';
import { chartColors } from '../lib/chartTheme';
import { useTheme } from '../context/ThemeContext';
import { useRealm } from '../context/RealmContext';
import { trackEvent } from '../lib/umami';

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

// Badge level names as the game presents them: Expert, I, II, III.
const BADGE_LABELS: Record<number, string> = {
    1: 'Expert',
    2: 'I',
    3: 'II',
    4: 'III',
};

const BADGE_CLASS_NAMES: Record<number, string> = {
    1: 'Expert',
    2: 'Badge I',
    3: 'Badge II',
    4: 'Badge III',
};

const SHIP_TYPE_LABELS: Record<string, string> = {
    battleship: 'BB',
    cruiser: 'CA',
    destroyer: 'DD',
    carrier: 'CV',
    aircarrier: 'CV',
    'aircraft carrier': 'CV',
    submarine: 'Sub',
    sub: 'Sub',
};

// Canonical filter-button order for the type row; unknown labels sort last.
const SHIP_TYPE_ORDER = ['BB', 'CA', 'DD', 'CV', 'Sub'];

// Fill the Efficiency panel's LOCKED_PANEL_HEIGHT_PX (1057) shell: the plot
// grows to this SVG height so the tab shell matches the Activity / Ships /
// Profile / Population tabs. 1057 minus the heading row (~28px + 12px margin)
// and the bottom badge-count legend (2x scale, ~40px + 8px margin), with a
// small cushion. Re-tune if the locked panel height changes.
const STRIP_PLOT_MIN_SVG_HEIGHT = 800;

const getShipTypeLabel = (shipType: string | null | undefined): string => {
    if (!shipType) {
        return 'Unknown';
    }

    return SHIP_TYPE_LABELS[shipType.trim().toLowerCase()] || shipType;
};

// Rows must carry a plottable (type, tier) cell; rows missing a tier (ships
// absent from the catalog) are dropped from both the plot and the legend counts
// so the two never disagree.
const normalizeBadgeDots = (
    efficiencyRows?: EfficiencyRowInput[] | null,
): EfficiencyBadgeDot[] => {
    const dots: EfficiencyBadgeDot[] = [];
    if (!Array.isArray(efficiencyRows)) {
        return dots;
    }

    for (const row of efficiencyRows) {
        if (!row || typeof row !== 'object') {
            continue;
        }

        const badgeClass = Number(row.top_grade_class || 0);
        const shipId = Number(row.ship_id || 0);
        const shipTier = row.ship_tier == null ? null : Number(row.ship_tier);
        if (!shipId || badgeClass < 1 || badgeClass > 4 || shipTier == null || !Number.isFinite(shipTier)) {
            continue;
        }

        const shipName = (row.ship_name || '').trim();

        dots.push({
            shipId,
            shipName: shipName || `Ship ${shipId}`,
            shipType: getShipTypeLabel(row.ship_type || null),
            shipTier,
            badgeClass,
            badgeLabel: BADGE_LABELS[badgeClass] || row.top_grade_label || row.badge_label || `Class ${badgeClass}`,
        });
    }

    return dots;
};

// True when the player has at least one plottable efficiency badge. Shares
// normalizeBadgeDots so the "dark the Efficiency tab" gate and the panel's own
// empty state ("No Efficiency Badge data…") can never disagree.
export const hasEfficiencyBadges = (
    efficiencyRows?: EfficiencyRowInput[] | null,
): boolean => normalizeBadgeDots(efficiencyRows).length > 0;

// null means "all selected" — the default, and what an emptied or completed
// selection collapses back to. Same toggle semantics as the Ships tab: a click
// while All is active solos that value.
const toggleFilterValue = <T extends string | number>(
    current: T[] | null,
    value: T,
    available: T[],
): T[] | null => {
    if (current === null) {
        return [value];
    }
    if (current.includes(value)) {
        const next = current.filter((entry) => entry !== value);
        return next.length > 0 ? next : null;
    }
    const next = [...current, value];
    return next.length === available.length ? null : next;
};

const PlayerEfficiencyBadges: React.FC<PlayerEfficiencyBadgesProps> = ({
    efficiencyRows,
}) => {
    const { theme } = useTheme();
    const { realm } = useRealm();
    const colors = chartColors[theme];
    // Memoized so parent re-renders don't hand the chart a fresh array and
    // relaunch its force simulation from the center.
    const dots = useMemo(() => normalizeBadgeDots(efficiencyRows), [efficiencyRows]);

    const [selectedTypes, setSelectedTypes] = useState<string[] | null>(null);
    const [selectedTiers, setSelectedTiers] = useState<number[] | null>(null);

    // New badge data (player/realm change) resets both filters to All so a
    // solo selection never carries over and blanks the next player's plot.
    useEffect(() => {
        setSelectedTypes(null);
        setSelectedTiers(null);
    }, [dots]);

    const availableTypes = useMemo(() => {
        const rank = (label: string) => {
            const index = SHIP_TYPE_ORDER.indexOf(label);
            return index === -1 ? SHIP_TYPE_ORDER.length : index;
        };
        return Array.from(new Set(dots.map((dot) => dot.shipType)))
            .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
    }, [dots]);
    const availableTiers = useMemo(
        () => Array.from(new Set(dots.map((dot) => dot.shipTier))).sort((a, b) => b - a),
        [dots],
    );

    // Memoized for the same reason as `dots`: the chart must only receive a
    // fresh array when the filter selection actually changes.
    const visibleDots = useMemo(() => dots.filter((dot) => (
        (selectedTypes === null || selectedTypes.includes(dot.shipType))
        && (selectedTiers === null || selectedTiers.includes(dot.shipTier))
    )), [dots, selectedTypes, selectedTiers]);

    const toggleType = (shipType: string) => {
        trackEvent('efficiency-filter', { realm, control: 'type', value: shipType });
        setSelectedTypes((current) => toggleFilterValue(current, shipType, availableTypes));
    };

    const toggleTier = (tier: number) => {
        trackEvent('efficiency-filter', { realm, control: 'tier', value: tier });
        setSelectedTiers((current) => toggleFilterValue(current, tier, availableTiers));
    };

    const selectAllTypes = () => {
        trackEvent('efficiency-filter', { realm, control: 'type', value: 'all' });
        setSelectedTypes(null);
    };

    const selectAllTiers = () => {
        trackEvent('efficiency-filter', { realm, control: 'tier', value: 'all' });
        setSelectedTiers(null);
    };

    const filterButtonClass = (selected: boolean) => (selected
        ? 'border border-[var(--accent-mid)] bg-[var(--accent-faint)] px-2 py-1 text-xs font-medium text-[var(--accent-dark)]'
        : 'border border-[var(--border)] bg-[var(--bg-surface)] px-2 py-1 text-xs font-medium text-[var(--text-secondary)]');

    const badgeCounts = [1, 2, 3, 4].map((badgeClass) => ({
        badgeClass,
        label: BADGE_LABELS[badgeClass],
        name: BADGE_CLASS_NAMES[badgeClass],
        count: visibleDots.filter((dot) => dot.badgeClass === badgeClass).length,
    }));

    return (
        <div>
            <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2">
                <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--accent-mid)]">Efficiency Badges</h3>
                {dots.length > 0 && (
                    <>
                        <div className="flex flex-wrap items-center gap-1" aria-label="Filter badges by ship type">
                            <button
                                key="all-types"
                                type="button"
                                aria-pressed={selectedTypes === null}
                                className={filterButtonClass(selectedTypes === null)}
                                onClick={selectAllTypes}
                            >
                                All
                            </button>
                            {availableTypes.map((shipType) => (
                                <button
                                    key={shipType}
                                    type="button"
                                    aria-pressed={selectedTypes !== null && selectedTypes.includes(shipType)}
                                    className={filterButtonClass(selectedTypes !== null && selectedTypes.includes(shipType))}
                                    onClick={() => toggleType(shipType)}
                                >
                                    {shipType}
                                </button>
                            ))}
                        </div>
                        <span aria-hidden="true" className="h-4 w-px bg-[var(--border)]" />
                        <div className="flex flex-wrap items-center gap-1" aria-label="Filter badges by ship tier">
                            <button
                                key="all-tiers"
                                type="button"
                                aria-pressed={selectedTiers === null}
                                className={filterButtonClass(selectedTiers === null)}
                                onClick={selectAllTiers}
                            >
                                All
                            </button>
                            {availableTiers.map((tier) => (
                                <button
                                    key={tier}
                                    type="button"
                                    aria-pressed={selectedTiers !== null && selectedTiers.includes(tier)}
                                    className={filterButtonClass(selectedTiers !== null && selectedTiers.includes(tier))}
                                    onClick={() => toggleTier(tier)}
                                >
                                    T{tier}
                                </button>
                            ))}
                        </div>
                    </>
                )}
                <InfoTooltip
                    label="Efficiency Badges"
                    description="Efficiency badges mark a player's best qualifying ship performances in Tier V+ Random Battles. Each dot is one badged ship, clustered by ship type, sized by tier, and colored by badge class, so you can see at a glance where a player's peak performances cluster. Use the type and tier filters to thin a crowded plot."
                    align="right"
                    className="ml-auto"
                />
            </div>
            {dots.length === 0 ? (
                <div className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-4 py-3 text-sm text-[var(--accent-light)]">
                    No Efficiency Badge data is stored for this player yet, or no qualifying ships have earned a badge.
                </div>
            ) : visibleDots.length === 0 ? (
                <div className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-4 py-3 text-sm text-[var(--accent-light)]">
                    No badges match the selected type and tier filters.
                </div>
            ) : (
                <>
                    <EfficiencyStripPlotSVG dots={visibleDots} theme={theme} minSvgHeight={STRIP_PLOT_MIN_SVG_HEIGHT} />
                    <div className="mt-2 flex items-center justify-center gap-8 font-serif text-2xl" aria-label="Badge counts by class">
                        {badgeCounts.map((entry) => (
                            <span
                                key={entry.badgeClass}
                                className={`inline-flex items-center gap-3 ${entry.count === 0 ? 'opacity-50' : ''}`}
                                title={`${entry.name}: ${entry.count}`}
                            >
                                <span
                                    aria-hidden="true"
                                    className="inline-block h-5 w-5 rounded-full"
                                    style={{ backgroundColor: badgeClassColor(colors, entry.badgeClass) }}
                                />
                                <span className="font-semibold text-[var(--text-secondary)]">{entry.label}</span>
                                <span className="text-[var(--text-secondary)]">×{entry.count}</span>
                            </span>
                        ))}
                    </div>
                </>
            )}
        </div>
    );
};

export default PlayerEfficiencyBadges;
