import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { chartColors, type ChartTheme } from '../lib/chartTheme';

export interface EfficiencyBadgeDot {
    shipId: number;
    shipName: string;
    shipType: string;
    shipTier: number;
    badgeClass: number;
    badgeLabel: string;
}

interface EfficiencyStripPlotSVGProps {
    dots: EfficiencyBadgeDot[];
    theme?: ChartTheme;
    svgWidth?: number;
    // Target SVG height so the chart fills its locked panel. 0 = default height.
    minSvgHeight?: number;
}

type Colors = typeof chartColors['light'];

const SHIP_TYPE_ORDER = ['DD', 'CA', 'BB', 'CV', 'Sub'];

const ROMAN_TIERS = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI'];

const romanTier = (tier: number): string => ROMAN_TIERS[tier - 1] ?? String(tier);

export const badgeClassColor = (colors: Colors, badgeClass: number): string => {
    if (badgeClass === 1) return colors.badgeE;
    if (badgeClass === 2) return colors.badgeI;
    if (badgeClass === 3) return colors.badgeII;
    return colors.badgeIII;
};

const MIN_DOT_RADIUS = 7;
const MAX_DOT_RADIUS = 17;
const HIT_PAD = 4;
const DEFAULT_SVG_HEIGHT = 460;
const MARGIN = { top: 50, right: 16, bottom: 16, left: 16 };
const AXIS_FONT_SIZE = '13px';
const SUMMARY_FONT_SIZE = '14px';
// Live force layout, per the classic d3 force-directed-graph shape: ships of a
// type bond to their type hub (strong, short links) and hubs bond weakly to
// each other (long links); every node carries a weak many-body repulsion so
// clusters shoulder apart, and a gentle anchor pulls each type toward its own
// quadrant so dragged dots rubber-band home.
const INTRA_LINK_STRENGTH = 0.35;
const INTER_LINK_STRENGTH = 0.02;
const INTER_LINK_DISTANCE = 280;
// Strong enough that the type clusters actually separate into their quadrants
// before the simulation cools (at 0.05 they congealed into one central blob).
const QUADRANT_ANCHOR_STRENGTH = 0.2;
const CHARGE_STRENGTH = -40;
const COLLIDE_PAD = 2.5;
const DRAG_ALPHA_TARGET = 0.3;
// The center-spring start is bistable: a pure center launch can lock into a
// single collide-pressure blob instead of separating. Seeding each node a
// fraction of the way toward its quadrant breaks that symmetry decisively
// while still reading as "springs from the middle"; the slower cooling gives
// the anchors time to finish the separation.
const ANCHOR_SEED_BIAS = 0.2;
const ALPHA_DECAY = 0.015;

interface SimNode {
    dot: EfficiencyBadgeDot;
    anchorX: number;
    anchorY: number;
    x: number;
    y: number;
    vx?: number;
    vy?: number;
    fx?: number | null;
    fy?: number | null;
    index?: number;
}

interface SimLink {
    source: SimNode;
    target: SimNode;
    kind: 'intra' | 'inter';
}

// Minimal shape of the d3 force simulation we hold on to (the repo types d3
// as an untyped module; see d3.d.ts).
interface BadgeSimulation {
    stop: () => BadgeSimulation;
    restart: () => BadgeSimulation;
    alphaTarget: (value: number) => BadgeSimulation;
}

const compareDots = (left: EfficiencyBadgeDot, right: EfficiencyBadgeDot): number => {
    if (left.badgeClass !== right.badgeClass) {
        return left.badgeClass - right.badgeClass;
    }
    return left.shipName.localeCompare(right.shipName);
};

// Quadrant anchor fractions of the plot area, by number of type clusters.
const anchorLayout = (count: number): Array<[number, number]> => {
    if (count <= 1) return [[0.5, 0.5]];
    if (count === 2) return [[0.3, 0.5], [0.7, 0.5]];
    if (count === 3) return [[0.5, 0.27], [0.27, 0.73], [0.73, 0.73]];
    if (count === 4) return [[0.28, 0.27], [0.72, 0.27], [0.28, 0.73], [0.72, 0.73]];
    return [[0.28, 0.25], [0.72, 0.25], [0.28, 0.75], [0.72, 0.75], [0.5, 0.5]];
};

