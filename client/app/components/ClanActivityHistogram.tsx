import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as d3 from 'd3';

interface ClanActivityHistogramProps {
    clanId: number;
    memberCount: number;
    svgHeight?: number;
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
    color: string;
    members: ClanMemberData[];
    count: number;
    averageWinRate: number | null;
    averageDays: number | null;
}

const BUCKET_ORDER: Array<{ key: ActivityBucketKey; label: string; subtitle: string; color: string }> = [
    { key: 'active_7d', label: '0-7d', subtitle: 'Active now', color: '#08519c' },
    { key: 'active_30d', label: '8-30d', subtitle: 'Still warm', color: '#3182bd' },
    { key: 'cooling_90d', label: '31-90d', subtitle: 'Cooling', color: '#6baed6' },
    { key: 'dormant_180d', label: '91-180d', subtitle: 'Dormant', color: '#9ecae1' },
    { key: 'inactive_180d_plus', label: '181d+', subtitle: 'Gone dark', color: '#d9e2ec' },
    { key: 'unknown', label: 'Unknown', subtitle: 'No recency', color: '#e5e7eb' },
];

const drawEmptyState = (containerElement: HTMLDivElement, message: string, width: number, height: number) => {
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
        .style('fill', '#6b7280')
        .text(message);
};

const formatAverageWinRate = (value: number | null): string => {
    if (value == null) {
        return 'No WR signal';
    }

    return `${value.toFixed(1)}% avg WR`;
};

const buildBins = (members: ClanMemberData[]): ActivityBin[] => BUCKET_ORDER.map((bucket) => {
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
    };
});

