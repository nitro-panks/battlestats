import * as d3 from 'd3';
import { type ChartColors, type ChartTheme } from './chartTheme';

// Ranked league ordinal: Bronze < Silver < Gold < Typhoon < Hurricane. 0 =
// unknown (old rank-only seasons). Prefers the league NAME, falling back to a
// numeric league id. Shared by the ranked scatter + timeline so glyphs match.
export const leagueOrderFrom = (name?: string | null, num?: number | null): number => {
    const map: Record<string, number> = { bronze: 1, silver: 2, gold: 3, typhoon: 4, hurricane: 5 };
    const byName = map[(name ?? '').trim().toLowerCase()];
    return byName ?? (Number.isFinite(num) ? Number(num) : 0);
};

// Glyph shape by league: circle (Bronze/unknown), square-on-point (Silver — a
// 45°-rotated square), star (Gold and above). Sizes are AREA; stars/squares
// read smaller than a circle at equal area, so they're bumped up.
export const leagueSymbol = (order: number) => {
    if (order >= 3) return { type: d3.symbolStar, size: 135, rotate: 0 };
    if (order === 2) return { type: d3.symbolSquare, size: 95, rotate: 45 };
    return { type: d3.symbolCircle, size: 85, rotate: 0 };
};

// Border encodes league metal: 1px silver (Silver squares), 1px gold (Gold+
// stars); Bronze/unknown keeps the neutral card-bg contrast ring.
export const leagueStroke = (order: number, colors: ChartColors): { color: string; width: number } => {
    if (order >= 3) return { color: colors.badgeI, width: 1 };
    if (order === 2) return { color: colors.badgeII, width: 1 };
    return { color: colors.barBg, width: 1.5 };
};

// Inner hairline just inside the metal border: black in dark mode, white in
// light, so the metal ring reads against the win-rate fill.
export const leagueInnerBorderColor = (theme: ChartTheme): string => (theme === 'dark' ? '#000000' : '#ffffff');
