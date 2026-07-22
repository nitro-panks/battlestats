import React, { useEffect, useRef, useState } from 'react';
import * as d3 from 'd3';
import { chartColors, drawSvgMessage, resolveContainerChartWidth, type ChartTheme } from '../lib/chartTheme';
import { leagueOrderFrom, leagueSymbol, leagueStroke, leagueInnerBorderColor } from '../lib/rankedLeagueGlyph';
import wrColor from '../lib/wrColor';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson, isAbortError } from '../lib/sharedJsonFetch';
import { degradationMonitor } from '../lib/degradationMonitor';
import { usePlayerRequestSignal } from '../context/PlayerRequestScopeContext';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';

// One ranked season, trimmed to the fields the scatter plots. Mirrors the
// RankedSeasons table's payload (same /api/fetch/ranked_data endpoint).
interface RankedSeasonPoint {
    season_id: number;
    season_label: string;
    total_battles: number;
    win_rate: number; // 0..1 fraction
    highest_league?: number;
    highest_league_name?: string;
    start_date?: string | null;
}

// First 4-digit run in the season's start date (ISO "YYYY-…" or similar).
const seasonYear = (startDate?: string | null): string | null => {
    const match = /(\d{4})/.exec(startDate ?? '');
    return match ? match[1] : null;
};

// League glyphs (shape/border/inner-hairline) live in ../lib/rankedLeagueGlyph
// so the ranked scatter + timeline stay identical. These thin wrappers adapt a
// RankedSeasonPoint to those helpers.
const leagueOrder = (season: RankedSeasonPoint): number => leagueOrderFrom(season.highest_league_name, season.highest_league);
const seasonSymbol = (season: RankedSeasonPoint) => leagueSymbol(leagueOrder(season));

interface RankedSeasonScatterSVGProps {
    playerId: number;
    isLoading?: boolean;
    // 600 matches the heatmap default; the real width is the container's, so the
    // scatter and heatmap resolve to the SAME width and compact breakpoint.
    svgWidth?: number;
    svgHeight?: number;
    theme?: ChartTheme;
}

const RANKED_FETCH_RETRY_DELAY_MS = 350;
const RANKED_PENDING_RETRY_DELAY_MS = 1500;
const RANKED_PENDING_RETRY_LIMIT = 12;

const delay = (timeoutMs: number): Promise<void> => new Promise((resolve) => {
    window.setTimeout(resolve, timeoutMs);
});

// y-domain: pad the observed WR range out to 5-point gridlines, clamp to
// [0,100], and hold a minimum 15-point span so a single season (or two nearly
// equal ones) doesn't get its spread stretched across the whole height.
const winRateDomain = (wrValues: number[]): [number, number] => {
    const minWR = Math.min(...wrValues);
    const maxWR = Math.max(...wrValues);
    let lo = Math.max(0, Math.floor((minWR - 4) / 5) * 5);
    let hi = Math.min(100, Math.ceil((maxWR + 4) / 5) * 5);
    if (hi - lo < 15) {
        const mid = (lo + hi) / 2;
        lo = Math.max(0, Math.floor((mid - 7.5) / 5) * 5);
        hi = Math.min(100, lo + 15);
        if (hi - lo < 15) lo = Math.max(0, hi - 15);
    }
    return [lo, hi];
};

