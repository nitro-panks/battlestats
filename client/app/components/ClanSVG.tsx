import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as d3 from 'd3';
import type { ClanMemberData, ActivityBucketKey } from './clanMembersShared';
import { buildClanChartMemberActivity, buildClanChartMemberActivitySignature, type ClanChartMemberActivity } from './clanChartActivity';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson } from '../lib/sharedJsonFetch';

interface ClanProps {
    clanId: number;
    onSelectMember?: (memberName: string) => void;
    highlightedPlayerName?: string;
    svgWidth?: number;
    svgHeight?: number;
    membersData?: ClanMemberData[];
}

interface ClanData {
    player_name: string;
    pvp_battles: number;
    pvp_ratio: number;
}

interface ClanPlotPoint extends ClanData {
    activity_bucket: ActivityBucketKey;
    days_since_last_battle: number | null;
}

interface ActivitySegment {
    key: ActivityBucketKey;
    label: string;
    shortLabel: string;
    color: string;
    count: number;
    share: number;
}

const ACTIVITY_BUCKETS: Array<{ key: ActivityBucketKey; label: string; shortLabel: string; color: string }> = [
    { key: 'active_7d', label: 'Active now', shortLabel: '0-7d', color: '#08519c' },
    { key: 'active_30d', label: 'Still warm', shortLabel: '8-30d', color: '#3182bd' },
    { key: 'cooling_90d', label: 'Cooling', shortLabel: '31-90d', color: '#6baed6' },
    { key: 'dormant_180d', label: 'Dormant', shortLabel: '91-180d', color: '#9ecae1' },
    { key: 'inactive_180d_plus', label: 'Gone dark', shortLabel: '181d+', color: '#d9e2ec' },
    { key: 'unknown', label: 'No recency', shortLabel: 'Unknown', color: '#e5e7eb' },
];

const selectClanColorByWR = (winRatio: number) => {
    if (winRatio > 65) {
        return '#810c9e';
    }
    if (winRatio >= 60) {
        return '#D042F3';
    }
    if (winRatio >= 56) {
        return '#3182bd';
    }
    if (winRatio >= 54) {
        return '#74c476';
    }
    if (winRatio >= 52) {
        return '#a1d99b';
    }
    if (winRatio >= 50) {
        return '#fed976';
    }
    if (winRatio >= 45) {
        return '#fd8d3c';
    }
    if (winRatio >= 40) {
        return '#e6550d';
    }
    return '#a50f15';
};

const buildActivitySegments = (points: ClanPlotPoint[]): ActivitySegment[] => {
    const total = points.length;

    return ACTIVITY_BUCKETS.map((bucket) => {
        const count = points.filter((point) => point.activity_bucket === bucket.key).length;
        return {
            ...bucket,
            count,
            share: total > 0 ? (count / total) * 100 : 0,
        };
    });
};

