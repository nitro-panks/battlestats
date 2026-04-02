import React, { useEffect, useRef, useState } from 'react';
import * as d3 from 'd3';
import type { ClanMemberData } from './clanMembersShared';
import { buildClanChartMemberActivitySignature } from './clanChartActivity';
import { incrementChartFetches, decrementChartFetches } from '../lib/sharedJsonFetch';
import { chartColors, type ChartTheme } from '../lib/chartTheme';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';
import type { ClanMemberTier } from './useClanMemberTiers';

interface Clan3DProps {
    clanId: number;
    onSelectMember?: (memberName: string) => void;
    svgWidth?: number;
    svgHeight?: number;
    membersData?: ClanMemberData[];
    memberTiers: ClanMemberTier[];
    theme?: ChartTheme;
}

interface PlotData {
    player_name: string;
    pvp_battles: number;
    pvp_ratio: number;
}

interface Point3D {
    name: string;
    battles: number;
    wr: number;
    kdr: number;
    nx: number;
    ny: number;
    nz: number;
    color: string;
}

// ── Projection math ──────────────────────────────────────────────

function rotateY(p: { nx: number; ny: number; nz: number }, a: number) {
    const cos = Math.cos(a), sin = Math.sin(a);
    return { nx: p.nx * cos + p.nz * sin, ny: p.ny, nz: -p.nx * sin + p.nz * cos };
}

function rotateX(p: { nx: number; ny: number; nz: number }, a: number) {
    const cos = Math.cos(a), sin = Math.sin(a);
    return { nx: p.nx, ny: p.ny * cos - p.nz * sin, nz: p.ny * sin + p.nz * cos };
}

function project(p: { nx: number; ny: number; nz: number }, cx: number, cy: number, scale: number) {
    const focal = 4;
    const pScale = focal / (focal + p.nz);
    return {
        x: cx + scale * p.nx * pScale,
        y: cy - scale * p.ny * pScale,
        z: p.nz,
        s: pScale,
    };
}

// ── Color by WR ──────────────────────────────────────────────────

const wrColor = (wr: number, theme: ChartTheme) => {
    const c = chartColors[theme];
    if (wr > 65) return c.wrElite;
    if (wr >= 60) return c.wrSuperUnicum;
    if (wr >= 56) return c.wrUnicum;
    if (wr >= 54) return c.wrVeryGood;
    if (wr >= 52) return c.wrGood;
    if (wr >= 50) return c.wrAboveAvg;
    if (wr >= 45) return c.wrAverage;
    if (wr >= 40) return c.wrBelowAvg;
    return c.wrBad;
};

// ── Axis line helpers ────────────────────────────────────────────

interface AxisDef {
    from: { nx: number; ny: number; nz: number };
    to: { nx: number; ny: number; nz: number };
    label: string;
}

const AXES: AxisDef[] = [
    { from: { nx: -1, ny: -1, nz: -1 }, to: { nx: 1, ny: -1, nz: -1 }, label: 'Battles →' },
    { from: { nx: -1, ny: -1, nz: -1 }, to: { nx: -1, ny: 1, nz: -1 }, label: 'Win Rate →' },
    { from: { nx: -1, ny: -1, nz: -1 }, to: { nx: -1, ny: -1, nz: 1 }, label: 'KDR →' },
];

