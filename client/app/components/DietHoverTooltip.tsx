import React from 'react';
import type { DietTooltipLine } from './tierTypeDietModel';

/**
 * The hover overlay the Profile tab's tier figure shows beside a cell, shared by
 * its bar view (TierTypeDietSVG) and its treemap view (TierTypeTreemapSVG) so a
 * cell reads identically whichever view the visitor has chosen. First line is
 * the title; the rest sit on a two-column value/label grid.
 */
export interface DietHoverState {
    lines: DietTooltipLine[];
    x: number;
    y: number;
}

interface DietHoverTooltipProps {
    hover: DietHoverState;
    /** Measured width of the chart container, used to keep the panel on-chart. */
    containerWidth: number;
}

const DietHoverTooltip: React.FC<DietHoverTooltipProps> = ({ hover, containerWidth }) => (
    <div
        className="pointer-events-none absolute z-10 rounded bg-[var(--bg-page)] px-2 py-1 text-xs shadow-md ring-1 ring-[var(--accent-faint)]"
        style={{
            left: Math.min(Math.max(hover.x + 10, 0), Math.max(containerWidth - 170, 0)),
            top: Math.max(hover.y - 44, 0),
        }}
    >
        <div className="pb-[3px] font-semibold text-[var(--text-strong)]">
            {typeof hover.lines[0] === 'string' ? hover.lines[0] : hover.lines[0].value}
        </div>
        <div className="grid grid-cols-[auto_1fr] gap-x-2">
            {hover.lines.slice(1).map((line, index) => (
                typeof line === 'string' ? (
                    <div key={index} className="col-span-2 text-[var(--text-muted)]">{line}</div>
                ) : (
                    <React.Fragment key={index}>
                        <span
                            className="text-right font-semibold tabular-nums text-[var(--text-strong)]"
                            style={line.color ? { color: line.color } : undefined}
                        >
                            {line.value}
                        </span>
                        <span className="text-[var(--text-muted)]">{line.label}</span>
                    </React.Fragment>
                )
            ))}
        </div>
    </div>
);

export default DietHoverTooltip;
