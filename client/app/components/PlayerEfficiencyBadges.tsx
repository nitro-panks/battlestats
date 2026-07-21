import React, { useMemo } from 'react';
import InfoTooltip from './InfoTooltip';
import EfficiencyBadgeTable, { type EfficiencyBadgeDot } from './EfficiencyBadgeTable';
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
    pvp_battles?: number | null;
    win_ratio?: number | null;
}

interface PlayerEfficiencyBadgesProps {
    efficiencyRows?: EfficiencyRowInput[] | null;
    // Scroll cap (px) for the badge table, forwarded to EfficiencyBadgeTable so
    // a badge-heavy player's table matches the shared insights-panel height.
    maxTableHeightPx?: number;
}

// Badge level names as the game presents them: Expert, I, II, III.
const BADGE_LABELS: Record<number, string> = {
    1: 'Expert',
    2: 'I',
    3: 'II',
    4: 'III',
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

const getShipTypeLabel = (shipType: string | null | undefined): string => {
    if (!shipType) {
        return 'Unknown';
    }

    return SHIP_TYPE_LABELS[shipType.trim().toLowerCase()] || shipType;
};

// Rows must carry a (type, tier) pair; rows missing a tier (ships absent from
// the catalog) are dropped so the table never lists a badge it can't place.
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
        const battles = row.pvp_battles == null ? null : Number(row.pvp_battles);
        const winRatio = row.win_ratio == null ? null : Number(row.win_ratio);

        dots.push({
            shipId,
            shipName: shipName || `Ship ${shipId}`,
            shipType: getShipTypeLabel(row.ship_type || null),
            shipTier,
            badgeClass,
            badgeLabel: BADGE_LABELS[badgeClass] || row.top_grade_label || row.badge_label || `Class ${badgeClass}`,
            battles: battles != null && Number.isFinite(battles) ? battles : null,
            winRatio: winRatio != null && Number.isFinite(winRatio) ? winRatio : null,
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

const PlayerEfficiencyBadges: React.FC<PlayerEfficiencyBadgesProps> = ({
    efficiencyRows,
    maxTableHeightPx,
}) => {
    const { theme } = useTheme();
    const dots = useMemo(() => normalizeBadgeDots(efficiencyRows), [efficiencyRows]);

    return (
        <div>
            {/* pt-2.5/pl-[15px] is the shared tab-top header spot across the
                Profile/Efficiency/Clan Battles insight tabs. */}
            <div className="mb-3 flex items-start gap-x-3 pt-2.5 pl-[15px]">
                <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--accent-mid)]">Efficiency Badges</h3>
                <InfoTooltip
                    label="Efficiency Badges"
                    description="Efficiency badges mark a player's best qualifying ship performances in Tier V+ Random Battles. This table lists each badged ship with its tier, class, and award grade (Expert, I, II, III). Click any column header to sort."
                    align="right"
                    className="ml-auto"
                />
            </div>
            {dots.length === 0 ? (
                <div className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-4 py-3 text-sm text-[var(--accent-light)]">
                    No Efficiency Badge data is stored for this player yet, or no qualifying ships have earned a badge.
                </div>
            ) : (
                <EfficiencyBadgeTable dots={dots} theme={theme} maxTableHeightPx={maxTableHeightPx} />
            )}
        </div>
    );
};

export default PlayerEfficiencyBadges;