// ── Draw (updates existing SVG content) ─────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const renderFrame = (
    g: any,
    tooltipG: any,
    points: Point3D[],
    rotY: number,
    rotXVal: number,
    cx: number,
    cy: number,
    scale: number,
    theme: ChartTheme,
    onSelectMember?: (name: string) => void,
) => {
    const colors = chartColors[theme];
    g.selectAll('*').remove();
    tooltipG.selectAll('*').remove();

    // Draw axes
    for (const axis of AXES) {
        const fromR = rotateX(rotateY(axis.from, rotY), rotXVal);
        const toR = rotateX(rotateY(axis.to, rotY), rotXVal);
        const p1 = project(fromR, cx, cy, scale);
        const p2 = project(toR, cx, cy, scale);

        g.append('line')
            .attr('x1', p1.x).attr('y1', p1.y)
            .attr('x2', p2.x).attr('y2', p2.y)
            .attr('stroke', colors.gridLine || colors.labelText)
            .attr('stroke-opacity', 0.3)
            .attr('stroke-width', 1);

        g.append('text')
            .attr('x', p2.x + (p2.x - p1.x) * 0.06)
            .attr('y', p2.y + (p2.y - p1.y) * 0.06)
            .attr('text-anchor', 'middle')
            .attr('dominant-baseline', 'middle')
            .style('font-size', '10px')
            .style('fill', colors.labelText)
            .style('fill-opacity', 0.6)
            .text(axis.label);
    }

    // Draw grid lines on back planes
    const gridSteps = [-1, -0.5, 0, 0.5, 1];
    const gridColor = colors.gridLine || colors.labelText;

    // XY back-plane (z = -1)
    for (const s of gridSteps) {
        const h1 = project(rotateX(rotateY({ nx: -1, ny: s, nz: -1 }, rotY), rotXVal), cx, cy, scale);
        const h2 = project(rotateX(rotateY({ nx: 1, ny: s, nz: -1 }, rotY), rotXVal), cx, cy, scale);
        g.append('line').attr('x1', h1.x).attr('y1', h1.y).attr('x2', h2.x).attr('y2', h2.y)
            .attr('stroke', gridColor).attr('stroke-opacity', 0.08).attr('stroke-width', 0.5);
        const v1 = project(rotateX(rotateY({ nx: s, ny: -1, nz: -1 }, rotY), rotXVal), cx, cy, scale);
        const v2 = project(rotateX(rotateY({ nx: s, ny: 1, nz: -1 }, rotY), rotXVal), cx, cy, scale);
        g.append('line').attr('x1', v1.x).attr('y1', v1.y).attr('x2', v2.x).attr('y2', v2.y)
            .attr('stroke', gridColor).attr('stroke-opacity', 0.08).attr('stroke-width', 0.5);
    }

    // XZ floor-plane (y = -1)
    for (const s of gridSteps) {
        const h1 = project(rotateX(rotateY({ nx: -1, ny: -1, nz: s }, rotY), rotXVal), cx, cy, scale);
        const h2 = project(rotateX(rotateY({ nx: 1, ny: -1, nz: s }, rotY), rotXVal), cx, cy, scale);
        g.append('line').attr('x1', h1.x).attr('y1', h1.y).attr('x2', h2.x).attr('y2', h2.y)
            .attr('stroke', gridColor).attr('stroke-opacity', 0.08).attr('stroke-width', 0.5);
        const v1 = project(rotateX(rotateY({ nx: s, ny: -1, nz: -1 }, rotY), rotXVal), cx, cy, scale);
        const v2 = project(rotateX(rotateY({ nx: s, ny: -1, nz: 1 }, rotY), rotXVal), cx, cy, scale);
        g.append('line').attr('x1', v1.x).attr('y1', v1.y).attr('x2', v2.x).attr('y2', v2.y)
            .attr('stroke', gridColor).attr('stroke-opacity', 0.08).attr('stroke-width', 0.5);
    }

    // Project & depth-sort points
    const projected = points.map((p) => {
        const rotated = rotateX(rotateY(p, rotY), rotXVal);
        const proj = project(rotated, cx, cy, scale);
        return { ...p, px: proj.x, py: proj.y, pz: proj.z, ps: proj.s };
    }).sort((a, b) => a.pz - b.pz);

    // Draw dots
    for (const pt of projected) {
        const r = Math.max(3, 6 * pt.ps);
        const opacity = 0.4 + 0.5 * pt.ps;

        const circle = g.append('circle')
            .attr('cx', pt.px)
            .attr('cy', pt.py)
            .attr('r', r)
            .attr('fill', pt.color)
            .attr('fill-opacity', opacity)
            .attr('stroke', pt.color)
            .attr('stroke-width', 0.5)
            .attr('stroke-opacity', opacity * 0.8)
            .style('cursor', 'pointer');

        circle.on('mouseenter', () => {
            circle.attr('r', r * 1.5).attr('fill-opacity', 1).attr('stroke-opacity', 1);

            const tooltipBg = tooltipG.append('rect')
                .attr('class', 'tooltip-bg')
                .attr('rx', 4).attr('ry', 4)
                .attr('fill', theme === 'dark' ? 'rgba(20,20,30,0.92)' : 'rgba(255,255,255,0.95)')
                .attr('stroke', colors.labelText)
                .attr('stroke-opacity', 0.2);

            const lines = [
                pt.name,
                `${pt.battles.toLocaleString()} battles  ·  ${pt.wr.toFixed(1)}% WR`,
                `KDR ${pt.kdr.toFixed(2)}`,
            ];

            const texts = lines.map((line, i) =>
                tooltipG.append('text')
                    .attr('x', pt.px + r + 8)
                    .attr('y', pt.py - 8 + i * 14)
                    .style('font-size', i === 0 ? '11px' : '10px')
                    .style('font-weight', i === 0 ? '600' : '400')
                    .style('fill', colors.labelText)
                    .text(line)
            );

            const maxWidth = Math.max(...texts.map((t) => (t.node()?.getComputedTextLength() ?? 0)));
            tooltipBg
                .attr('x', pt.px + r + 4)
                .attr('y', pt.py - 22)
                .attr('width', maxWidth + 10)
                .attr('height', lines.length * 14 + 8);
        });

        circle.on('mouseleave', () => {
            circle.attr('r', r).attr('fill-opacity', opacity).attr('stroke-opacity', opacity * 0.8);
            tooltipG.selectAll('*').remove();
        });

        if (onSelectMember) {
            circle.on('click', () => {
                onSelectMember(pt.name);
            });
        }
    }
};

