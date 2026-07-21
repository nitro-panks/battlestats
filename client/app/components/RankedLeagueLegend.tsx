import React from 'react';
import { chartColors, type ChartTheme } from '../lib/chartTheme';

// Shape key for the ranked-season scatter's glyphs. Kept as a d3-free
// presentational component (inline SVG) so it can be imported normally without
// pulling d3 into the tabs bundle. Shapes + borders mirror
// RankedSeasonScatterSVG: circle = Bronze (neutral ring), square-on-point =
// Silver (silver border + inner hairline), star = Gold+ (gold border + inner
// hairline). Fill is neutral because SHAPE encodes league here; the chart's fill
// (win-rate color) is a separate channel not shown in the legend. `theme` is
// passed (not read from context) so the metal colors match the chart without a
// provider dependency in tests.
interface RankedLeagueLegendProps {
    theme: ChartTheme;
}

interface LegendGlyph {
    label: string;
    stroke: string | null; // metal border color; null = neutral (Bronze)
    shape: React.ReactElement; // base shape; fill/stroke applied via cloneElement
}

const RankedLeagueLegend: React.FC<RankedLeagueLegendProps> = ({ theme }) => {
    const colors = chartColors[theme];
    // badgeI = gold, badgeII = silver (same tokens the chart borders use).
    const innerBorder = theme === 'dark' ? '#000000' : '#ffffff';

    const glyphs: LegendGlyph[] = [
        { label: 'Bronze', stroke: null, shape: <circle cx="7" cy="7" r="5" /> },
        { label: 'Silver', stroke: colors.badgeII, shape: <rect x="2.5" y="2.5" width="9" height="9" transform="rotate(45 7 7)" /> },
        {
            label: 'Gold+',
            stroke: colors.badgeI,
            shape: <path d="M7 1 L8.47 4.98 L12.71 5.15 L9.38 7.77 L10.53 11.85 L7 9.5 L3.47 11.85 L4.62 7.77 L1.29 5.15 L5.53 4.98 Z" />,
        },
    ];

    return (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--text-secondary)]" aria-label="Season glyph legend by highest league">
            <span className="uppercase tracking-wide text-[var(--text-muted)]">Season glyph</span>
            {glyphs.map((glyph) => (
                <span key={glyph.label} className="inline-flex items-center gap-1.5">
                    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" className="fill-current">
                        {/* Outer: neutral fill + metal border. */}
                        {React.cloneElement(glyph.shape, {
                            fill: 'currentColor',
                            stroke: glyph.stroke ?? 'none',
                            strokeWidth: glyph.stroke ? 1 : 0,
                        })}
                        {/* Inner hairline just inside the metal border (Silver/Gold
                            only). Scaled around center; non-scaling-stroke keeps it
                            a true 1px despite the scale. */}
                        {glyph.stroke ? (
                            <g transform="translate(7 7) scale(0.72) translate(-7 -7)">
                                {React.cloneElement(glyph.shape, {
                                    fill: 'none',
                                    stroke: innerBorder,
                                    strokeWidth: 1,
                                    vectorEffect: 'non-scaling-stroke',
                                })}
                            </g>
                        ) : null}
                    </svg>
                    {glyph.label}
                </span>
            ))}
        </div>
    );
};

export default RankedLeagueLegend;