const drawChart = (
    container: HTMLDivElement,
    seasons: RankedSeasonPoint[],
    svgWidth: number,
    svgHeight: number,
    theme: ChartTheme,
): void => {
    const colors = chartColors[theme];
    const plot = seasons.filter((season) => (season.total_battles || 0) > 0);

    d3.select(container).selectAll('*').remove();
    if (plot.length === 0) {
        drawSvgMessage(container, 'No ranked seasons to plot yet.', { width: svgWidth, height: 120, color: colors.labelMuted });
        return;
    }

    // margin.left MUST match the heatmap (52 / 38) so the two y-axes line up;
    // right also matches so the plots share a right edge. compact uses the same
    // svgWidth < 480 threshold the heatmap uses.
    const compact = svgWidth < 480;
    const margin = compact
        ? { top: 16, right: 8, bottom: 40, left: 38 }
        : { top: 20, right: 18, bottom: 46, left: 52 };
    const axisFontSize = compact ? '9px' : '10px';
    const width = svgWidth - margin.left - margin.right;
    const height = svgHeight - margin.top - margin.bottom;

    const svgRoot = d3.select(container).append('svg')
        .attr('width', svgWidth)
        .attr('height', svgHeight);
    const svg = svgRoot.append('g')
        .attr('transform', `translate(${margin.left}, ${margin.top})`);

    const battlesVals = plot.map((season) => season.total_battles);
    const wrVals = plot.map((season) => season.win_rate * 100);
    const maxBattles = Math.max(...battlesVals);

    // x = battles, linear from 0 so the axis reads honestly; pad the top so the
    // busiest season's dot doesn't sit on the right edge.
    const x = d3.scaleLinear()
        .domain([0, Math.max(maxBattles * 1.08, maxBattles + 1)])
        .range([0, width]);
    const [yLo, yHi] = winRateDomain(wrVals);
    const y = d3.scaleLinear().domain([yLo, yHi]).range([height, 0]);

    // Gridlines (y) for reading WR bands.
    svg.append('g')
        .selectAll('line')
        .data(y.ticks(compact ? 3 : 5))
        .enter()
        .append('line')
        .attr('x1', 0).attr('x2', width)
        .attr('y1', (tick: number) => y(tick)).attr('y2', (tick: number) => y(tick))
        .attr('stroke', colors.gridLine)
        .attr('stroke-width', 1)
        .attr('stroke-opacity', 0.35);

    // Axes.
    svg.append('g')
        .style('color', colors.labelText)
        .attr('transform', `translate(0, ${height})`)
        .call(d3.axisBottom(x).ticks(compact ? 3 : 5, '~s').tickSizeOuter(0))
        .selectAll('text')
        .style('font-size', axisFontSize);
    svg.append('g')
        .style('color', colors.labelText)
        .call(d3.axisLeft(y).ticks(compact ? 3 : 5).tickSizeOuter(0).tickFormat((tick: number) => `${tick}%`))
        .selectAll('text')
        .style('font-size', axisFontSize);

    // Axis titles.
    svg.append('text')
        .attr('x', width / 2).attr('y', height + (compact ? 32 : 36))
        .attr('text-anchor', 'middle')
        .style('font-size', axisFontSize)
        .style('fill', colors.labelMuted)
        .text('Ranked Battles');
    svg.append('text')
        .attr('transform', 'rotate(-90)')
        .attr('x', -height / 2).attr('y', compact ? -28 : -40)
        .attr('text-anchor', 'middle')
        .style('font-size', axisFontSize)
        .style('fill', colors.labelMuted)
        .text('Win Rate');

    // Hover detail sits in the top margin, left-aligned with the y-axis, so it
    // never collides with the points.
    const detail = svg.append('g')
        .attr('class', 'hover-detail')
        .attr('transform', `translate(${width}, ${compact ? -14 : -16})`)
        .style('opacity', 0)
        .style('pointer-events', 'none');
    const detailText = detail.append('text').attr('x', 0).attr('y', 0)
        .attr('dominant-baseline', 'hanging').attr('text-anchor', 'end');

    const showDetail = (season: RankedSeasonPoint) => {
        detailText.selectAll('*').remove();
        detailText.append('tspan')
            .style('font-size', '14px').attr('font-weight', '700').style('fill', colors.accentLink)
            .text(season.season_label);
        const year = seasonYear(season.start_date);
        if (year) {
            detailText.append('tspan')
                .attr('dx', 8).style('font-size', '13px').style('fill', colors.labelMuted)
                .text(year);
        }
        if (season.highest_league_name) {
            detailText.append('tspan')
                .attr('dx', 12).style('font-size', '13px').style('fill', colors.labelText)
                .text(season.highest_league_name);
        }
        detailText.append('tspan')
            .attr('dx', 12).style('font-size', '13px').style('fill', colors.labelText)
            .text(`${season.total_battles.toLocaleString()} Battles`);
        detailText.append('tspan')
            .attr('dx', 12).style('font-size', '13px').style('fill', colors.labelText)
            .text(`${(season.win_rate * 100).toFixed(1)}% WR`);
        detail.style('opacity', 1);
    };

    // One symbol path per season: shape by league, fill by WR. The base
    // translate is kept on a data attribute so hover can re-apply it with a
    // scale (a symbol has no radius to bump like a circle did).
    const symbolGen = d3.symbol();
    const pointTransform = (season: RankedSeasonPoint) => `translate(${x(season.total_battles)}, ${y(season.win_rate * 100)}) rotate(${seasonSymbol(season).rotate})`;

    // Border encodes league metal: 1px silver on the Silver squares, 1px gold on
    // the Gold+ stars; Bronze/unknown keeps the neutral card-bg contrast ring.
    const strokeFor = (season: RankedSeasonPoint) => leagueStroke(leagueOrder(season), colors);

    // Hairline drawn just inside the metal border of the Silver/Gold glyphs so
    // the metal ring reads against the fill; black in dark mode, white in light.
    const innerBorderColor = leagueInnerBorderColor(theme);

    // One group per season so the metal-bordered glyph and its inner hairline
    // scale together on hover.
    const points = svg.append('g')
        .selectAll('g')
        .data(plot)
        .enter()
        .append('g')
        .attr('transform', pointTransform)
        .style('cursor', 'pointer');

    points.append('path')
        .attr('d', (season: RankedSeasonPoint) => {
            const { type, size } = seasonSymbol(season);
            return symbolGen.type(type).size(size)();
        })
        .attr('fill', (season: RankedSeasonPoint) => wrColor(season.win_rate * 100))
        .attr('stroke', (season: RankedSeasonPoint) => strokeFor(season).color)
        .attr('stroke-width', (season: RankedSeasonPoint) => strokeFor(season).width);

    // Inner 1px hairline (Silver squares + Gold+ stars only), ~81% of the glyph
    // radius so it sits just inside the metal border.
    points.filter((season: RankedSeasonPoint) => leagueOrder(season) >= 2)
        .append('path')
        .attr('d', (season: RankedSeasonPoint) => {
            const { type, size } = seasonSymbol(season);
            return symbolGen.type(type).size(size * 0.66)();
        })
        .attr('fill', 'none')
        .attr('stroke', innerBorderColor)
        .attr('stroke-width', 1)
        .style('pointer-events', 'none');

    points.append('title')
        .text((season: RankedSeasonPoint) => {
            const league = season.highest_league_name ? `${season.highest_league_name} · ` : '';
            return `${season.season_label}: ${league}${season.total_battles.toLocaleString()} battles, ${(season.win_rate * 100).toFixed(1)}% WR`;
        });

    points
        .on('mouseover', function onOver(this: SVGGElement, _event: MouseEvent, season: RankedSeasonPoint) {
            // Keep the league metal border; the scale is the hover emphasis.
            d3.select(this).attr('transform', `${pointTransform(season)} scale(1.35)`);
            showDetail(season);
        })
        .on('mouseout', function onOut(this: SVGGElement, _event: MouseEvent, season: RankedSeasonPoint) {
            d3.select(this).attr('transform', pointTransform(season));
            detail.style('opacity', 0);
        });
};

