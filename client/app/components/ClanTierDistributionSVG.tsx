import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { chartColors, type ChartTheme } from '../lib/chartTheme';
import { useClanTiersDistribution, ClanTierData } from './useClanTiersDistribution';
import ErrorBoundary from './ErrorBoundary';
import LoadingPanel from './LoadingPanel';

interface ClanTierDistributionSVGProps {
    clanId: number;
    svgHeight?: number;
    theme?: ChartTheme;
}

type Colors = typeof chartColors['light'];

const romanNumerals = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI'];

const getRomanTier = (tier: number) => {
    if (tier >= 1 && tier <= 11) {
        return romanNumerals[tier - 1];
    }
    return String(tier);
};

const drawTierPlot = (container: HTMLDivElement, svgHeight: number, colors: Colors, data: ClanTierData[]) => {
    const containerWidth = Math.max(container.clientWidth || 0, 280);
    const compact = containerWidth < 420;

    d3.select(container).selectAll('*').remove();

    const totalSvgWidth = containerWidth;
    const totalSvgHeight = compact ? Math.min(svgHeight, 200) : svgHeight;
    const margin = compact
        ? { top: 20, right: 14, bottom: 28, left: 42 }
        : { top: 20, right: 24, bottom: 36, left: 52 };

    const width = totalSvgWidth - margin.left - margin.right;
    const height = totalSvgHeight - margin.top - margin.bottom;
    const axisFontSize = compact ? '9px' : '10px';

    const svgRoot = d3.select(container)
        .append('svg')
        .attr('width', totalSvgWidth)
        .attr('height', totalSvgHeight)
        .attr('viewBox', `0 0 ${totalSvgWidth} ${totalSvgHeight}`)
        .style('display', 'block')
        .style('max-width', '100%');

    const svg = svgRoot
        .append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    const renderData = () => {
        if (!data || data.length === 0) {
            return;
        }

        // Sort data 1 to 11
        const sortedData = [...data].sort((a, b) => a.ship_tier - b.ship_tier);

        const maxBattles = Math.max(d3.max(sortedData, (d: ClanTierData) => d.pvp_battles) || 0, 10);
        
        const x = d3.scaleBand()
            .range([0, width])
            .domain(sortedData.map(d => getRomanTier(d.ship_tier)))
            .padding(0.2);
            
        const y = d3.scaleLinear()
            .domain([0, maxBattles * 1.05])
            .range([height, 0]);

        svg.append('g')
            .attr('class', 'tier-x-axis')
            .attr('transform', `translate(0, ${height})`)
            .call(d3.axisBottom(x).tickSize(0).tickPadding(8))
            .selectAll('text')
            .style('font-size', axisFontSize)
            .style('fill', colors.axisText);

        svg.select('.tier-x-axis')?.select('.domain')?.remove();

        svg.append('g')
            .attr('class', 'tier-y-axis')
            .style('color', colors.labelMuted)
            .call(d3.axisLeft(y).ticks(5).tickFormat((value: number) => d3.format('~s')(value)).tickSize(-width))
            .selectAll('text')
            .style('font-size', axisFontSize)
            .style('fill', colors.axisText);
            
        svg.select('.tier-y-axis')?.select('.domain')?.remove();
        svg.selectAll('.tier-y-axis line')
            .style('stroke', colors.gridLine)
            .style('stroke-width', 1)
            .attr('stroke-dasharray', '2,2');

        const bars = svg.selectAll('.tier-bar')
            .data(sortedData)
            .enter()
            .append('rect')
            .classed('tier-bar', true)
            .attr('x', (d: ClanTierData) => x(getRomanTier(d.ship_tier)) ?? 0)
            .attr('y', (d: ClanTierData) => y(d.pvp_battles))
            .attr('width', x.bandwidth())
            .attr('height', (d: ClanTierData) => height - y(d.pvp_battles))
            .attr('fill', colors.axisLine)
            .attr('fill-opacity', 0.7)
            .attr('stroke', colors.axisLine)
            .attr('stroke-width', 1)
            .style('cursor', 'pointer');
            
        bars.append('title').text((d: ClanTierData) => `${d.pvp_battles.toLocaleString()} Total Battles`);
    };

    renderData();
};

interface InternalClanTierSVGProps extends ClanTierDistributionSVGProps {
    data: ClanTierData[];
}

const ClanTierSVG: React.FC<InternalClanTierSVGProps> = ({ clanId, svgHeight = 220, theme = 'light', data }) => {
    const containerRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        const container = containerRef.current;
        if (!container || !data || data.length === 0) {
            return;
        }

        const colors = chartColors[theme];
        const render = () => {
            drawTierPlot(container, svgHeight, colors, data);
        };

        render();
        window.addEventListener('resize', render);

        return () => {
            window.removeEventListener('resize', render);
        };
    }, [data, svgHeight, theme]);

    if (!data || data.length === 0) {
        return null;
    }

    return <div ref={containerRef} className="w-full"></div>;
};

// Wrapper with error boundary and loader
const ClanTierDistributionContainer: React.FC<ClanTierDistributionSVGProps> = (props) => {
    const { data, loading, error } = useClanTiersDistribution(props.clanId);
    
    if (error) {
        return <div className="p-4 text-sm text-red-500 bg-red-100 dark:bg-red-900/20 rounded-md">Tier data unavailable: {error}</div>;
    }
    
    if (loading) {
        return <LoadingPanel label="Aggregating clan tier distributions..." minHeight={props.svgHeight ?? 220} />;
    }
    
    return (
        <ErrorBoundary fallback={<div className="p-4 text-sm text-red-500">Tier data unavailable</div>}>
            <ClanTierSVG {...props} data={data} />
        </ErrorBoundary>
    );
};

export default ClanTierDistributionContainer;
