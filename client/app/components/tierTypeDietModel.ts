/**
 * Model + scales behind TierTypeDietSVG (the Profile tab's ship-diet figure).
 *
 * Sits beside its component, following the payload-helper-next-to-chart
 * convention. Everything here is derived from the captain's own
 * `player_cells`; the payload's population layers (`tiles`, `trend`,
 * `tracked_population`) are deliberately unread — the figure's subject is the
 * captain, not the crowd.
 */
import * as d3 from 'd3';
import { formatCompactCount, wrColorByRatio, type ChartTheme } from '../lib/chartTheme';
import type { TierTypePayload, TierTypePlayerCell } from './playerProfileChartData';

export const SHIP_TYPE_ABBREV: Record<string, string> = {
    'Destroyer': 'DD',
    'Cruiser': 'CA',
    'Battleship': 'BB',
    'Aircraft Carrier': 'CV',
    'AirCarrier': 'CV',
    'Carrier': 'CV',
    'Submarine': 'SS',
};

/**
 * Battle count at which a cell's win rate is drawn at full colour.
 *
 * The standard error of a win-rate proportion runs ~0.5/sqrt(n): about 35pp at
 * 2 battles, 7pp at 50, 5pp at 100. Confidence therefore tracks sqrt(n), and
 * 100 battles is where the error narrows enough for the colour to be making a
 * claim worth believing.
 */
export const CONFIDENCE_FULL_BATTLES = 100;

export const confidenceFromBattles = (battles: number): number =>
    Math.min(1, Math.sqrt(Math.max(battles, 0) / CONFIDENCE_FULL_BATTLES));

/** A cell whose colour is damped this far toward neutral makes no real claim. */
export const THIN_EVIDENCE_CONFIDENCE = 0.45;

// One fixed neutral per theme. Every thin cell collapses toward THIS colour
// rather than toward a desaturated version of its own win-rate band, so a
// 2-battle 100% cell and a 2-battle 30% cell are indistinguishable — which is
// the honest reading. Desaturating each band in place would leak the win rate
// back through lightness.
const CONFIDENCE_NEUTRAL: Record<ChartTheme, string> = {
    light: '#cbd5e1',
    dark: '#39424e',
};

/**
 * Win-rate colour damped toward neutral by sample size, so full colour is only
 * ever spent on a cell that has earned it.
 */
export const confidenceFadedWrColor = (
    winRatio: number,
    battles: number,
    theme: ChartTheme,
): string => d3.interpolateLab(
    CONFIDENCE_NEUTRAL[theme],
    wrColorByRatio(winRatio),
)(confidenceFromBattles(battles));

export interface DietMarginTotal {
    /** Ship-type name or tier number as a string; also the band-scale key. */
    key: string;
    battles: number;
    wins: number;
    winRatio: number;
}

export interface TierTypeDietModel {
    /** Every ship type the payload knows, in payload order — absence is data. */
    shipTypes: string[];
    /** Tier rows trimmed to the span the captain sails; interior gaps kept. */
    tiers: number[];
    cells: TierTypePlayerCell[];
    /** Column margin: replaces the standalone "Performance by Ship Type" chart. */
    byType: DietMarginTotal[];
    /** Row margin: one total per tier in `tiers` order (zeros kept, like byType). */
    byTier: DietMarginTotal[];
    totalBattles: number;
    maxCellBattles: number;
}

const marginTotal = (key: string, battles: number, wins: number): DietMarginTotal => ({
    key,
    battles,
    wins,
    winRatio: battles > 0 ? wins / battles : 0,
});

export const buildTierTypeDietModel = (payload: TierTypePayload): TierTypeDietModel => {
    const shipTypes = payload.x_labels ?? [];
    const cells = payload.player_cells ?? [];

    // Trim tier rows to the captain's own span. An all-empty tier 11 row (and
    // any empty tail below the lowest tier sailed) is ink spent on nothing;
    // interior gaps stay, because a skipped tier inside the span is a fact
    // about the diet.
    const playedTiers = cells.map((row) => row.ship_tier);
    const fallbackTiers = payload.y_values ?? [];
    const minTier = playedTiers.length ? Math.min(...playedTiers) : Math.min(...fallbackTiers);
    const maxTier = playedTiers.length ? Math.max(...playedTiers) : Math.max(...fallbackTiers);
    const tiers = fallbackTiers
        .filter((tier) => tier >= minTier && tier <= maxTier)
        .slice()
        .sort((a, b) => b - a);

    const typeAggregate = new Map<string, { battles: number; wins: number; }>();
    const tierAggregate = new Map<number, { battles: number; wins: number; }>();
    cells.forEach((row) => {
        const type = typeAggregate.get(row.ship_type) ?? { battles: 0, wins: 0 };
        type.battles += row.pvp_battles;
        type.wins += row.wins;
        typeAggregate.set(row.ship_type, type);
        const tier = tierAggregate.get(row.ship_tier) ?? { battles: 0, wins: 0 };
        tier.battles += row.pvp_battles;
        tier.wins += row.wins;
        tierAggregate.set(row.ship_tier, tier);
    });

    return {
        shipTypes,
        tiers,
        cells,
        byType: shipTypes.map((shipType) => {
            const aggregate = typeAggregate.get(shipType) ?? { battles: 0, wins: 0 };
            return marginTotal(shipType, aggregate.battles, aggregate.wins);
        }),
        byTier: tiers.map((tier) => {
            const aggregate = tierAggregate.get(tier) ?? { battles: 0, wins: 0 };
            return marginTotal(String(tier), aggregate.battles, aggregate.wins);
        }),
        totalBattles: d3.sum(cells, (row: TierTypePlayerCell) => row.pvp_battles),
        maxCellBattles: d3.max(cells, (row: TierTypePlayerCell) => row.pvp_battles) ?? 1,
    };
};

