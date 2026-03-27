import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as d3 from 'd3';
import { chartColors, type ChartTheme } from '../lib/chartTheme';

interface ClanActivityHistogramProps {
    clanId: number;
    memberCount: number;
    svgHeight?: number;
    svgWidth?: number;
    theme?: ChartTheme;
}

interface ClanMemberData {
    name: string;
    is_hidden: boolean;
    pvp_ratio: number | null;
    days_since_last_battle: number | null;
    activity_bucket: ActivityBucketKey;
}

type ActivityBucketKey = 'active_7d' | 'active_30d' | 'cooling_90d' | 'dormant_180d' | 'inactive_180d_plus' | 'unknown';

type SvgRootSelection = ReturnType<typeof d3.select>;

interface ActivityBin {
    key: ActivityBucketKey;
    label: string;
    subtitle: string;
    members: ClanMemberData[];
    count: number;
    averageWinRate: number | null;
    averageDays: number | null;
    shareOfRoster: number;
}

const BUCKET_META: Array<{ key: ActivityBucketKey; label: string; subtitle: string }> = [
    { key: 'active_7d', label: '0-7d', subtitle: 'Active now' },
    { key: 'active_30d', label: '8-30d', subtitle: 'Still warm' },
    { key: 'cooling_90d', label: '31-90d', subtitle: 'Cooling' },
    { key: 'dormant_180d', label: '91-180d', subtitle: 'Dormant' },
    { key: 'inactive_180d_plus', label: '181d+', subtitle: 'Gone dark' },
    { key: 'unknown', label: 'Unknown', subtitle: 'No recency' },
];

type Colors = typeof chartColors['light'];

const ACTIVITY_COLOR_KEY: Record<ActivityBucketKey, keyof Colors> = {
    active_7d: 'activityActive',
    active_30d: 'activityRecent',
    cooling_90d: 'activityCooling',
    dormant_180d: 'activityDormant',
    inactive_180d_plus: 'activityInactive',
    unknown: 'activityUnknown',
};

const drawEmptyState = (containerElement: HTMLDivElement, message: string, width: number, height: number, colors: Colors) => {
    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const svg = container
        .append('svg')
        .attr('width', width)
        .attr('height', height);

    svg.append('text')
        .attr('x', 0)
        .attr('y', 18)
        .style('font-size', '12px')
        .style('fill', colors.labelText)
        .text(message);
};

const formatAverageWinRate = (value: number | null): string => {
    if (value == null) {
        return 'No WR signal';
    }

    return `${value.toFixed(1)}% avg WR`;
};

const buildBins = (members: ClanMemberData[]): ActivityBin[] => BUCKET_META.map((bucket) => {
    const bucketMembers = members.filter((member) => member.activity_bucket === bucket.key);
    const membersWithWinRate = bucketMembers.filter((member) => typeof member.pvp_ratio === 'number');
    const membersWithDays = bucketMembers.filter((member) => typeof member.days_since_last_battle === 'number');

    return {
        ...bucket,
        members: bucketMembers,
        count: bucketMembers.length,
        averageWinRate: membersWithWinRate.length > 0
            ? membersWithWinRate.reduce((sum, member) => sum + (member.pvp_ratio || 0), 0) / membersWithWinRate.length
            : null,
        averageDays: membersWithDays.length > 0
            ? membersWithDays.reduce((sum, member) => sum + (member.days_since_last_battle || 0), 0) / membersWithDays.length
            : null,
        shareOfRoster: members.length > 0 ? (bucketMembers.length / members.length) * 100 : 0,
    };
});

