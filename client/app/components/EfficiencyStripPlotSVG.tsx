import React, { useEffect, useMemo, useRef } from 'react';
import * as d3 from 'd3';
import { chartColors, resolveContainerChartWidth, type ChartColors as Colors, type ChartTheme } from '../lib/chartTheme';

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


const SHIP_TYPE_ORDER = ['DD', 'CA', 'BB', 'CV', 'Sub'];

const ROMAN_TIERS = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI'];

const romanTier = (tier: number): string => ROMAN_TIERS[tier - 1] ?? String(tier);

export const badgeClassColor = (colors: Colors, badgeClass: number): string => {
    if (badgeClass === 1) return colors.badgeE;
    if (badgeClass === 2) return colors.badgeI;
    if (badgeClass === 3) return colors.badgeII;
    return colors.badgeIII;
};

const MIN_DOT_RADIUS = 10;
const MAX_DOT_RADIUS = 24;
const HIT_PAD = 4;
const DEFAULT_SVG_HEIGHT = 460;
const MARGIN = { top: 50, right: 16, bottom: 16, left: 16 };
const AXIS_FONT_SIZE = '13px';
const SUMMARY_FONT_SIZE = '22px';
// Live force layout, per the classic d3 force-directed-graph shape: ships of a
// type bond to their type hub (strong, short links) and hubs bond weakly to
// each other (long links); every node carries a weak many-body repulsion so
// clusters shoulder apart, and a gentle anchor pulls each type toward its own
// quadrant so dragged dots rubber-band home.
const INTRA_LINK_STRENGTH = 0.35;
const INTER_LINK_STRENGTH = 0.02;
const INTER_LINK_DISTANCE = 150;
// Strong enough that the type clusters actually separate into their quadrants
// before the simulation cools (at 0.05 they congealed into one central blob).
const QUADRANT_ANCHOR_STRENGTH = 0.25;
const CHARGE_STRENGTH = -40;
const COLLIDE_PAD = 2.5;
const DRAG_ALPHA_TARGET = 0.3;
// Hover gravity: circles that share a hover effect with the hovered circle
// (its tier, its medal) are slowly pulled toward it — twice as hard when they
// share both — shouldering unrelated circles aside via the collision force.
// The hovered circle itself is pinned. Releasing the hover lets the quadrant
// anchors carry everyone home. The pull keeps tracking the circle while it
// is dragged around.
const HOVER_PULL_STRENGTH = 0.08;
const HOVER_ALPHA_TARGET = 0.12;
// Fills are translucent at rest; the hovered medal group goes fully opaque.
const REST_FILL_OPACITY = 0.35;
// Hover accent for the type cohort: borders of every same-type circle and
// the cluster's type label turn this cyan while the hover holds.
const HOVER_TYPE_ACCENT = '#06b6d4';
// The center-spring start is bistable: a pure center launch can lock into a
// single collide-pressure blob instead of separating. Seeding each node a
// fraction of the way toward its quadrant breaks that symmetry decisively
// while still reading as "springs from the middle". Cooling is fast and
// friction high so the load animation is one smooth exhale — settled in a
// couple of seconds, no lingering jostle.
const ANCHOR_SEED_BIAS = 0.3;
const ALPHA_DECAY = 0.05;
const VELOCITY_DECAY = 0.55;
// While a circle is being dragged or hovered the whole field runs at higher
// friction, so displaced circles get shouldered aside and stop instead of
// sloshing around, and the hover gravity's inward drift stays a crawl.
// Restored to the rest value when the interaction ends.
const DRAG_VELOCITY_DECAY = 0.8;
const HOVER_VELOCITY_DECAY = 0.75;

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

// Visual-only connection webs: every pair of circles sharing a ship type, a
// tier, or an award class is joined by a faint line. They carry no force —
// the layout stays type-clustered — but the three overlapping meshes (class
// edges tinted in their badge color) give the graph its sense of depth.
type MeshKind = 'type' | 'tier' | 'class';

interface MeshLink {
    source: SimNode;
    target: SimNode;
    kind: MeshKind;
    badgeClass: number;
}

const MESH_OPACITY: Record<MeshKind, number> = { type: 0.1, tier: 0.07, class: 0.16 };
const MESH_WIDTH: Record<MeshKind, number> = { type: 0.7, tier: 0.6, class: 0.8 };

const buildMeshLinks = (nodes: SimNode[], kind: MeshKind, keyOf: (node: SimNode) => string | number): MeshLink[] => {
    const groups = new Map<string | number, SimNode[]>();
    nodes.forEach((node) => {
        const key = keyOf(node);
        const group = groups.get(key);
        if (group) {
            group.push(node);
        } else {
            groups.set(key, [node]);
        }
    });

    const links: MeshLink[] = [];
    groups.forEach((members) => {
        for (let i = 0; i < members.length; i += 1) {
            for (let j = i + 1; j < members.length; j += 1) {
                links.push({ source: members[i], target: members[j], kind, badgeClass: members[i].dot.badgeClass });
            }
        }
    });
    return links;
};