export const formatWinPercent = (ratio: number): string => `${(ratio * 100).toFixed(0)}%`;

// Vertical layout of the bar figure (TierTypeDietSVG). Exported so the Map
// view can take exactly the same height for the same payload: switching
// Bars <-> Map must not move anything below the figure.
export const DIET_ROW_STEP = 26;
export const DIET_TYPE_LABEL_BAND = 21;
export const DIET_COL_BAR_MAX = 26;
// Air between the grid's last tier row and the by-class margin below it, on
// top of the class-label band. The two are different readings of the data —
// individual cells above, class totals below — and need to look it.
export const DIET_CLASS_MARGIN_GAP = 26;
const DIET_TOP_PAD = 10;
// Bottom pad only: the figure carries no legend, so this just keeps the class
// margin's win-rate line off the SVG's bottom edge.
const DIET_BOTTOM_PAD = 6;
const DIET_MARGIN_BAND = DIET_TYPE_LABEL_BAND + DIET_CLASS_MARGIN_GAP + DIET_COL_BAR_MAX + 30;

/** Total SVG height of the bar figure for a model with this many tier rows. */
export const dietFigureHeight = (tierCount: number): number =>
    DIET_TOP_PAD + tierCount * DIET_ROW_STEP + DIET_MARGIN_BAND + DIET_BOTTOM_PAD;

// A tooltip line: plain muted text, or a value/label pair on the shared
// two-column grid. Mirrors the contract BattleHistoryTreemaps established so
// the overlays stay one pattern. Shared by the bar figure (TierTypeDietSVG)
// and its treemap view (TierTypeTreemapSVG), so a cell reads the same in both.
export type DietTooltipLine = string | { value: string; label: string; color?: string };

export const cellTooltipLines = (
    row: TierTypePlayerCell,
    model: TierTypeDietModel,
    theme: ChartTheme,
    drillable = false,
): DietTooltipLine[] => {
    const confidence = confidenceFromBattles(row.pvp_battles);
    const lines: DietTooltipLine[] = [
        `Tier ${row.ship_tier} ${SHIP_TYPE_ABBREV[row.ship_type] ?? row.ship_type}`,
        { value: formatCompactCount(row.pvp_battles), label: 'battles' },
        {
            value: `${(row.win_ratio * 100).toFixed(1)}%`,
            label: 'win rate',
            color: confidenceFadedWrColor(row.win_ratio, row.pvp_battles, theme),
        },
        { value: formatWinPercent(row.pvp_battles / Math.max(model.totalBattles, 1)), label: 'of your battles' },
    ];
    if (confidence < THIN_EVIDENCE_CONFIDENCE) {
        lines.push('Too few battles here to read the win rate.');
    }
    if (drillable) {
        lines.push('Click to see these ships');
    }
    return lines;
};

/**
 * Tooltip for one margin total (a whole tier, or a whole class): the treemap
 * panels' hover, on the same two-column contract as the per-cell lines.
 */
export const marginTooltipLines = (
    axis: 'tier' | 'class',
    total: DietMarginTotal,
    model: TierTypeDietModel,
    theme: ChartTheme,
    drillable = false,
): DietTooltipLine[] => {
    const lines: DietTooltipLine[] = [
        axis === 'tier' ? `Tier ${total.key}` : `All ${SHIP_TYPE_ABBREV[total.key] ?? total.key}`,
        { value: formatCompactCount(total.battles), label: 'battles' },
        {
            value: `${(total.winRatio * 100).toFixed(1)}%`,
            label: 'win rate',
            color: confidenceFadedWrColor(total.winRatio, total.battles, theme),
        },
        { value: formatWinPercent(total.battles / Math.max(model.totalBattles, 1)), label: 'of your battles' },
    ];
    if (confidenceFromBattles(total.battles) < THIN_EVIDENCE_CONFIDENCE) {
        lines.push('Too few battles here to read the win rate.');
    }
    if (drillable) {
        lines.push('Click to see these ships');
    }
    return lines;
};