const showDetails = (svgRoot: SvgRootSelection, bin: ActivityBin, totalMembers: number, colors: Colors) => {
    svgRoot.select('.clan-activity-details').remove();

    const detailGroup = svgRoot
        .append('g')
        .attr('class', 'clan-activity-details')
        .attr('transform', 'translate(46, 14)');

    const memberPreview = bin.members.slice(0, 4).map((member) => member.name).join(', ');
    const overflow = bin.count > 4 ? ` +${bin.count - 4} more` : '';

    const title = detailGroup.append('text')
        .attr('x', 0)
        .attr('y', 0)
        .style('font-size', '11px')
        .style('font-weight', '700')
        .style('fill', colors.labelStrong)
        .text(`${bin.subtitle} • ${bin.label}`);

    const meta = detailGroup.append('text')
        .attr('x', 0)
        .attr('y', 17)
        .style('font-size', '10px')
        .style('fill', colors.axisText)
        .text(`${bin.count} members • ${((bin.count / Math.max(totalMembers, 1)) * 100).toFixed(0)}% of roster • ${formatAverageWinRate(bin.averageWinRate)}`);

    const names = detailGroup.append('text')
        .attr('x', 0)
        .attr('y', 33)
        .style('font-size', '10px')
        .style('fill', colors.labelMuted)
        .text(memberPreview ? `${memberPreview}${overflow}` : 'No members in this band');

    const averageDays = detailGroup.append('text')
        .attr('x', 0)
        .attr('y', 49)
        .style('font-size', '10px')
        .style('fill', colors.labelMuted)
        .text(bin.averageDays == null ? 'Average recency unavailable' : `${Math.round(bin.averageDays)} day average idle span`);

    const nodes = [title.node(), meta.node(), names.node(), averageDays.node()].filter(Boolean) as SVGGraphicsElement[];
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
        .attr('fill', colors.surface)
        .attr('fill-opacity', 0.96);
};

const drawChart = (containerElement: HTMLDivElement, bins: ActivityBin[], totalMembers: number, containerWidth: number, svgHeight: number, colors: Colors) => {
    const margin = { top: 10, right: 18, bottom: 34, left: 18 };
    const width = containerWidth - margin.left - margin.right;
    const height = svgHeight - margin.top - margin.bottom;
    const container = d3.select(containerElement);
    container.selectAll('*').remove();

    const svgRoot = container
        .append('svg')
        .attr('width', width + margin.left + margin.right)
        .attr('height', svgHeight);

    const svg = svgRoot
        .append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    if (!bins.length) {
        drawEmptyState(containerElement, 'No clan activity data available.', containerWidth, svgHeight, colors);
        return;
    }

    const x = d3.scaleLinear()
        .domain([0, 100])
        .range([0, width]);

    const barHeight = Math.min(48, Math.max(34, height - 14));
    const barY = Math.max(12, (height - barHeight) / 2);

    let startShare = 0;
    const segments = bins.map((bin: ActivityBin) => {
        const segment = {
            ...bin,
            shareStart: startShare,
            shareEnd: startShare + bin.shareOfRoster,
        };
        startShare = segment.shareEnd;
        return segment;
    });

    svg.append('g')
        .attr('class', 'clan-activity-grid')
        .attr('transform', `translate(0, ${barY + barHeight})`)
        .call(d3.axisBottom(x).tickValues([0, 20, 40, 60, 80, 100]).tickSize(8).tickFormat(() => ''));
    svg.select('.clan-activity-grid')?.select('.domain')?.remove();
    svg.selectAll('.clan-activity-grid line')
        .style('stroke', colors.gridLine)
        .style('stroke-width', 1);

    svg.append('g')
        .attr('transform', `translate(0, ${barY + barHeight + 8})`)
        .style('color', colors.labelMuted)
        .call(d3.axisBottom(x).tickValues([0, 20, 40, 60, 80, 100]).tickFormat((value: number) => `${value}%`).tickSizeOuter(0))
        .selectAll('text')
        .style('font-size', '10px');

    svg.append('rect')
        .attr('x', 0)
        .attr('y', barY)
        .attr('width', width)
        .attr('height', barHeight)
        .attr('rx', 5)
        .attr('fill', colors.barBg)
        .attr('stroke', colors.gridLine)
        .attr('stroke-width', 1);

    svg.append('g')
        .selectAll('rect')
        .data(segments)
        .enter()
        .append('rect')
        .attr('x', (segment: ActivityBin & { shareStart: number }) => x(segment.shareStart))
        .attr('y', barY)
        .attr('width', (segment: ActivityBin & { shareStart: number; shareEnd: number }) => Math.max(0, x(segment.shareEnd) - x(segment.shareStart)))
        .attr('height', barHeight)
        .attr('fill', (segment: ActivityBin) => colors[ACTIVITY_COLOR_KEY[segment.key]] as string)
        .attr('stroke', colors.barStroke)
        .attr('stroke-width', 1)
        .style('cursor', 'default')
        .on('mouseover', (_event: MouseEvent, segment: ActivityBin) => showDetails(svgRoot, segment, totalMembers, colors))
        .on('mouseout', () => svgRoot.select('.clan-activity-details').remove());
};