// ── Component ────────────────────────────────────────────────────

const FETCH_RETRY_DELAY = 350;
const FETCH_ATTEMPTS = 2;
const PENDING_RETRY_DELAY = 3000;
const PENDING_RETRY_LIMIT = 20;

const delayMs = (ms: number) => new Promise<void>((r) => { window.setTimeout(r, ms); });

const Clan3DSVG: React.FC<Clan3DProps> = ({
    clanId,
    onSelectMember,
    svgWidth = 900,
    svgHeight = 480,
    membersData,
    memberTiers,
    theme = 'light',
}) => {
    const { realm } = useRealm();
    const containerRef = useRef<HTMLDivElement>(null);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const svgRef = useRef<any>(null);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const gRef = useRef<any>(null);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const tooltipGRef = useRef<any>(null);
    const [plotData, setPlotData] = useState<PlotData[] | null>(null);
    const [plotError, setPlotError] = useState(false);
    const rotYRef = useRef(0.6);
    const rotXRef = useRef(0.3);
    const autoRotateRef = useRef(true);
    const animFrameRef = useRef<number | null>(null);
    const isDraggingRef = useRef(false);

    const memberActivitySig = buildClanChartMemberActivitySignature(membersData ?? []);

    // Fetch scatter data
    useEffect(() => {
        let cancelled = false;
        let chartSignalled = false;

        const fetchPlotData = async () => {
            chartSignalled = true;
            incrementChartFetches();

            for (let attempt = 0; attempt < FETCH_ATTEMPTS; attempt++) {
                try {
                    const response = await fetch(
                        withRealm(`/api/fetch/clan_data/${clanId}:active`, realm),
                    );
                    if (!response.ok) throw new Error(`${response.status}`);
                    const data = await response.json() as PlotData[];
                    const pending = response.headers.get('X-Clan-Plot-Pending') === 'true';

                    if (cancelled) return;
                    setPlotData(data);
                    setPlotError(false);

                    if (pending) {
                        for (let i = 0; i < PENDING_RETRY_LIMIT && !cancelled; i++) {
                            await delayMs(PENDING_RETRY_DELAY);
                            if (cancelled) return;
                            const retry = await fetch(withRealm(`/api/fetch/clan_data/${clanId}:active`, realm));
                            if (retry.ok) {
                                const retryData = await retry.json() as PlotData[];
                                if (!cancelled) setPlotData(retryData);
                                if (retry.headers.get('X-Clan-Plot-Pending') !== 'true') break;
                            }
                        }
                    }
                    return;
                } catch {
                    if (cancelled) return;
                    if (attempt < FETCH_ATTEMPTS - 1) await delayMs(FETCH_RETRY_DELAY);
                }
            }
            if (!cancelled) setPlotError(true);
        };

        void fetchPlotData().finally(() => {
            if (chartSignalled) {
                chartSignalled = false;
                decrementChartFetches();
            }
        });

        return () => {
            cancelled = true;
            if (chartSignalled) {
                chartSignalled = false;
                decrementChartFetches();
            }
        };
    }, [clanId, realm]);

    // Create persistent SVG once, attach drag handler
    useEffect(() => {
        if (!containerRef.current) return;
        const container = containerRef.current;

        // Clear any previous SVG
        d3.select(container).selectAll('*').remove();

        const svg = d3.select(container).append('svg')
            .attr('width', svgWidth)
            .attr('height', svgHeight)
            .style('cursor', 'grab')
            .style('user-select', 'none');

        svgRef.current = svg.node();
        gRef.current = svg.append('g');
        tooltipGRef.current = svg.append('g').style('pointer-events', 'none');

        // Drag handler — persists across renders
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const drag = (d3.drag as any)()
            .on('start', () => {
                isDraggingRef.current = true;
                autoRotateRef.current = false;
                svg.style('cursor', 'grabbing');
            })
            .on('drag', (event: { dx: number; dy: number }) => {
                rotYRef.current += event.dx * 0.008;
                rotXRef.current -= event.dy * 0.008;
                rotXRef.current = Math.max(-Math.PI / 2.5, Math.min(Math.PI / 2.5, rotXRef.current));
            })
            .on('end', () => {
                isDraggingRef.current = false;
                svg.style('cursor', 'grab');
            });

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        svg.call(drag as any);

        return () => {
            d3.select(container).selectAll('*').remove();
            svgRef.current = null;
            gRef.current = null;
            tooltipGRef.current = null;
        };
    }, [svgWidth, svgHeight]);

    // Build 3D points and run animation loop
    useEffect(() => {
        if (!gRef.current || !tooltipGRef.current || !plotData) return;

        const kdrMap = new Map<string, number>();
        for (const mt of memberTiers) {
            if (mt.kdr != null) {
                kdrMap.set(mt.name, mt.kdr);
            }
        }

        // Compute median KDR for fallback
        const validKdrs = memberTiers.filter((m) => m.kdr != null).map((m) => m.kdr!);
        const medianKdr = validKdrs.length > 0
            ? validKdrs.sort((a, b) => a - b)[Math.floor(validKdrs.length / 2)]
            : 1.0;

        const maxBattles = Math.max(...plotData.map((d) => d.pvp_battles), 1);
        const wrValues = plotData.map((d) => d.pvp_ratio);
        const minWr = Math.min(...wrValues) - 2;
        const maxWr = Math.max(...wrValues) + 2;

        // KDR range for normalisation — clamp outliers at 3.0
        const kdrValues = plotData.map((d) => Math.min(kdrMap.get(d.player_name) ?? medianKdr, 3.0));
        const minKdr = Math.min(...kdrValues);
        const maxKdr = Math.max(...kdrValues);
        const kdrRange = maxKdr - minKdr || 1;

        const points: Point3D[] = plotData.map((d) => {
            const rawKdr = kdrMap.get(d.player_name) ?? medianKdr;
            const kdr = Math.min(rawKdr, 3.0);
            return {
                name: d.player_name,
                battles: d.pvp_battles,
                wr: d.pvp_ratio,
                kdr: rawKdr,
                nx: (d.pvp_battles / maxBattles) * 2 - 1,
                ny: ((d.pvp_ratio - minWr) / (maxWr - minWr)) * 2 - 1,
                nz: ((kdr - minKdr) / kdrRange) * 2 - 1,
                color: wrColor(d.pvp_ratio, theme),
            };
        });

        // Cancel any existing animation
        if (animFrameRef.current != null) {
            cancelAnimationFrame(animFrameRef.current);
            animFrameRef.current = null;
        }

        const margin = { top: 24, right: 20, bottom: 30, left: 20 };
        const plotW = svgWidth - margin.left - margin.right;
        const plotH = svgHeight - margin.top - margin.bottom;
        const cx = margin.left + plotW / 2;
        const cy = margin.top + plotH / 2;
        const scale = Math.min(plotW, plotH) * 0.38;

        const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        const g = gRef.current;
        const tooltipG = tooltipGRef.current;

        const animate = () => {
            if (autoRotateRef.current && !prefersReducedMotion) {
                rotYRef.current += 0.003;
            }
            renderFrame(g, tooltipG, points, rotYRef.current, rotXRef.current, cx, cy, scale, theme, onSelectMember);
            animFrameRef.current = requestAnimationFrame(animate);
        };

        animate();

        return () => {
            if (animFrameRef.current != null) {
                cancelAnimationFrame(animFrameRef.current);
                animFrameRef.current = null;
            }
        };
    }, [plotData, memberTiers, memberActivitySig, svgWidth, svgHeight, theme, onSelectMember]);

    return (
        <div>
            <div
                ref={containerRef}
                style={{ width: svgWidth, maxWidth: '100%', minHeight: svgHeight, touchAction: 'none' }}
            >
                {plotError && (
                    <div className="text-sm text-[var(--text-secondary)]">
                        Unable to load clan chart data.
                    </div>
                )}
                {!plotData && !plotError && (
                    <div className="text-sm text-[var(--text-secondary)]">
                        Loading 3D clan chart...
                    </div>
                )}
            </div>
            {plotData && (
                <button
                    type="button"
                    onClick={() => {
                        rotYRef.current = 0.6;
                        rotXRef.current = 0.3;
                        autoRotateRef.current = true;
                    }}
                    className="mt-1 rounded border border-[var(--border)] px-2 py-0.5 text-xs text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
                >
                    Reset view
                </button>
            )}
        </div>
    );
};

export default Clan3DSVG;