const drawClanPlot = (
    containerElement: HTMLDivElement,
    onSelectMember: ClanProps['onSelectMember'],
    highlightedPlayerName: ClanProps['highlightedPlayerName'],
    svgWidth: number,
    svgHeight: number,
    chartMembers: ClanChartMemberActivity[],
    plotData: ClanData[],
) => {
    const margin = { top: 64, right: 16, bottom: 32, left: 38 };
    const width = svgWidth - margin.left - margin.right;
    const height = svgHeight - margin.top - margin.bottom;

    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const svgRoot = container
        .append('svg')
        .attr('width', width + margin.left + margin.right)
        .attr('height', height + margin.top + margin.bottom);

    const svg = svgRoot
        .append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    const activityGroup = svgRoot
        .append('g')
        .attr('transform', `translate(${margin.left}, 16)`);

    const hideDetails = () => {
        activityGroup.select('.player-details').remove();
    };

    const showActivityDetails = (segment: ActivitySegment) => {
        activityGroup.select('.activity-details').remove();

        const detailGroup = activityGroup.append('g')
            .attr('class', 'activity-details')
            .attr('transform', 'translate(0, 2)')
            .style('pointer-events', 'none');

        const title = detailGroup.append('text')
            .attr('x', 0)
            .attr('y', 0)
            .attr('dominant-baseline', 'hanging')
            .style('font-size', '11px')
            .style('font-weight', '700')
            .style('fill', '#0f172a')
            .text(`${segment.label} • ${segment.shortLabel}`);

        title.append('tspan')
            .attr('dx', 10)
            .style('font-size', '10px')
            .style('font-weight', '400')
            .style('fill', '#475569')
            .text(`(${segment.share.toFixed(0)}%)`);

        const nodes = [title.node()].filter(Boolean) as SVGGraphicsElement[];
        const boxes = nodes.map((node) => node.getBBox());
        const minX = Math.min(...boxes.map((box) => box.x));
        const minY = Math.min(...boxes.map((box) => box.y));
        const maxX = Math.max(...boxes.map((box) => box.x + box.width));
        const maxY = Math.max(...boxes.map((box) => box.y + box.height));

        detailGroup.insert('rect', 'text')
            .attr('x', minX - 10)
            .attr('y', minY - 6)
            .attr('width', maxX - minX + 20)
            .attr('height', maxY - minY + 12)
            .attr('rx', 6)
            .attr('fill', 'rgba(255, 255, 255, 0.94)');
    };
    if (!plotData.length) {
        svg.append('text')
            .attr('x', 0)
            .attr('y', 16)
            .attr('class', 'text-sm')
            .style('fill', '#6b7280')
            .text('No clan chart data available.');
        return;
    }

    const membersByName = new Map<string, ClanChartMemberActivity>();
    chartMembers.forEach((member) => {
        membersByName.set(member.normalizedName, member);
    });

    const data: ClanPlotPoint[] = plotData.map((datum) => {
        const member = membersByName.get(datum.player_name.trim().toLowerCase());
        return {
            ...datum,
            activity_bucket: member?.activity_bucket || 'unknown',
            days_since_last_battle: member?.days_since_last_battle ?? null,
        };
    });

    const activitySegments = buildActivitySegments(data);

    const max = (d3.max(data, (datum: ClanData) => datum.pvp_battles) || 0) + 50;
    const ymax = (d3.max(data, (datum: ClanData) => datum.pvp_ratio) || 0) + 2;
    const ymin = (d3.min(data, (datum: ClanData) => datum.pvp_ratio) || 0) - 2;
    const normalizedHighlightedPlayerName = highlightedPlayerName?.trim().toLowerCase() || null;
    let hoveredBucket: ActivityBucketKey | null = null;

    svg.selectAll('*').remove();
    activityGroup.selectAll('*').remove();

    const activityBarY = 22;
    const activityBarHeight = 20;

    const activityScale = d3.scaleLinear()
        .domain([0, 100])
        .range([0, width]);

    let shareCursor = 0;
    const segments = activitySegments.map((segment) => {
        const enriched = {
            ...segment,
            shareStart: shareCursor,
            shareEnd: shareCursor + segment.share,
        };
        shareCursor = enriched.shareEnd;
        return enriched;
    });

    activityGroup.append('rect')
        .attr('x', 0)
        .attr('y', activityBarY)
        .attr('width', width)
        .attr('height', activityBarHeight)
        .attr('rx', 5)
        .attr('fill', '#f8fafc')
        .attr('stroke', '#e2e8f0')
        .attr('stroke-width', 1);

    activityGroup.append('g')
        .selectAll('rect')
        .data(segments)
        .enter()
        .append('rect')
        .attr('x', (segment: ActivitySegment & { shareStart: number }) => activityScale(segment.shareStart))
        .attr('y', activityBarY)
        .attr('width', (segment: ActivitySegment & { shareStart: number; shareEnd: number }) => Math.max(0, activityScale(segment.shareEnd) - activityScale(segment.shareStart)))
        .attr('height', activityBarHeight)
        .attr('fill', (segment: ActivitySegment) => segment.color)
        .attr('stroke', '#ffffff')
        .attr('stroke-width', 1)
        .style('cursor', 'default')
        .on('mouseover', function (_event: MouseEvent, segment: ActivitySegment) {
            hoveredBucket = segment.key;
            showActivityDetails(segment);
            applyBucketFilter();
        })
        .on('mouseout', function () {
            hoveredBucket = null;
            activityGroup.select('.activity-details').remove();
            applyBucketFilter();
        });

    const x = d3.scaleLinear()
        .domain([0, max])
        .range([0, width]);
    svg.append('g')
        .style('color', '#6b7280')
        .attr('transform', `translate(0, ${height})`)
        .call(d3.axisBottom(x).ticks(5).tickSizeOuter(0))
        .selectAll('text')
        .attr('transform', 'translate(-10,0)rotate(-45)')
        .style('text-anchor', 'end');

    const y = d3.scaleLinear()
        .domain([ymin, ymax])
        .range([height, 0]);
    svg.append('g')
        .style('color', '#6b7280')
        .call(d3.axisLeft(y).ticks(5).tickSizeOuter(0));

    const showPointDetails = (datum: ClanPlotPoint) => {
        activityGroup.select('.player-details').remove();

        const detailGroup = activityGroup.append('g')
            .attr('class', 'player-details')
            .attr('transform', 'translate(0, 2)')
            .style('pointer-events', 'none');

        const detailText = detailGroup.append('text')
            .attr('x', 0)
            .attr('y', 0)
            .attr('dominant-baseline', 'hanging');

        detailText.append('tspan')
            .style('font-size', '11px')
            .attr('font-weight', '700')
            .style('fill', '#084594')
            .text(datum.player_name);

        detailText.append('tspan')
            .attr('dx', 10)
            .style('font-size', '10px')
            .attr('font-weight', '400')
            .style('fill', '#6b7280')
            .text(`${datum.pvp_battles} Battles`);

        detailText.append('tspan')
            .attr('dx', 10)
            .style('font-size', '10px')
            .attr('font-weight', '400')
            .style('fill', '#6b7280')
            .text(`${datum.pvp_ratio}% WR`);

        if (datum.days_since_last_battle != null) {
            detailText.append('tspan')
                .attr('dx', 10)
                .style('font-size', '10px')
                .attr('font-weight', '400')
                .style('fill', '#6b7280')
                .text(`${datum.days_since_last_battle}d idle`);
        }

        const textNode = detailText.node();
        if (!textNode) {
            return;
        }

        const bbox = textNode.getBBox();
        detailGroup.insert('rect', 'text')
            .attr('x', bbox.x - 8)
            .attr('y', bbox.y - 4)
            .attr('width', bbox.width + 16)
            .attr('height', bbox.height + 8)
            .attr('rx', 6)
            .attr('fill', 'rgba(255, 255, 255, 0.92)');
    };

    const points = svg.append('g')
        .selectAll('g')
        .data(data)
        .enter()
        .append('g')
        .attr('transform', (datum: ClanPlotPoint) => `translate(${x(datum.pvp_battles)}, ${y(datum.pvp_ratio)})`);

    const dotSelection = points
        .append('circle')
        .attr('cx', 0)
        .attr('cy', 0)
        .attr('class', (datum: ClanPlotPoint) => normalizedHighlightedPlayerName === datum.player_name.trim().toLowerCase() ? 'clan-player-dot' : null)
        .attr('r', 4)
        .style('stroke', '#444')
        .style('stroke-width', 1.25)
        .style('cursor', onSelectMember ? 'pointer' : 'default')
        .attr('fill', (datum: ClanPlotPoint) => selectClanColorByWR(datum.pvp_ratio))
        .on('click', function (_event: MouseEvent, datum: ClanPlotPoint) {
            if (onSelectMember) {
                onSelectMember(datum.player_name);
            }
        })
        .on('mouseover', function (this: SVGCircleElement, _event: MouseEvent, datum: ClanPlotPoint) {
            showPointDetails(datum);
            d3.select(this).transition()
                .duration(50)
                .attr('fill', '#bcbddc');
        })
        .on('mouseout', function (_event: MouseEvent, _datum: ClanPlotPoint) {
            hideDetails();
            applyBucketFilter();
        });

    const applyBucketFilter = () => {
        dotSelection
            .attr('display', (datum: ClanPlotPoint) => {
                if (!hoveredBucket) {
                    return null;
                }
                return datum.activity_bucket === hoveredBucket ? null : null;
            })
            .attr('fill', (datum: ClanPlotPoint) => {
                if (!hoveredBucket) {
                    return selectClanColorByWR(datum.pvp_ratio);
                }
                return datum.activity_bucket === hoveredBucket ? selectClanColorByWR(datum.pvp_ratio) : '#d1d5db';
            })
            .attr('opacity', (datum: ClanPlotPoint) => {
                if (!hoveredBucket) {
                    return 1;
                }
                return datum.activity_bucket === hoveredBucket ? 1 : 0.18;
            })
            .attr('r', (datum: ClanPlotPoint) => {
                if (!hoveredBucket) {
                    return 4;
                }
                return datum.activity_bucket === hoveredBucket ? 4.5 : 3;
            });

        points
            .filter((datum: ClanPlotPoint) => hoveredBucket !== null && datum.activity_bucket === hoveredBucket)
            .raise();

        points
            .filter((datum: ClanPlotPoint) => normalizedHighlightedPlayerName === datum.player_name.trim().toLowerCase())
            .raise();
    };

    points
        .filter((datum: ClanPlotPoint) => normalizedHighlightedPlayerName === datum.player_name.trim().toLowerCase())
        .append('circle')
        .attr('class', 'clan-player-pulse-ring')
        .attr('r', 7)
        .attr('fill', 'none')
        .attr('stroke', '#f59e0b')
        .attr('stroke-width', 1.75)
        .attr('stroke-linecap', 'round')
        .style('pointer-events', 'none');

    points
        .filter((datum: ClanPlotPoint) => normalizedHighlightedPlayerName === datum.player_name.trim().toLowerCase())
        .raise();

    applyBucketFilter();
};