const ClanActivityHistogram: React.FC<ClanActivityHistogramProps> = ({ clanId, memberCount, svgHeight = 246, svgWidth = 900, theme = 'light' }) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [members, setMembers] = useState<ClanMemberData[]>([]);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [containerWidth, setContainerWidth] = useState(320);

    useEffect(() => {
        const controller = new AbortController();

        const fetchMembers = async () => {
            setLoading(true);
            setLoadError(null);

            try {
                const response = await fetch(`/api/fetch/clan_members/${clanId}`, { signal: controller.signal });
                if (!response.ok) {
                    throw new Error(`Failed to fetch clan members: ${response.status}`);
                }

                const data = await response.json();
                setMembers(Array.isArray(data) ? data : []);
            } catch (error) {
                if (!controller.signal.aborted) {
                    setLoadError('Unable to load clan activity right now.');
                }
            } finally {
                if (!controller.signal.aborted) {
                    setLoading(false);
                }
            }
        };

        fetchMembers();
        return () => controller.abort();
    }, [clanId]);

    useEffect(() => {
        if (!containerRef.current) {
            return;
        }

        const observer = new ResizeObserver((entries) => {
            for (const entry of entries) {
                setContainerWidth(entry.contentRect.width);
            }
        });

        observer.observe(containerRef.current);
        setContainerWidth(containerRef.current.clientWidth);

        return () => observer.disconnect();
    }, []);

    const bins = useMemo(() => buildBins(members), [members]);
    const activeThirtyCount = useMemo(
        () => members.filter((member) => typeof member.days_since_last_battle === 'number' && member.days_since_last_battle <= 30).length,
        [members],
    );
    const dormantCount = useMemo(
        () => members.filter((member) => typeof member.days_since_last_battle === 'number' && member.days_since_last_battle > 90).length,
        [members],
    );

    useEffect(() => {
        if (!containerRef.current || containerWidth < 120 || loading) {
            return;
        }

        const colors = chartColors[theme];

        if (loadError) {
            drawEmptyState(containerRef.current, loadError, containerWidth, 96, colors);
            return;
        }

        drawChart(containerRef.current, bins, members.length, svgWidth, svgHeight, colors);
    }, [bins, containerWidth, loadError, loading, members.length, svgHeight, svgWidth, theme]);

    const rosterSize = members.length || memberCount;
    const activeShare = rosterSize > 0 ? Math.round((activeThirtyCount / rosterSize) * 100) : 0;

    return (
        <section>
            <h3 className="text-sm font-semibold uppercase tracking-wide text-[#2171b5] dark:text-[#58a6ff]">Clan Activity</h3>
            <p className="mt-2 max-w-4xl text-sm leading-7 text-[#4a5568] dark:text-[#c9d1d9]">
                {activeThirtyCount} of {rosterSize} members have played within the last 30 days. {dormantCount} have been dark for more than 90 days. The single 100% bar keeps every inactivity band on one stable line, so you can read the clan&apos;s full composition from active now at the left edge to no recency at the right edge before comparing skill and volume below.
            </p>
            <p className="mt-1 text-xs uppercase tracking-[0.18em] text-[#718096] dark:text-[#8b949e]">
                {activeShare}% active within 30 days
            </p>
            {loading ? (
                <div className="mt-4 flex min-h-[180px] items-center justify-center rounded-md border border-[var(--border)] bg-[var(--bg-surface)] text-sm text-[var(--text-secondary)]">
                    Loading clan activity...
                </div>
            ) : (
                <div ref={containerRef} className="mt-3 w-[900px] max-w-full overflow-x-auto overflow-y-hidden" />
            )}
        </section>
    );
};

export default ClanActivityHistogram;
