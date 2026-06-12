'use client';

import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { useTheme } from '../context/ThemeContext';
import { chartColors } from '../lib/chartTheme';

const SUB_ART = [
    '         |\\_',
    '   _____|~ |____',
    '  (  --         ~~~~--_,',
    "   ~~~~~~~~~~~~~~~~~~~'`",
];

const WIDTH = 900;
const HEIGHT = 300;
const FONT_SIZE = 18;
const LINE_H = 22;
const CROSS_MS = 12000;
// Fixed off-screen bounds (no getBBox). At ~0.6em/char monospace, the widest
// line (~23 chars) is ~250px; -360 → WIDTH+60 fully clears both edges.
const X_START = -360;
const X_END = WIDTH + 60;
const BLOCK_H = SUB_ART.length * LINE_H;
const Y_MID = (HEIGHT - BLOCK_H) / 2;

const SubmarineEasterEgg: React.FC = () => {
    const ref = useRef<HTMLDivElement | null>(null);
    const { theme } = useTheme();

    useEffect(() => {
        const host = ref.current;
        if (!host) return;
        const colors = chartColors[theme];

        d3.select(host).selectAll('*').remove();

        const svg = d3
            .select(host)
            .append('svg')
            .attr('viewBox', `0 0 ${WIDTH} ${HEIGHT}`)
            .attr('width', '100%')
            .attr('role', 'img')
            .attr('aria-label', 'There are no Tier 9 submarines — but here is one anyway.')
            .style('display', 'block')
            .style('max-width', `${WIDTH}px`)
            .style('background', 'transparent');

        const g = svg.append('g');
        const text = g
            .append('text')
            .attr('font-family', 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace')
            .attr('font-size', FONT_SIZE)
            .attr('fill', colors.shipSS)
            .style('white-space', 'pre');

        SUB_ART.forEach((line, i) => {
            text
                .append('tspan')
                .attr('x', 0)
                .attr('y', i * LINE_H + FONT_SIZE)
                .attr('xml:space', 'preserve')
                .text(line);
        });

        const reduce =
            typeof window !== 'undefined' &&
            window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        if (reduce) {
            // Static, roughly centered (no animation).
            g.attr('transform', `translate(${(WIDTH - 250) / 2}, ${Y_MID})`);
            return () => {
                d3.select(host).selectAll('*').remove();
            };
        }

        let stopped = false;
        const swim = () => {
            if (stopped) return;
            g.attr('transform', `translate(${X_START}, ${Y_MID})`)
                .transition()
                .duration(CROSS_MS)
                .ease(d3.easeLinear)
                .attrTween('transform', () => {
                    const ix = d3.interpolateNumber(X_START, X_END);
                    return (t: number) => {
                        const x = ix(t);
                        const bob = Math.sin(t * Math.PI * 4) * 8; // gentle vertical bob
                        return `translate(${x}, ${Y_MID + bob})`;
                    };
                })
                .on('end', swim);
        };
        swim();

        return () => {
            stopped = true;
            d3.select(host).selectAll('*').interrupt();
            d3.select(host).selectAll('*').remove();
        };
    }, [theme]);

    return <div ref={ref} className="w-full" style={{ maxWidth: WIDTH }} />;
};

export default SubmarineEasterEgg;