// Minimal shape of the d3 force simulation we hold on to (the repo types d3
// as an untyped module; see d3.d.ts).
interface BadgeSimulation {
    stop: () => BadgeSimulation;
    restart: () => BadgeSimulation;
    alphaTarget: (value: number) => BadgeSimulation;
    velocityDecay: (value: number) => BadgeSimulation;
}

const compareDots = (left: EfficiencyBadgeDot, right: EfficiencyBadgeDot): number => {
    if (left.badgeClass !== right.badgeClass) {
        return left.badgeClass - right.badgeClass;
    }
    return left.shipName.localeCompare(right.shipName);
};

// How many hover effects two circles share: same tier (throb) and/or same
// medal (ring). Doubles the hover gravity when both apply.
const sharedHoverEffects = (a: SimNode, b: SimNode): number =>
    (a.dot.shipTier === b.dot.shipTier ? 1 : 0) + (a.dot.badgeClass === b.dot.badgeClass ? 1 : 0);

// Quadrant anchor fractions of the plot area, by number of type clusters —
// pulled toward the center so the cluster constellation reads as one system.
const anchorLayout = (count: number): Array<[number, number]> => {
    if (count <= 1) return [[0.5, 0.5]];
    if (count === 2) return [[0.4, 0.5], [0.6, 0.5]];
    if (count === 3) return [[0.5, 0.38], [0.4, 0.62], [0.6, 0.62]];
    if (count === 4) return [[0.4, 0.38], [0.6, 0.38], [0.4, 0.62], [0.6, 0.62]];
    return [[0.4, 0.36], [0.6, 0.36], [0.4, 0.66], [0.6, 0.66], [0.5, 0.5]];
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
    // They are re-raised over the dots after the dot layers mount, and wear a
    // chart-background halo, so they stay readable when the big circles press
    // together into one mass.
    const typeLabelLayer = svg.append('g')
        .style('pointer-events', 'none');
    const typeLabels = new Map<string, ReturnType<typeof svg.append>>();
    shipTypes.forEach((shipType) => {
        typeLabels.set(shipType, typeLabelLayer.append('text')
            .attr('text-anchor', 'middle')
            .attr('stroke', colors.chartBg)
            .attr('stroke-width', 3.5)
            .attr('paint-order', 'stroke')
            .style('font-size', AXIS_FONT_SIZE)
            .style('font-weight', '700')
            .style('fill', colors.labelStrong)
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

    // Left-aligned with the "Efficiency Badges" header text above (the shared
    // 15px tab-header inset — the SVG spans the uninset panel, so x=15 here
    // lands on the header's x), not the plot margin.
    const summaryGroup = svgRoot.append('g')
        .attr('transform', 'translate(15, 18)');

    const renderSummary = (dot: EfficiencyBadgeDot) => {
        summaryGroup.selectAll('*').remove();

        const summaryText = summaryGroup.append('text')
            .attr('x', 0)
            .attr('y', 0)
            .attr('dominant-baseline', 'middle')
            .style('font-size', SUMMARY_FONT_SIZE)
            .style('font-weight', '700')
            .style('fill', colors.labelStrong);

        summaryText.append('tspan').text(dot.shipName);
        summaryText.append('tspan')
            .style('fill', colors.labelMuted)
            .text(' · ');
        // The award rank wears its medal color, mirroring the legend below.
        summaryText.append('tspan')
            .style('fill', badgeClassColor(colors, dot.badgeClass))
            .text(dot.badgeLabel);
    };

    // The three connection webs, layered type → tier → class beneath the dots.
    // The hub-spoke/inter-hub force links are physics-only and not drawn.
    const meshLinks: MeshLink[] = [
        ...buildMeshLinks(nodes, 'type', (node) => node.dot.shipType),
        ...buildMeshLinks(nodes, 'tier', (node) => node.dot.shipTier),
        ...buildMeshLinks(nodes, 'class', (node) => node.dot.badgeClass),
    ];
    const meshNodes = svg.append('g')
        .selectAll('line')
        .data(meshLinks)
        .enter()
        .append('line')
        .attr('class', (link: MeshLink) => `badge-mesh badge-mesh-${link.kind}`)
        .attr('stroke', (link: MeshLink) => (link.kind === 'class'
            ? badgeClassColor(colors, link.badgeClass)
            : link.kind === 'tier' ? colors.accentMid : colors.labelMuted))
        .attr('stroke-opacity', (link: MeshLink) => MESH_OPACITY[link.kind])
        .attr('stroke-width', (link: MeshLink) => MESH_WIDTH[link.kind]);

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
        .attr('stroke-width', dotStrokeWidth)
        .style('fill-opacity', REST_FILL_OPACITY)
        .style('transition', 'fill-opacity 250ms ease, stroke 250ms ease');

    // Hover rings: a 3px inner border in the dot's own medal color, faded in
    // for every circle sharing the hovered dot's badge level while its fill
    // fades out. Pre-built and position-synced so hover only toggles opacity.
    const ringNodes = svg.append('g')
        .selectAll('circle')
        .data(nodes)
        .enter()
        .append('circle')
        .attr('class', 'badge-dot-ring')
        .attr('r', (node: SimNode) => Math.max(2, radiusFor(node) - 2))
        .attr('fill', 'none')
        .attr('stroke', (node: SimNode) => badgeClassColor(colors, node.dot.badgeClass))
        .attr('stroke-width', 3)
        .style('opacity', 0)
        .style('pointer-events', 'none')
        .style('transition', 'opacity 250ms ease');

    // Tier labels: each circle carries its tier's roman numeral, hidden at
    // rest and faded in for every circle sharing the hovered dot's tier.
    // Pre-built and position-synced so hover only toggles opacity.
    const tierLabelNodes = svg.append('g')
        .selectAll('text')
        .data(nodes)
        .enter()
        .append('text')
        .attr('class', 'badge-dot-tier-label')
        .attr('text-anchor', 'middle')
        .attr('dominant-baseline', 'central')
        .attr('stroke', colors.chartBg)
        .attr('stroke-width', 3)
        .attr('paint-order', 'stroke')
        .style('font-size', (node: SimNode) => `${Math.max(11, Math.round(radiusFor(node) * 0.62))}px`)
        .style('font-weight', '700')
        .style('fill', colors.labelStrong)
        .style('opacity', 0)
        .style('pointer-events', 'none')
        .style('transition', 'opacity 250ms ease')
        .text((node: SimNode) => romanTier(node.dot.shipTier));

    const hitNodes = svg.append('g')
        .selectAll('circle')
        .data(nodes)
        .enter()
        .append('circle')
        .attr('class', 'badge-dot-hit')
        .attr('r', (node: SimNode) => radiusFor(node) + HIT_PAD)
        .attr('fill', 'transparent')
        .style('cursor', 'grab');

    // Labels read over the dot mass (they ignore pointer events, so hovers
    // and drags pass through to the hit circles beneath).
    typeLabelLayer.raise();

    const clampNodes = () => {
        nodes.forEach((node) => {
            const radius = radiusFor(node);
            node.x = Math.max(radius + 1, Math.min(plotWidth - radius - 1, node.x));
            node.y = Math.max(radius + 1, Math.min(plotHeight - radius - 1, node.y));
        });
    };

    const renderPositions = () => {
        clampNodes();
        meshNodes
            .attr('x1', (link: MeshLink) => link.source.x)
            .attr('y1', (link: MeshLink) => link.source.y)
            .attr('x2', (link: MeshLink) => link.target.x)
            .attr('y2', (link: MeshLink) => link.target.y);
        dotNodes
            .attr('cx', (node: SimNode) => node.x)
            .attr('cy', (node: SimNode) => node.y);
        ringNodes
            .attr('cx', (node: SimNode) => node.x)
            .attr('cy', (node: SimNode) => node.y);
        tierLabelNodes
            .attr('x', (node: SimNode) => node.x)
            .attr('y', (node: SimNode) => node.y);
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
        .velocityDecay(VELOCITY_DECAY)
        .on('tick', renderPositions);

    // Hover gravity: while a circle is hovered (or dragged), every circle
    // sharing one of its hover effects drifts toward its live position —
    // twice the pull when both effects are shared. Collision does the gentle
    // shoving of unrelated circles along the way.
    let activeNode: SimNode | null = null;
    let draggingNode: SimNode | null = null;
    // One place decides the field's friction: drag > hover > rest.
    const syncVelocityDecay = () => {
        simulation.velocityDecay(draggingNode
            ? DRAG_VELOCITY_DECAY
            : activeNode ? HOVER_VELOCITY_DECAY : VELOCITY_DECAY);
    };
    simulation.force('hover-pull', (alpha: number) => {
        const target = activeNode;
        if (!target) {
            return;
        }
        nodes.forEach((node) => {
            if (node === target) {
                return;
            }
            const shares = sharedHoverEffects(target, node);
            if (!shares) {
                return;
            }
            const pull = HOVER_PULL_STRENGTH * shares * alpha;
            node.vx = (node.vx ?? 0) + (target.x - node.x) * pull;
            node.vy = (node.vy ?? 0) + (target.y - node.y) * pull;
        });
    });

    // Drag with rubber-band: the node is pinned to the pointer while dragging;
    // on release the anchor/link forces pull it back toward its quadrant. The
    // hover gravity stays engaged for the dragged node.
    const drag = d3.drag()
        .on('start', (event: { active: number }, node: SimNode) => {
            if (!event.active) simulation.alphaTarget(DRAG_ALPHA_TARGET).restart();
            draggingNode = node;
            activeNode = node;
            syncVelocityDecay();
            node.fx = node.x;
            node.fy = node.y;
        })
        .on('drag', (event: { x: number; y: number }, node: SimNode) => {
            node.fx = event.x;
            node.fy = event.y;
        })
        .on('end', (event: { active: number }, node: SimNode) => {
            if (!event.active) simulation.alphaTarget(activeNode ? HOVER_ALPHA_TARGET : 0);
            draggingNode = null;
            syncVelocityDecay();
            node.fx = null;
            node.fy = null;
        });
    hitNodes.call(drag);

    // Hover semantics: every dot sharing the hovered dot's TYPE turns its
    // border cyan (as does the cluster's type label); every dot sharing its
    // TIER shows its tier's roman numeral; every dot sharing its MEDAL goes
    // from the translucent rest fill to fully opaque and fades in the 3px
    // inner ring in its medal color, while every other medal class empties
    // its fill entirely (stroke-only) until the hover ends.
    const clearHoverHighlights = () => {
        dotNodes
            .attr('stroke', colors.barStroke)
            .style('fill-opacity', REST_FILL_OPACITY);
        ringNodes.style('opacity', 0);
        tierLabelNodes.style('opacity', 0);
        typeLabels.forEach((label) => label.style('fill', colors.labelStrong));
        // The ship-name summary only exists while a dot is hovered.
        summaryGroup.selectAll('*').remove();
    };

    hitNodes
        .on('mouseover', function (_event: MouseEvent, node: SimNode) {
            clearHoverHighlights();
            dotNodes
                .filter((other: SimNode) => other.dot.shipType === node.dot.shipType)
                .attr('stroke', HOVER_TYPE_ACCENT);
            typeLabels.get(node.dot.shipType)?.style('fill', HOVER_TYPE_ACCENT);
            tierLabelNodes
                .filter((other: SimNode) => other.dot.shipTier === node.dot.shipTier)
                .style('opacity', 1);
            dotNodes
                .filter((other: SimNode) => other.dot.badgeClass === node.dot.badgeClass)
                .style('fill-opacity', 1);
            dotNodes
                .filter((other: SimNode) => other.dot.badgeClass !== node.dot.badgeClass)
                .style('fill-opacity', 0);
            ringNodes
                .filter((other: SimNode) => other.dot.badgeClass === node.dot.badgeClass)
                .style('opacity', 1);
            renderSummary(node.dot);

            // Engage the hover gravity: pin the hovered circle where it sits
            // and let its tier/medal mates start their slow pull toward it.
            activeNode = node;
            if (draggingNode !== node) {
                node.fx = node.x;
                node.fy = node.y;
            }
            syncVelocityDecay();
            simulation.alphaTarget(HOVER_ALPHA_TARGET).restart();
        })
        .on('mouseout', function (_event: MouseEvent, node: SimNode) {
            // A fast drag can outrun its hit circle and fire mouseout — keep
            // the gravity engaged for the dragged node.
            if (draggingNode === node) {
                return;
            }
            clearHoverHighlights();
            activeNode = null;
            syncVelocityDecay();
            node.fx = null;
            node.fy = null;
            simulation.alphaTarget(0);
        });

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

    // The draw effect keys on the dots' CONTENT, not the array identity —
    // parent re-renders (polls, context updates) must not relaunch the
    // simulation and make the chart spring from the center again. The ref
    // carries the latest array to the draw effect without being a dependency;
    // it is assigned in its own effect (before the draw effect, in-order) so
    // no ref is touched during render.
    const dotsRef = useRef(dots);
    useEffect(() => {
        dotsRef.current = dots;
    });
    const dotsKey = useMemo(
        () => dots.map((dot) => `${dot.shipId}:${dot.badgeClass}`).join(','),
        [dots],
    );

    useEffect(() => {
        const containerElement = containerRef.current;
        if (!containerElement) {
            return;
        }

        const colors = chartColors[theme];
        let simulation: BadgeSimulation | null = null;
        let resizeFrame: number | null = null;

        const resolveWidth = () => resolveContainerChartWidth(containerElement.clientWidth, svgWidth);

        const redraw = () => {
            simulation?.stop();
            simulation = drawChart(containerElement, dotsRef.current, resolveWidth(), minSvgHeight, colors);
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
    }, [dotsKey, minSvgHeight, svgWidth, theme]);

    return <div ref={containerRef} className="w-full" />;
};

export default EfficiencyStripPlotSVG;
