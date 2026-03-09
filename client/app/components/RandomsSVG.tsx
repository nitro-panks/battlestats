import React, { useEffect, useState, useRef, useMemo } from 'react';
import * as d3 from 'd3';

interface RandomsSVGProps {
    playerId: number;
    isLoading?: boolean;
}

interface RandomsRow {
    pvp_battles: number;
    ship_name: string;
    ship_type: string;
    ship_tier: number;
    win_ratio: number;
    wins: number;
}

const TOP_N = 20;

const selectRandomsColorByWr = (winRatio: number): string => {
    if (winRatio > 0.65) return '#810c9e';
    if (winRatio >= 0.60) return '#D042F3';
    if (winRatio >= 0.56) return '#3182bd';
    if (winRatio >= 0.54) return '#74c476';
    if (winRatio >= 0.52) return '#a1d99b';
    if (winRatio >= 0.50) return '#fed976';
    if (winRatio >= 0.45) return '#fd8d3c';
    if (winRatio >= 0.40) return '#e6550d';
    return '#a50f15';
};

const drawBattlePlot = (containerElement: HTMLDivElement, data: RandomsRow[]) => {
    const margin = { top: 10, right: 20, bottom: 30, left: 140 };
    const width = 500 - margin.left - margin.right;
    const height = 500 - margin.top - margin.bottom;

    const svg = d3.select(containerElement)
        .append('svg')
        .attr('width', width + margin.left + margin.right)
        .attr('height', height + margin.top + margin.bottom)
        .append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    const max = Math.max(d3.max(data, (datum: RandomsRow) => +datum.pvp_battles) || 0, 15);

    const x = d3.scaleLinear()
        .domain([0, max])
        .range([1, width]);
    svg.append('g')
        .attr('transform', `translate(0, ${height})`)
        .call(d3.axisBottom(x))
        .selectAll('text')
        .attr('transform', 'translate(-10,0)rotate(-45)')
        .style('text-anchor', 'end');

    const y = d3.scaleBand()
        .range([0, height])
        .domain(data.map((datum: RandomsRow) => datum.ship_name))
        .padding(.1);
    svg.append('g')
        .call(d3.axisLeft(y));

    svg.append('text')
        .attr('class', 'f6 lh-copy bar1')
        .attr('text-anchor', 'end')
        .attr('x', width)
        .attr('y', height - 6)
        .text('Random Battles');

    const showDetails = (datum: RandomsRow) => {
        const startX = 200;
        const startY = 320;
        const xOffset = 10;
        const winPercentage = ((datum.wins / datum.pvp_battles) * 100).toFixed(2);

        const detailGroup = svg.append('g')
            .classed('details', true);
        detailGroup.append('text')
            .attr('x', startX)
            .attr('y', startY)
            .attr('font-weight', '700')
            .text(datum.ship_name);

        detailGroup.append('text')
            .attr('x', startX + xOffset)
            .attr('y', startY + 25)
            .style('font-size', '16px')
            .text(winPercentage);
        detailGroup.append('text')
            .attr('x', startX + xOffset + 45)
            .attr('y', startY + 25)
            .style('font-size', '12px')
            .text('% Win Rate');

        detailGroup.append('text')
            .attr('x', startX + xOffset)
            .attr('y', startY + 47)
            .style('font-size', '16px')
            .text(datum.pvp_battles);
    };

    const hideDetails = () => {
        svg.selectAll('.details').remove();
    };

    const nodes = svg.selectAll('.rect')
        .data(data)
        .enter()
        .append('g')
        .classed('rect', true);

    nodes.append('rect')
        .attr('x', x(0))
        .attr('y', (datum: RandomsRow) => (y(datum.ship_name) ?? 0) + 3)
        .attr('width', (datum: RandomsRow) => x(datum.pvp_battles))
        .attr('height', y.bandwidth() * .7)
        .attr('fill', '#d9d9d9');

    nodes.append('rect')
        .attr('x', x(0))
        .attr('y', (datum: RandomsRow) => y(datum.ship_name) ?? 0)
        .attr('width', (datum: RandomsRow) => x(datum.wins))
        .attr('height', y.bandwidth())
        .style('stroke', '#444')
        .style('stroke-width', 0.75)
        .attr('fill', (datum: RandomsRow) => selectRandomsColorByWr(datum.win_ratio))
        .on('mouseover', function (this: SVGRectElement, _event: MouseEvent, datum: RandomsRow) {
            showDetails(datum);
            d3.select(this).transition()
                .duration(50)
                .attr('fill', '#bcbddc');
        })
        .on('mouseout', function (this: SVGRectElement, _event: MouseEvent, datum: RandomsRow) {
            hideDetails();
            d3.select(this).transition()
                .duration(50)
                .attr('fill', selectRandomsColorByWr(datum.win_ratio));
        });
};

