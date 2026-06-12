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
// line (~23 chars) is ~250px. The art faces left (bow on the left), so it swims
// right → left: start off the right edge, exit past the left.
const X_START = WIDTH + 60;
const X_END = -360;
const BLOCK_H = SUB_ART.length * LINE_H;
const Y_MID = (HEIGHT - BLOCK_H) / 2;

// --- Kraken: chases the sub from behind (the sub swims right → left, so the
// kraken trails to its right) and rises from the deep, mostly below the
// viewBox. Tendrils reach up at ~60° toward the fleeing tail. All coordinates
// are group-local — the kraken rides inside the same translating <g> as the
// sub, so it bobs and crosses in lockstep, always one boat-length behind.
const KRAKEN_CX = 430; // mantle center x (right of the sub block ≈ behind it)
const KRAKEN_CY = 210; // mantle center y (mostly past the bottom edge)
const MANTLE_RX = 60;
const MANTLE_RY = 52;
const TENTACLE_LEN = 150;
const TENTACLE_W = 7;
const TENTACLE_BASE_Y = KRAKEN_CY - MANTLE_RY + 5; // tendrils sprout from the crown
// Each tendril: x-offset of its root from the mantle center, the elevation
// angle (math degrees, 90° = straight up), and a lateral curl for an organic
// S. Roots run left→right while angles rise 150°→92°, so the left tendrils
// lean hardest after the sub and the right ones stand vertical — mean ≈ 60°.
const TENTACLES = [
    { dx: -42, deg: 138, curl: 14 },
    { dx: -20, deg: 129, curl: -10 },
    { dx: 2, deg: 120, curl: 12 },
    { dx: 24, deg: 111, curl: -8 },
    { dx: 44, deg: 102, curl: 10 },
];

// A tapered, gently curved tendril: wide at the root (bx,by), pointed at the
// tip. svg y grows downward, so "up" is -sin.
const tendrilPath = (
    bx: number,
    by: number,
    deg: number,
    len: number,
    w: number,
    curl: number,
): string => {
    const rad = (deg * Math.PI) / 180;
    const dx = Math.cos(rad);
    const dy = -Math.sin(rad);
    const px = -dy; // unit perpendicular
    const py = dx;
    const tipx = bx + dx * len;
    const tipy = by + dy * len;
    const mx = bx + dx * len * 0.55;
    const my = by + dy * len * 0.55;
    const c1x = mx + px * (w * 0.6 + curl);
    const c1y = my + py * (w * 0.6 + curl);
    const c2x = mx - px * (w * 0.6 - curl);
    const c2y = my - py * (w * 0.6 - curl);
    return `M ${bx + px * w} ${by + py * w} Q ${c1x} ${c1y} ${tipx} ${tipy} Q ${c2x} ${c2y} ${bx - px * w} ${by - py * w} Z`;
};

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
            .attr(
                'aria-label',
                'There are no Tier 9 submarines — but here is one anyway, fleeing a kraken.',
            )
            .style('display', 'block')
            .style('max-width', `${WIDTH}px`)
            .style('background', 'transparent');

        const g = svg.append('g');

        // Kraken first → it renders behind the sub (the sub is escaping it).
        const kraken = g.append('g').attr('aria-hidden', 'true');
        const krakenInk = colors.shipSS; // share the sub's deep-water ink
        TENTACLES.forEach((t) => {
            kraken
                .append('path')
                .attr(
                    'd',
                    tendrilPath(KRAKEN_CX + t.dx, TENTACLE_BASE_Y, t.deg, TENTACLE_LEN, TENTACLE_W, t.curl),
                )
                .attr('fill', krakenInk)
                .attr('opacity', 0.9);
        });
        kraken
            .append('ellipse')
            .attr('cx', KRAKEN_CX)
            .attr('cy', KRAKEN_CY)
            .attr('rx', MANTLE_RX)
            .attr('ry', MANTLE_RY)
            .attr('fill', krakenInk);
        [-20, 20].forEach((ex) => {
            kraken
                .append('ellipse')
                .attr('cx', KRAKEN_CX + ex)
                .attr('cy', KRAKEN_CY - 40)
                .attr('rx', 8)
                .attr('ry', 10)
                .attr('fill', '#ffd166'); // glowing eyes from the dark
            kraken
                .append('circle')
                .attr('cx', KRAKEN_CX + ex)
                .attr('cy', KRAKEN_CY - 37)
                .attr('r', 3.2)
                .attr('fill', '#111');
        });

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