const drawChart = (
    containerElement: HTMLDivElement,
    dots: EfficiencyBadgeDot[],
    svgWidth: number,
    minSvgHeight: number,
    colors: Colors,
): BadgeSimulation | null => {
    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    if (!dots.length) {
        return null;
    }

    const svgHeight = Math.max(minSvgHeight, DEFAULT_SVG_HEIGHT);
    const plotWidth = svgWidth - MARGIN.left - MARGIN.right;
    const plotHeight = svgHeight - MARGIN.top - MARGIN.bottom;

    const presentTypes = new Set(dots.map((dot) => dot.shipType));
    const shipTypes = [
        ...SHIP_TYPE_ORDER.filter((shipType) => presentTypes.has(shipType)),
        ...[...presentTypes].filter((shipType) => !SHIP_TYPE_ORDER.includes(shipType)).sort(),
    ];
    const anchors = anchorLayout(shipTypes.length);
    const anchorFor = (shipType: string): [number, number] => {
        const index = Math.min(shipTypes.indexOf(shipType), anchors.length - 1);
        const [fx, fy] = anchors[Math.max(0, index)];
        return [fx * plotWidth, fy * plotHeight];
    };

    // Circle size encodes ship tier: Tier V reads smallest, Tier X/XI largest.
    const dataMinTier = d3.min(dots, (dot: EfficiencyBadgeDot) => dot.shipTier) ?? 5;
    const dataMaxTier = d3.max(dots, (dot: EfficiencyBadgeDot) => dot.shipTier) ?? 10;
    const tierSpan = Math.max(1, dataMaxTier - dataMinTier);
    const radiusForTier = (tier: number): number =>
        MIN_DOT_RADIUS + ((tier - dataMinTier) / tierSpan) * (MAX_DOT_RADIUS - MIN_DOT_RADIUS);
    const radiusFor = (node: SimNode): number => radiusForTier(node.dot.shipTier);

    // Every node springs from the plot center on load (tiny deterministic
    // spiral offsets keep the collision force from dividing by zero).
    const nodes: SimNode[] = [...dots].sort(compareDots).map((dot, index) => {
        const [anchorX, anchorY] = anchorFor(dot.shipType);
        const angle = index * 0.7;
        return {
            dot,
            anchorX,
            anchorY,
            x: plotWidth / 2 + (anchorX - plotWidth / 2) * ANCHOR_SEED_BIAS + Math.cos(angle) * (2 + index * 0.4),
            y: plotHeight / 2 + (anchorY - plotHeight / 2) * ANCHOR_SEED_BIAS + Math.sin(angle) * (2 + index * 0.4),
        };
    });

    // Bonds: each type's best badge is the hub; members bond strongly to their
    // hub, hubs bond weakly to the next type's hub.
    const hubs = new Map<string, SimNode>();
    nodes.forEach((node) => {
        if (!hubs.has(node.dot.shipType)) {
            hubs.set(node.dot.shipType, node);
        }
    });
    const links: SimLink[] = [];
    nodes.forEach((node) => {
        const hub = hubs.get(node.dot.shipType);
        if (hub && hub !== node) {
            links.push({ source: hub, target: node, kind: 'intra' });
        }
    });
    shipTypes.forEach((shipType, index) => {
        if (index === 0) return;
        const previousHub = hubs.get(shipTypes[index - 1]);
        const hub = hubs.get(shipType);
        if (previousHub && hub) {
            links.push({ source: previousHub, target: hub, kind: 'inter' });
        }
    });

    const svgRoot = container.append('svg')
        .attr('width', svgWidth)
        .attr('height', svgHeight);

    const svg = svgRoot.append('g')
        .attr('transform', `translate(${MARGIN.left}, ${MARGIN.top})`);

    // Type labels ride above their cluster — positions update every tick so a
    // label follows its blob wherever the simulation (or a drag) takes it.
    const typeLabels = new Map<string, ReturnType<typeof svg.append>>();
    shipTypes.forEach((shipType) => {
        typeLabels.set(shipType, svg.append('text')
            .attr('text-anchor', 'middle')
            .style('font-size', AXIS_FONT_SIZE)
            .style('font-weight', '600')
            .style('fill', colors.axisText)
            .text(shipType));
    });

    const renderTypeLabels = () => {
        shipTypes.forEach((shipType) => {
            const members = nodes.filter((node) => node.dot.shipType === shipType);
            if (!members.length) {
                return;
            }
            const centroidX = members.reduce((sum, node) => sum + node.x, 0) / members.length;
            const top = Math.min(...members.map((node) => node.y - radiusFor(node)));
            typeLabels.get(shipType)
                ?.attr('x', Math.max(16, Math.min(plotWidth - 16, centroidX)))
                .attr('y', Math.max(12, top - 12));
        });
    };

    const summaryGroup = svgRoot.append('g')
        .attr('transform', `translate(${MARGIN.left + 4}, 18)`);

    const renderSummary = (dot: EfficiencyBadgeDot) => {
        summaryGroup.selectAll('*').remove();

        const line = summaryGroup.append('text')
            .attr('x', 0)
            .attr('y', 0)
            .attr('dominant-baseline', 'middle')
            .style('font-size', SUMMARY_FONT_SIZE);

        line.append('tspan')
            .style('font-weight', '700')
            .style('fill', colors.labelStrong)
            .text(dot.shipName);

        line.append('tspan')
            .style('fill', colors.labelMuted)
            .text('  ·  ');

        line.append('tspan')
            .style('font-weight', '700')
            .style('fill', badgeClassColor(colors, dot.badgeClass))
            .text(`Badge ${dot.badgeLabel}`);

        line.append('tspan')
            .style('fill', colors.labelMid)
            .text(`  ·  ${dot.shipType}  ·  Tier ${romanTier(dot.shipTier)}`);
    };

    const linkNodes = svg.append('g')
        .selectAll('line')
        .data(links)
        .enter()
        .append('line')
        .attr('class', (link: SimLink) => `badge-link badge-link-${link.kind}`)
        .attr('stroke', colors.gridLine)
        .attr('stroke-opacity', (link: SimLink) => (link.kind === 'intra' ? 0.7 : 0.45))
        .attr('stroke-width', (link: SimLink) => (link.kind === 'intra' ? 1.2 : 1))
        .attr('stroke-dasharray', (link: SimLink) => (link.kind === 'inter' ? '4 4' : null));

    const dotStrokeWidth = 2;
    const dotNodes = svg.append('g')
        .selectAll('circle')
        .data(nodes)
        .enter()
        .append('circle')
        .attr('class', 'badge-dot')
        .attr('data-ship-type', (node: SimNode) => node.dot.shipType)
        .attr('r', (node: SimNode) => radiusFor(node))
        .attr('fill', (node: SimNode) => badgeClassColor(colors, node.dot.badgeClass))
        .attr('stroke', colors.barStroke)
        .attr('stroke-width', dotStrokeWidth);

    const hitNodes = svg.append('g')
        .selectAll('circle')
        .data(nodes)
        .enter()
        .append('circle')
        .attr('class', 'badge-dot-hit')
        .attr('r', (node: SimNode) => radiusFor(node) + HIT_PAD)
        .attr('fill', 'transparent')
        .style('cursor', 'grab');

    const clampNodes = () => {
        nodes.forEach((node) => {
            const radius = radiusFor(node);
            node.x = Math.max(radius + 1, Math.min(plotWidth - radius - 1, node.x));
            node.y = Math.max(radius + 1, Math.min(plotHeight - radius - 1, node.y));
        });
    };

    const renderPositions = () => {
        clampNodes();
        linkNodes
            .attr('x1', (link: SimLink) => link.source.x)
            .attr('y1', (link: SimLink) => link.source.y)
            .attr('x2', (link: SimLink) => link.target.x)
            .attr('y2', (link: SimLink) => link.target.y);
        dotNodes
            .attr('cx', (node: SimNode) => node.x)
            .attr('cy', (node: SimNode) => node.y);
        hitNodes
            .attr('cx', (node: SimNode) => node.x)
            .attr('cy', (node: SimNode) => node.y);
        renderTypeLabels();
    };

    const simulation = d3.forceSimulation(nodes)
        .force('link', d3.forceLink(links)
            .distance((link: SimLink) => (link.kind === 'intra'
                ? radiusFor(link.source) + radiusFor(link.target) + 8
                : INTER_LINK_DISTANCE))
            .strength((link: SimLink) => (link.kind === 'intra' ? INTRA_LINK_STRENGTH : INTER_LINK_STRENGTH)))
        .force('charge', d3.forceManyBody().strength(CHARGE_STRENGTH))
        .force('x', d3.forceX((node: SimNode) => node.anchorX).strength(QUADRANT_ANCHOR_STRENGTH))
        .force('y', d3.forceY((node: SimNode) => node.anchorY).strength(QUADRANT_ANCHOR_STRENGTH))
        .force('collide', d3.forceCollide((node: SimNode) => radiusFor(node) + COLLIDE_PAD).iterations(2))
        .alphaDecay(ALPHA_DECAY)
        .on('tick', renderPositions);

    // Drag with rubber-band: the node is pinned to the pointer while dragging;
    // on release the anchor/link forces pull it back toward its quadrant.
    const drag = d3.drag()
        .on('start', (event: { active: number }, node: SimNode) => {
            if (!event.active) simulation.alphaTarget(DRAG_ALPHA_TARGET).restart();
            node.fx = node.x;
            node.fy = node.y;
        })
        .on('drag', (event: { x: number; y: number }, node: SimNode) => {
            node.fx = event.x;
            node.fy = event.y;
        })
        .on('end', (event: { active: number }, node: SimNode) => {
            if (!event.active) simulation.alphaTarget(0);
            node.fx = null;
            node.fy = null;
        });
    hitNodes.call(drag);

    hitNodes
        .on('mouseover', function (_event: MouseEvent, node: SimNode) {
            const index = nodes.indexOf(node);
            if (index >= 0) {
                d3.select(dotNodes.nodes()[index])
                    .attr('stroke', colors.labelStrong)
                    .attr('stroke-width', dotStrokeWidth + 0.5);
            }
            renderSummary(node.dot);
        })
        .on('mouseout', function (_event: MouseEvent, node: SimNode) {
            const index = nodes.indexOf(node);
            if (index >= 0) {
                d3.select(dotNodes.nodes()[index])
                    .attr('stroke', colors.barStroke)
                    .attr('stroke-width', dotStrokeWidth);
            }
        });

    // Default the summary to the player's best badge (class asc, then name) so
    // the panel never starts blank.
    renderSummary(nodes[0].dot);
    renderPositions();

    return simulation;
};

const EfficiencyStripPlotSVG: React.FC<EfficiencyStripPlotSVGProps> = ({
    dots,
    theme = 'light',
    svgWidth = 570,
    minSvgHeight = 0,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const containerElement = containerRef.current;
        if (!containerElement) {
            return;
        }

        const colors = chartColors[theme];
        let simulation: BadgeSimulation | null = null;
        let resizeFrame: number | null = null;

        const resolveWidth = () => Math.max(containerElement.clientWidth || svgWidth, 320);

        const redraw = () => {
            simulation?.stop();
            simulation = drawChart(containerElement, dots, resolveWidth(), minSvgHeight, colors);
        };

        const onResize = () => {
            if (resizeFrame != null) cancelAnimationFrame(resizeFrame);
            resizeFrame = requestAnimationFrame(redraw);
        };

        redraw();
        window.addEventListener('resize', onResize);
        return () => {
            simulation?.stop();
            window.removeEventListener('resize', onResize);
            if (resizeFrame != null) cancelAnimationFrame(resizeFrame);
        };
    }, [dots, minSvgHeight, svgWidth, theme]);

    return <div ref={containerRef} className="w-full" />;
};

export default EfficiencyStripPlotSVG;