const ClanSVGComponent: React.FC<ClanProps> = ({ clanId, onSelectMember, highlightedPlayerName, svgWidth = 320, svgHeight = 280, membersData = [] }) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const onSelectMemberRef = useRef(onSelectMember);
    const chartMemberActivitySignature = useMemo(() => buildClanChartMemberActivitySignature(membersData), [membersData]);
    const chartMemberActivity = useMemo(() => buildClanChartMemberActivity(membersData), [chartMemberActivitySignature]);
    const [plotData, setPlotData] = useState<ClanData[] | null>(null);
    const [plotError, setPlotError] = useState(false);

    useEffect(() => {
        let cancelled = false;

        setPlotData(null);
        setPlotError(false);

        fetchSharedJson<ClanData[]>(`/api/fetch/clan_data/${clanId}:active`, {
            label: 'Clan plot data',
            ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
        })
            .then(({ data }) => {
                if (!cancelled) {
                    setPlotData(data);
                }
            })
            .catch(() => {
                if (!cancelled) {
                    setPlotError(true);
                }
            });

        return () => {
            cancelled = true;
        };
    }, [clanId]);

    useEffect(() => {
        onSelectMemberRef.current = onSelectMember;
    }, [onSelectMember]);

    useEffect(() => {
        if (!containerRef.current) {
            return;
        }

        if (plotData === null && !plotError) {
            d3.select(containerRef.current).selectAll('*').remove();
            return;
        }

        if (plotError) {
            const container = d3.select(containerRef.current);
            container.selectAll('*').remove();
            const svg = container.append('svg')
                .attr('width', svgWidth)
                .attr('height', svgHeight);

            svg.append('text')
                .attr('x', 16)
                .attr('y', 24)
                .attr('class', 'text-sm')
                .style('fill', '#6b7280')
                .text('Unable to load clan chart.');
            return;
        }

        if (plotData !== null) {
            drawClanPlot(
                containerRef.current,
                (memberName) => onSelectMemberRef.current?.(memberName),
                highlightedPlayerName,
                svgWidth,
                svgHeight,
                chartMemberActivity,
                plotData,
            );
        }
    }, [chartMemberActivity, chartMemberActivitySignature, highlightedPlayerName, plotData, plotError, svgHeight, svgWidth]);

    return <div ref={containerRef}></div>;
};

const areClanSvgPropsEqual = (previousProps: ClanProps, nextProps: ClanProps): boolean => {
    return previousProps.clanId === nextProps.clanId
        && previousProps.highlightedPlayerName === nextProps.highlightedPlayerName
        && (previousProps.svgWidth ?? 320) === (nextProps.svgWidth ?? 320)
        && (previousProps.svgHeight ?? 280) === (nextProps.svgHeight ?? 280)
        && buildClanChartMemberActivitySignature(previousProps.membersData ?? []) === buildClanChartMemberActivitySignature(nextProps.membersData ?? []);
};

const ClanSVG = React.memo(ClanSVGComponent, areClanSvgPropsEqual);

export default ClanSVG;
