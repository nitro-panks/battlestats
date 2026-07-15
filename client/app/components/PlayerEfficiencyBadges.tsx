import React, { useMemo } from 'react';
import SectionHeadingWithTooltip from './SectionHeadingWithTooltip';
import EfficiencyStripPlotSVG, { badgeClassColor, type EfficiencyBadgeDot } from './EfficiencyStripPlotSVG';
import { chartColors } from '../lib/chartTheme';
import { useTheme } from '../context/ThemeContext';

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

// Fill the Efficiency panel's LOCKED_PANEL_HEIGHT_PX (1057) shell: the plot
// grows to this SVG height so the tab shell matches the Activity / Ships /
// Profile / Population tabs. 1057 minus the heading row (~28px + 12px margin)
// and the bottom badge-count legend (2x scale, ~40px + 8px margin), with a
// small cushion. Re-tune if the locked panel height changes.
const STRIP_PLOT_MIN_SVG_HEIGHT = 950;

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

const PlayerEfficiencyBadges: React.FC<PlayerEfficiencyBadgesProps> = ({
    efficiencyRows,
}) => {
    const { theme } = useTheme();
    const colors = chartColors[theme];
    // Memoized so parent re-renders don't hand the chart a fresh array and
    // relaunch its force simulation from the center.
    const dots = useMemo(() => normalizeBadgeDots(efficiencyRows), [efficiencyRows]);
    const badgeCounts = [1, 2, 3, 4].map((badgeClass) => ({
        badgeClass,
        label: BADGE_LABELS[badgeClass],
        name: BADGE_CLASS_NAMES[badgeClass],
        count: dots.filter((dot) => dot.badgeClass === badgeClass).length,
    }));

    return (
        <div>
            <div className="mb-3 flex items-center gap-3">
                <SectionHeadingWithTooltip
                    title="Efficiency Badges"
                    description="Efficiency badges mark a player's best qualifying ship performances in Tier V+ Random Battles. Each dot is one badged ship, clustered by ship type, sized by tier, and colored by badge class, so you can see at a glance where a player's peak performances cluster."
                />
            </div>
            {dots.length === 0 ? (
                <div className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-4 py-3 text-sm text-[var(--accent-light)]">
                    No Efficiency Badge data is stored for this player yet, or no qualifying ships have earned a badge.
                </div>
            ) : (
                <>
                    <EfficiencyStripPlotSVG dots={dots} theme={theme} minSvgHeight={STRIP_PLOT_MIN_SVG_HEIGHT} />
                    <div className="mt-2 flex items-center justify-center gap-8 text-2xl" aria-label="Badge counts by class">
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
                                <span className="font-['Courier_New',Courier,monospace] text-[var(--text-secondary)]">×{entry.count}</span>
                            </span>
                        ))}
                    </div>
                </>
            )}
        </div>
    );
};

export default PlayerEfficiencyBadges;