const RandomsSVG: React.FC<RandomsSVGProps> = ({ playerId, isLoading = false }) => {
    const [allShips, setAllShips] = useState<RandomsRow[]>([]);
    const [selectedTypes, setSelectedTypes] = useState<string[]>([]);
    const [selectedTiers, setSelectedTiers] = useState<number[]>([]);
    const [isChartLoading, setIsChartLoading] = useState(false);
    const [randomsUpdatedAt, setRandomsUpdatedAt] = useState<string | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);

    // Fetch ALL ships once
    useEffect(() => {
        const fetchData = async () => {
            setIsChartLoading(true);
            try {
                const response = await fetch(`http://localhost:8888/api/fetch/randoms_data/${playerId}/?all=true`);
                const result: RandomsRow[] = await response.json();
                setAllShips(result);
                setRandomsUpdatedAt(response.headers.get('X-Randoms-Updated-At'));

                const types = Array.from(new Set(result.map((r) => r.ship_type)));
                const tiers = Array.from(new Set(result.map((r) => r.ship_tier))).sort((a, b) => b - a);
                setSelectedTypes(types);
                setSelectedTiers(tiers);
            } catch (error) {
                console.error('Error fetching data:', error);
            } finally {
                setIsChartLoading(false);
            }
        };
        fetchData();
    }, [playerId]);

    // Filter, sort, and take top N
    const chartData = useMemo(() => {
        const filtered = allShips.filter(
            (row) => selectedTypes.includes(row.ship_type) && selectedTiers.includes(row.ship_tier)
        );
        return filtered
            .sort((a, b) => b.pvp_battles - a.pvp_battles)
            .slice(0, TOP_N);
    }, [allShips, selectedTypes, selectedTiers]);

    // Draw chart when data changes
    useEffect(() => {
        if (!containerRef.current) return;
        d3.select(containerRef.current).selectAll("*").remove();
        if (chartData.length > 0) {
            drawBattlePlot(containerRef.current, chartData);
        }
    }, [chartData]);

    const availableTypes = Array.from(new Set(allShips.map((row) => row.ship_type)));
    const availableTiers = Array.from(new Set(allShips.map((row) => row.ship_tier))).sort((a, b) => b - a);

    const toggleType = (shipType: string) => {
        setSelectedTypes((current) =>
            current.includes(shipType)
                ? current.filter((value) => value !== shipType)
                : [...current, shipType]
        );
    };

    const toggleTier = (tier: number) => {
        setSelectedTiers((current) =>
            current.includes(tier)
                ? current.filter((value) => value !== tier)
                : [...current, tier]
        );
    };

    const getFreshnessStatus = (timestamp: string | null): 'fresh' | 'stale' | 'unknown' => {
        if (!timestamp) {
            return 'unknown';
        }

        const updatedAt = new Date(timestamp).getTime();
        if (Number.isNaN(updatedAt)) {
            return 'unknown';
        }

        const ageMs = Date.now() - updatedAt;
        return ageMs <= 24 * 60 * 60 * 1000 ? 'fresh' : 'stale';
    };

    const formatTimestamp = (timestamp: string | null): string => {
        if (!timestamp) {
            return 'unknown';
        }

        const parsed = new Date(timestamp);
        if (Number.isNaN(parsed.getTime())) {
            return 'unknown';
        }

        return parsed.toLocaleString();
    };

    const randomsFreshness = getFreshnessStatus(randomsUpdatedAt);

    const shouldGrayOut = isLoading || isChartLoading;

    return (
        <div>
            <div className="mb-2 text-xs text-gray-600">
                Randoms data last refreshed: {formatTimestamp(randomsUpdatedAt)}
                {' · '}
                <span className={randomsFreshness === 'fresh' ? 'text-green-700' : randomsFreshness === 'stale' ? 'text-red-700' : 'text-gray-500'}>
                    {randomsFreshness === 'fresh' ? 'fresh' : randomsFreshness === 'stale' ? 'stale' : 'unknown'}
                </span>
            </div>
            <div className="mb-2 text-sm">
                <div className="mb-1 font-semibold">Ship Type</div>
                <div className="flex flex-wrap gap-3">
                    {availableTypes.map((shipType) => (
                        <label key={shipType} className="flex items-center gap-1">
                            <input
                                type="checkbox"
                                checked={selectedTypes.includes(shipType)}
                                onChange={() => toggleType(shipType)}
                            />
                            <span>{shipType}</span>
                        </label>
                    ))}
                </div>
                <div className="mt-2 mb-1 font-semibold">Tier</div>
                <div className="flex flex-wrap gap-3">
                    {availableTiers.map((tier) => (
                        <label key={tier} className="flex items-center gap-1">
                            <input
                                type="checkbox"
                                checked={selectedTiers.includes(tier)}
                                onChange={() => toggleTier(tier)}
                            />
                            <span>{tier}</span>
                        </label>
                    ))}
                </div>
            </div>

            {chartData.length === 0 ? (
                <p className="text-sm text-gray-500">No ships match the selected filters.</p>
            ) : null}

            <div className="relative">
                <div
                    className={shouldGrayOut ? 'pointer-events-none opacity-60 grayscale transition' : 'transition'}
                    aria-busy={shouldGrayOut}
                >
                    <div ref={containerRef}></div>
                </div>
                {shouldGrayOut ? (
                    <div className="absolute inset-0 flex items-center justify-center rounded bg-gray-100/65">
                        <span className="rounded border border-gray-300 bg-white px-2 py-1 text-xs font-medium text-gray-600">
                            Loading random battles...
                        </span>
                    </div>
                ) : null}
            </div>
        </div>
    );
};

export default RandomsSVG;
