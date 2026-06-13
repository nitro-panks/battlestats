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

// The pursuer: an ASCII octopus (jgs) reaching after the fleeing sub. Rendered
// as monospace text in the same deep-water ink as the sub. It lives on its own
// layer *under* the sub and drifts independently — a sinusoidal ease-in-out
// glide across the screen along a sine bobbing path, decoupled from the sub's
// motion. Attribution ("jgs") is kept inline as the original artist's signature.
const OCTO_ART = [
    '                      ___',
    "                   .-'   `'.",
    '                  /         \\',
    '                  |         ;',
    '                  |         |           ___.--,',
    "         _.._     |0) ~ (0) |    _.---'`__.-( (_.",
    "  __.--'`_.. '.__.\\    '--. \\_.-' ,.--'`     `\"\"`",
    " ( ,.--'`   ',__ /./;   ;, '.__.'`    __",
    " _`) )  .---.__.' / |   |\\   \\__..--\"\"  \"\"\"--.,_",
    "`---' .'.''-._.-'`_./  /\\ '.  \\ _.-~~~````~~~-._`-.__.'",
    "      | |  .' _.-' |  |  \\  \\  '.               `~---`",
    "       \\ \\/ .'     \\  \\   '. '-._)",
    "        \\/ /        \\  \\    `=.__`~-.",
    "   jgs  / /\\         `) )    / / `\"\".`\\",
    "  , _.-'.'\\ \\        / /    ( (     / /",
    "   `--~`   ) )    .-'.'      '.'.  | (",
    "          (/`    ( (`          ) )  '-;",
    "           `      '-;         (-'",
];

const WIDTH = 900;
const HEIGHT = 300;
const FONT_SIZE = 18;
const LINE_H = 22;
// The octopus is much taller than the 4-line sub, so it gets its own (smaller)
// type scale. Tight line height keeps the ASCII art's proportions terminal-like.
const OCTO_FONT = 12;
const OCTO_LINE_H = 12;
const CROSS_MS = 12000;
// Fixed off-screen bounds (no getBBox). The art faces left (bow on the left),
// so the sub swims right → left. Start past the right edge; exit far enough
// left that the widest content clears the viewBox.
const X_START = WIDTH + 60;
const X_END = -700;
const BLOCK_H = SUB_ART.length * LINE_H;
const Y_MID = (HEIGHT - BLOCK_H) / 2;

// --- Octopus layer (independent of the sub) ---------------------------------
// Its vertical center sits a little above the sub's mid-line (negative = up).
const OCTO_Y = -60;
const OCTO_BASE_Y = Y_MID + OCTO_Y;
// Octopus block is ~400px wide, so exit well past the left edge to fully clear.
const OCTO_X_START = WIDTH + 60;
const OCTO_X_END = -440;
// Slightly slower than the sub so it perpetually trails — still pursuing.
const OCTO_CROSS_MS = 13000;
const OCTO_BOB = 16; // amplitude of the vertical bobbing path, px
// Horizontal offset behind the sub used only by the static (reduced-motion) frame.
const OCTO_X = 230;
const ASSEMBLY_W = 620; // sub + trailing octopus, used to center the static frame

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
                'There are no Tier 9 submarines — but here is one anyway, fleeing an octopus.',
            )
            .style('display', 'block')
            .style('max-width', `${WIDTH}px`)
            .style('background', 'transparent');

        // Two independent layers. The octopus layer is appended first so it
        // renders *under* the sub layer (the sub is escaping it).
        const octoLayer = svg.append('g').attr('aria-hidden', 'true');
        const subLayer = svg.append('g');

        const octoText = octoLayer
            .append('text')
            .attr('font-family', 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace')
            .attr('font-size', OCTO_FONT)
            .attr('fill', colors.shipSS)
            .attr('opacity', 0.92)
            .style('white-space', 'pre');
        OCTO_ART.forEach((line, i) => {
            octoText
                .append('tspan')
                .attr('x', 0)
                .attr('y', i * OCTO_LINE_H + OCTO_FONT)
                .attr('xml:space', 'preserve')
                .text(line);
        });

        const subText = subLayer
            .append('text')
            .attr('font-family', 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace')
            .attr('font-size', FONT_SIZE)
            .attr('fill', colors.shipSS)
            .style('white-space', 'pre');

        SUB_ART.forEach((line, i) => {
            subText
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
            const subX = (WIDTH - ASSEMBLY_W) / 2;
            subLayer.attr('transform', `translate(${subX}, ${Y_MID})`);
            octoLayer.attr('transform', `translate(${subX + OCTO_X}, ${OCTO_BASE_Y})`);
            return () => {
                d3.select(host).selectAll('*').remove();
            };
        }

        let stopped = false;

        // Sub: unchanged — linear right→left crossing with a gentle bob.
        const swim = () => {
            if (stopped) return;
            subLayer
                .attr('transform', `translate(${X_START}, ${Y_MID})`)
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

        // Octopus: independent layer. Horizontal travel eased with a sinusoidal
        // ease-in-out (slow at the edges, quick through the middle); vertical
        // follows a steady sine bobbing path keyed off raw progress.
        const drift = () => {
            if (stopped) return;
            octoLayer
                .attr('transform', `translate(${OCTO_X_START}, ${OCTO_BASE_Y})`)
                .transition()
                .duration(OCTO_CROSS_MS)
                .ease(d3.easeLinear)
                .attrTween('transform', () => {
                    const ix = d3.interpolateNumber(OCTO_X_START, OCTO_X_END);
                    return (t: number) => {
                        const x = ix(d3.easeSinInOut(t));
                        const bob = Math.sin(t * Math.PI * 3) * OCTO_BOB;
                        return `translate(${x}, ${OCTO_BASE_Y + bob})`;
                    };
                })
                .on('end', drift);
        };

        swim();
        drift();

        return () => {
            stopped = true;
            d3.select(host).selectAll('*').interrupt();
            d3.select(host).selectAll('*').remove();
        };
    }, [theme]);

    return <div ref={ref} className="w-full" style={{ maxWidth: WIDTH }} />;
};

export default SubmarineEasterEgg;
