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
// as monospace text in the same deep-water ink as the sub, riding inside the
// same translating <g> so it bobs and crosses in lockstep, one boat-length
// behind. Attribution ("jgs") is kept inline as the original artist's signature.
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
// Octopus group origin, in the same local frame as the sub. The sub's bow sits
// at x≈0 and its tail at x≈250; the octopus is parked just behind the tail
// (OCTO_X) and centered vertically about the sub (OCTO_Y, negative = up).
const OCTO_X = 230;
const OCTO_Y = -60;
const CROSS_MS = 12000;
// Fixed off-screen bounds (no getBBox). The art faces left (bow on the left),
// so the sub swims right → left with the octopus trailing to its right. Start
// past the right edge; exit far enough left that the octopus block (the
// rightmost content, ~OCTO_X + 400px wide) fully clears the viewBox.
const X_START = WIDTH + 60;
const X_END = -700;
const ASSEMBLY_W = 620; // sub + trailing octopus, used to center the static frame
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
            .attr(
                'aria-label',
                'There are no Tier 9 submarines — but here is one anyway, fleeing an octopus.',
            )
            .style('display', 'block')
            .style('max-width', `${WIDTH}px`)
            .style('background', 'transparent');

        const g = svg.append('g');

        // Octopus first → it renders behind the sub (the sub is escaping it).
        const octoG = g
            .append('g')
            .attr('aria-hidden', 'true')
            .attr('transform', `translate(${OCTO_X}, ${OCTO_Y})`);
        const octoText = octoG
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
            g.attr('transform', `translate(${(WIDTH - ASSEMBLY_W) / 2}, ${Y_MID})`);
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