const RankedSeasonScatterSVG: React.FC<RankedSeasonScatterSVGProps> = ({
    playerId,
    isLoading = false,
    svgWidth = 600,
    svgHeight = 240,
    theme = 'light',
}) => {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const { realm } = useRealm();
    const requestSignal = usePlayerRequestSignal();
    const [seasons, setSeasons] = useState<RankedSeasonPoint[] | null>(null);

    // Fetch ranked seasons (same endpoint + pending-retry as the RankedSeasons
    // table; fetchSharedJson dedups the two callers so it's one request).
    useEffect(() => {
        if (isLoading) return undefined;
        let isMounted = true;
        let timeoutId: ReturnType<typeof setTimeout> | null = null;
        let pendingAttempts = 0;

        const requestRankedData = async (): Promise<{ data: RankedSeasonPoint[]; pending: boolean } | null> => {
            for (let attempt = 0; attempt < 2; attempt += 1) {
                try {
                    const payload = await fetchSharedJson<RankedSeasonPoint[]>(withRealm(`/api/fetch/ranked_data/${playerId}/`, realm), {
                        label: `Ranked data ${playerId}`,
                        ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                        signal: requestSignal,
                        cacheKey: `ranked-data:${playerId}:${pendingAttempts}:${attempt}`,
                        responseHeaders: ['X-Ranked-Pending'],
                    });
                    return { data: payload.data, pending: payload.headers['X-Ranked-Pending'] === 'true' };
                } catch (err) {
                    if (isAbortError(err)) throw err;
                    if (attempt === 0) {
                        await delay(RANKED_FETCH_RETRY_DELAY_MS);
                        continue;
                    }
                }
            }
            return null;
        };

        const fetchData = async () => {
            timeoutId = null;
            try {
                const result = await requestRankedData();
                if (!isMounted) return;
                if (result === null) {
                    setSeasons([]);
                    return;
                }
                setSeasons(result.data);
                if (result.pending && pendingAttempts < RANKED_PENDING_RETRY_LIMIT) {
                    pendingAttempts += 1;
                    timeoutId = setTimeout(() => { void fetchData(); }, RANKED_PENDING_RETRY_DELAY_MS * degradationMonitor.getPollIntervalMultiplier());
                }
            } catch (err) {
                if (isAbortError(err) || !isMounted) return;
                setSeasons([]);
            }
        };

        void fetchData();
        return () => {
            isMounted = false;
            if (timeoutId) clearTimeout(timeoutId);
        };
    }, [playerId, realm, isLoading, requestSignal]);

    // Draw on data/theme/size change, and redraw on resize so the axis keeps
    // filling the container (staying aligned with the heatmap above).
    useEffect(() => {
        if (seasons === null || !containerRef.current) return undefined;
        const resolveWidth = () => resolveContainerChartWidth(containerRef.current?.clientWidth, svgWidth);

        const redraw = () => {
            if (containerRef.current) {
                drawChart(containerRef.current, seasons, resolveWidth(), svgHeight, theme);
            }
        };
        redraw();

        let resizeFrame: number | null = null;
        const onResize = () => {
            if (resizeFrame != null) cancelAnimationFrame(resizeFrame);
            resizeFrame = requestAnimationFrame(redraw);
        };
        window.addEventListener('resize', onResize);
        return () => {
            window.removeEventListener('resize', onResize);
            if (resizeFrame != null) cancelAnimationFrame(resizeFrame);
        };
    }, [seasons, theme, svgHeight, svgWidth]);

    return (
        <div
            ref={containerRef}
            className="w-full overflow-hidden rounded-md bg-[var(--bg-surface)]"
            role="img"
            aria-label="Ranked win rate versus battles played, one point per season"
        />
    );
};

export default RankedSeasonScatterSVG;
