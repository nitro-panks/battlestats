import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as d3 from 'd3';
import type { ClanMemberData, ActivityBucketKey } from './clanMembersShared';
import { buildClanChartMemberActivity, buildClanChartMemberActivitySignature, type ClanChartMemberActivity } from './clanChartActivity';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { chartColors, type ChartTheme } from '../lib/chartTheme';

interface ClanProps {
    clanId: number;
    onSelectMember?: (memberName: string) => void;
    highlightedPlayerName?: string;
    svgWidth?: number;
    svgHeight?: number;
    membersData?: ClanMemberData[];
    theme?: ChartTheme;
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

const getActivityBuckets = (theme: ChartTheme): Array<{ key: ActivityBucketKey; label: string; shortLabel: string; color: string }> => {
    const colors = chartColors[theme];
    return [
        { key: 'active_7d', label: 'Active now', shortLabel: '0-7d', color: colors.activityActive },
        { key: 'active_30d', label: 'Still warm', shortLabel: '8-30d', color: colors.activityRecent },
        { key: 'cooling_90d', label: 'Cooling', shortLabel: '31-90d', color: colors.activityCooling },
        { key: 'dormant_180d', label: 'Dormant', shortLabel: '91-180d', color: colors.activityDormant },
        { key: 'inactive_180d_plus', label: 'Gone dark', shortLabel: '181d+', color: colors.activityInactive },
        { key: 'unknown', label: 'No recency', shortLabel: 'Unknown', color: colors.activityUnknown },
    ];
};

const CLAN_PLOT_FETCH_RETRY_DELAY_MS = 350;
const CLAN_PLOT_FETCH_ATTEMPTS = 2;
const CLAN_PLOT_PENDING_RETRY_DELAY_MS = 3000;
const CLAN_PLOT_PENDING_RETRY_LIMIT = 20;

const delay = (timeoutMs: number): Promise<void> => new Promise((resolve) => {
    window.setTimeout(resolve, timeoutMs);
});

const selectClanColorByWR = (winRatio: number, theme: ChartTheme) => {
    const colors = chartColors[theme];
    if (winRatio > 65) {
        return colors.wrElite;
    }
    if (winRatio >= 60) {
        return colors.wrSuperUnicum;
    }
    if (winRatio >= 56) {
        return colors.wrUnicum;
    }
    if (winRatio >= 54) {
        return colors.wrVeryGood;
    }
    if (winRatio >= 52) {
        return colors.wrGood;
    }
    if (winRatio >= 50) {
        return colors.wrAboveAvg;
    }
    if (winRatio >= 45) {
        return colors.wrAverage;
    }
    if (winRatio >= 40) {
        return colors.wrBelowAvg;
    }
    return colors.wrBad;
};

const buildActivitySegments = (points: ClanPlotPoint[], theme: ChartTheme): ActivitySegment[] => {
    const total = points.length;

    return getActivityBuckets(theme).map((bucket) => {
        const count = points.filter((point) => point.activity_bucket === bucket.key).length;
        return {
            ...bucket,
            count,
            share: total > 0 ? (count / total) * 100 : 0,
        };
    });
};

const drawClanChartStatus = (
    containerElement: HTMLDivElement,
    message: string,
    svgWidth: number,
    svgHeight: number,
    theme: ChartTheme,
) => {
    const colors = chartColors[theme];
    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const svg = container.append('svg')
        .attr('width', svgWidth)
        .attr('height', svgHeight);

    svg.append('text')
        .attr('x', 16)
        .attr('y', 24)
        .attr('class', 'text-sm')
        .style('fill', colors.labelText)
        .text(message);
};

const drawClanPlot = (
    containerElement: HTMLDivElement,
    onSelectMember: ClanProps['onSelectMember'],
    highlightedPlayerName: ClanProps['highlightedPlayerName'],
    svgWidth: number,
    svgHeight: number,
    chartMembers: ClanChartMemberActivity[],
    plotData: ClanData[],
    theme: ChartTheme,
) => {
    const colors = chartColors[theme];
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
            .style('fill', colors.labelStrong)
            .text(`${segment.label} • ${segment.shortLabel}`);

        title.append('tspan')
            .attr('dx', 10)
            .style('font-size', '10px')
            .style('font-weight', '400')
            .style('fill', colors.labelMid)
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
            .attr('fill', theme === 'dark' ? 'rgba(13, 17, 23, 0.94)' : 'rgba(255, 255, 255, 0.94)');
    };
    if (!plotData.length) {
        svg.append('text')
            .attr('x', 0)
            .attr('y', 16)
            .attr('class', 'text-sm')
            .style('fill', colors.labelText)
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

    const activitySegments = buildActivitySegments(data, theme);

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
        .attr('fill', colors.barBg)
        .attr('stroke', colors.gridLine)
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
        .attr('stroke', colors.barStroke)
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
        .style('color', colors.labelText)
        .attr('transform', `translate(0, ${height})`)
        .call(d3.axisBottom(x).ticks(5).tickSizeOuter(0))
        .selectAll('text')
        .attr('transform', 'translate(-10,0)rotate(-45)')
        .style('text-anchor', 'end');

    const y = d3.scaleLinear()
        .domain([ymin, ymax])
        .range([height, 0]);
    svg.append('g')
        .style('color', colors.labelText)
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
            .style('fill', colors.accentLink)
            .text(datum.player_name);

        detailText.append('tspan')
            .attr('dx', 10)
            .style('font-size', '10px')
            .attr('font-weight', '400')
            .style('fill', colors.labelText)
            .text(`${datum.pvp_battles} Battles`);

        detailText.append('tspan')
            .attr('dx', 10)
            .style('font-size', '10px')
            .attr('font-weight', '400')
            .style('fill', colors.labelText)
            .text(`${datum.pvp_ratio}% WR`);

        if (datum.days_since_last_battle != null) {
            detailText.append('tspan')
                .attr('dx', 10)
                .style('font-size', '10px')
                .attr('font-weight', '400')
                .style('fill', colors.labelText)
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
            .attr('fill', theme === 'dark' ? 'rgba(13, 17, 23, 0.92)' : 'rgba(255, 255, 255, 0.92)');
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
        .style('stroke', colors.axisLine)
        .style('stroke-width', 1.25)
        .style('cursor', onSelectMember ? 'pointer' : 'default')
        .attr('fill', (datum: ClanPlotPoint) => selectClanColorByWR(datum.pvp_ratio, theme))
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
                    return selectClanColorByWR(datum.pvp_ratio, theme);
                }
                return datum.activity_bucket === hoveredBucket ? selectClanColorByWR(datum.pvp_ratio, theme) : '#d1d5db';
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

const ClanSVGComponent: React.FC<ClanProps> = ({ clanId, onSelectMember, highlightedPlayerName, svgWidth = 320, svgHeight = 280, membersData = [], theme = 'light' }) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const onSelectMemberRef = useRef(onSelectMember);
    const chartMemberActivitySignature = useMemo(() => buildClanChartMemberActivitySignature(membersData), [membersData]);
    const chartMemberActivity = useMemo(() => buildClanChartMemberActivity(membersData), [chartMemberActivitySignature]);
    const [plotData, setPlotData] = useState<ClanData[] | null>(null);
    const [plotError, setPlotError] = useState(false);
    const [isPlotPendingRefresh, setIsPlotPendingRefresh] = useState(false);

    useEffect(() => {
        let cancelled = false;
        let timeoutId: ReturnType<typeof setTimeout> | null = null;
        let pendingAttempts = 0;

        setPlotData(null);
        setPlotError(false);
        setIsPlotPendingRefresh(false);

        const requestPlotData = async (): Promise<{ data: ClanData[]; pending: boolean } | null> => {
            for (let attempt = 0; attempt < CLAN_PLOT_FETCH_ATTEMPTS; attempt += 1) {
                try {
                    const payload = await fetchSharedJson<ClanData[]>(`/api/fetch/clan_data/${clanId}:active`, {
                        label: 'Clan plot data',
                        ttlMs: 0,
                        cacheKey: `clan-plot:${clanId}:${pendingAttempts}:${attempt}`,
                        responseHeaders: ['X-Clan-Plot-Pending'],
                    });

                    return {
                        data: payload.data,
                        pending: payload.headers['X-Clan-Plot-Pending'] === 'true',
                    };
                } catch {
                    if (attempt + 1 < CLAN_PLOT_FETCH_ATTEMPTS) {
                        await delay(CLAN_PLOT_FETCH_RETRY_DELAY_MS);
                        if (cancelled) {
                            return null;
                        }
                        continue;
                    }
                }
            }

            return null;
        };

        const loadPlotData = async () => {
            timeoutId = null;

            const result = await requestPlotData();
            if (cancelled) {
                return;
            }

            if (result === null) {
                setPlotError(true);
                setIsPlotPendingRefresh(false);
                return;
            }

            setPlotData(result.data);
            setPlotError(false);
            setIsPlotPendingRefresh(result.pending);

            if (result.pending && pendingAttempts < CLAN_PLOT_PENDING_RETRY_LIMIT) {
                pendingAttempts += 1;
                timeoutId = setTimeout(() => {
                    void loadPlotData();
                }, CLAN_PLOT_PENDING_RETRY_DELAY_MS);
            }
        };

        void loadPlotData();

        return () => {
            cancelled = true;
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
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
            drawClanChartStatus(containerRef.current, 'Loading clan chart data...', svgWidth, svgHeight, theme);
            return;
        }

        if (plotError) {
            drawClanChartStatus(containerRef.current, 'Unable to load clan chart.', svgWidth, svgHeight, theme);
            return;
        }

        if (plotData !== null && isPlotPendingRefresh && plotData.length === 0) {
            drawClanChartStatus(containerRef.current, 'Loading clan chart data...', svgWidth, svgHeight, theme);
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
                theme,
            );
        }
    }, [chartMemberActivity, chartMemberActivitySignature, highlightedPlayerName, isPlotPendingRefresh, plotData, plotError, svgHeight, svgWidth, theme]);

    return <div ref={containerRef}></div>;
};

const areClanSvgPropsEqual = (previousProps: ClanProps, nextProps: ClanProps): boolean => {
    return previousProps.clanId === nextProps.clanId
        && previousProps.highlightedPlayerName === nextProps.highlightedPlayerName
        && (previousProps.svgWidth ?? 320) === (nextProps.svgWidth ?? 320)
        && (previousProps.svgHeight ?? 280) === (nextProps.svgHeight ?? 280)
        && (previousProps.theme ?? 'light') === (nextProps.theme ?? 'light')
        && buildClanChartMemberActivitySignature(previousProps.membersData ?? []) === buildClanChartMemberActivitySignature(nextProps.membersData ?? []);
};

const ClanSVG = React.memo(ClanSVGComponent, areClanSvgPropsEqual);

export default ClanSVG;