const showDetails = (svgRoot: SvgRootSelection, bin: ActivityBin, totalMembers: number) => {
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
        .style('fill', '#0f172a')
        .text(`${bin.subtitle} • ${bin.label}`);

    const meta = detailGroup.append('text')
        .attr('x', 0)
        .attr('y', 17)
        .style('font-size', '10px')
        .style('fill', '#475569')
        .text(`${bin.count} members • ${((bin.count / Math.max(totalMembers, 1)) * 100).toFixed(0)}% of roster • ${formatAverageWinRate(bin.averageWinRate)}`);

    const names = detailGroup.append('text')
        .attr('x', 0)
        .attr('y', 33)
        .style('font-size', '10px')
        .style('fill', '#64748b')
        .text(memberPreview ? `${memberPreview}${overflow}` : 'No members in this band');

    const nodes = [title.node(), meta.node(), names.node()].filter(Boolean) as SVGGraphicsElement[];
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

const drawChart = (containerElement: HTMLDivElement, bins: ActivityBin[], totalMembers: number, containerWidth: number, svgHeight: number) => {
    const margin = { top: 56, right: 16, bottom: 48, left: 42 };
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

    const visibleBins = bins.filter((bin) => bin.count > 0);
    if (!visibleBins.length) {
        drawEmptyState(containerElement, 'No clan activity data available.', containerWidth, svgHeight);
        return;
    }

    const x = d3.scaleBand()
        .domain(visibleBins.map((bin: ActivityBin) => bin.key))
        .range([0, width])
        .paddingInner(0.22)
        .paddingOuter(0.12);

    const maxCount = visibleBins.reduce((currentMax: number, bin: ActivityBin) => Math.max(currentMax, bin.count), 0);

    const y = d3.scaleLinear()
        .domain([0, maxCount || 1])
        .nice()
        .range([height, 0]);

    svg.append('g')
        .call(d3.axisLeft(y).ticks(Math.min(5, totalMembers || 1)).tickSize(-width).tickFormat(() => ''));
    svg.selectAll('.tick line')
        .style('stroke', '#e2e8f0');
    svg.selectAll('.domain').remove();

    svg.append('g')
        .style('color', '#64748b')
        .call(d3.axisLeft(y).ticks(Math.min(5, totalMembers || 1)).tickSizeOuter(0));

    svg.append('g')
        .attr('transform', `translate(0, ${height})`)
        .style('color', '#64748b')
        .call(d3.axisBottom(x).tickFormat((value: string) => {
            const matched = visibleBins.find((bin: ActivityBin) => bin.key === value);
            return matched ? matched.label : String(value);
        }).tickSizeOuter(0));

    svg.append('text')
        .attr('x', width / 2)
        .attr('y', height + 36)
        .attr('text-anchor', 'middle')
        .style('fill', '#64748b')
        .style('font-size', '10px')
        .text('Days Since Last Battle');

    const barGroups = svg.append('g')
        .selectAll('g')
        .data(visibleBins)
        .enter()
        .append('g');

    barGroups.append('rect')
        .attr('x', (bin: ActivityBin) => x(bin.key) || 0)
        .attr('y', (bin: ActivityBin) => y(bin.count))
        .attr('width', x.bandwidth())
        .attr('height', (bin: ActivityBin) => height - y(bin.count))
        .attr('rx', 4)
        .attr('fill', (bin: ActivityBin) => bin.color)
        .on('mouseover', (_event: MouseEvent, bin: ActivityBin) => showDetails(svgRoot, bin, totalMembers))
        .on('mouseout', () => svgRoot.select('.clan-activity-details').remove());

    barGroups.append('text')
        .attr('x', (bin: ActivityBin) => (x(bin.key) || 0) + (x.bandwidth() / 2))
        .attr('y', (bin: ActivityBin) => y(bin.count) - 8)
        .attr('text-anchor', 'middle')
        .style('font-size', '10px')
        .style('font-weight', '700')
        .style('fill', '#334155')
        .text((bin: ActivityBin) => String(bin.count));

    barGroups.append('text')
        .attr('x', (bin: ActivityBin) => (x(bin.key) || 0) + (x.bandwidth() / 2))
        .attr('y', height + 16)
        .attr('text-anchor', 'middle')
        .style('font-size', '10px')
        .style('fill', '#94a3b8')
        .text((bin: ActivityBin) => bin.subtitle);

    svgRoot.append('text')
        .attr('x', margin.left)
        .attr('y', 18)
        .style('font-size', '10px')
        .style('fill', '#64748b')
        .text('Bar height = members. Hover for roster slice and average WR.');
};

const ClanActivityHistogram: React.FC<ClanActivityHistogramProps> = ({ clanId, memberCount, svgHeight = 264 }) => {
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
                const response = await fetch(`http://localhost:8888/api/fetch/clan_members/${clanId}/`, { signal: controller.signal });
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

        if (loadError) {
            drawEmptyState(containerRef.current, loadError, containerWidth, 96);
            return;
        }

        drawChart(containerRef.current, bins, members.length, Math.max(containerWidth, 320), svgHeight);
    }, [bins, containerWidth, loadError, loading, members.length, svgHeight]);

    const rosterSize = members.length || memberCount;
    const activeShare = rosterSize > 0 ? Math.round((activeThirtyCount / rosterSize) * 100) : 0;

    return (
        <section>
            <h3 className="text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Clan Activity</h3>
            <p className="mt-2 max-w-4xl text-sm leading-7 text-[#4a5568]">
                {activeThirtyCount} of {rosterSize} members have played within the last 30 days. {dormantCount} have been dark for more than 90 days. Read left to right to see whether this roster still has a live core or just a nameplate.
            </p>
            <p className="mt-1 text-xs uppercase tracking-[0.18em] text-[#718096]">
                {activeShare}% active within 30 days
            </p>
            {loading ? (
                <div className="mt-4 flex min-h-[180px] items-center justify-center rounded-md border border-gray-200 bg-gray-50 text-sm text-gray-500">
                    Loading clan activity...
                </div>
            ) : (
                <div ref={containerRef} className="mt-4 w-full overflow-hidden" />
            )}
        </section>
    );
};

export default ClanActivityHistogram;