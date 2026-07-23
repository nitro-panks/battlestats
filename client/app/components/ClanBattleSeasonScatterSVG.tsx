import React, { useEffect, useRef, useState } from 'react';
import * as d3 from 'd3';
import { chartColors, drawSvgMessage, resolveContainerChartWidth, type ChartTheme } from '../lib/chartTheme';
import wrColor from '../lib/wrColor';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson, isAbortError } from '../lib/sharedJsonFetch';
import { degradationMonitor } from '../lib/degradationMonitor';
import { usePlayerRequestSignal } from '../context/PlayerRequestScopeContext';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';

// One clan-battle season, trimmed to what the scatter plots. Mirrors the
// PlayerClanBattleSeasons payload (same /api/fetch/player_clan_battle_seasons
// endpoint). NOTE: win_rate here is already a PERCENTAGE (0..100), unlike the
// ranked payload's 0..1 fraction — so no ×100.
interface ClanBattleSeasonPoint {
    season_id: number;
    season_label: string;
    battles: number;
    win_rate: number; // percent
    start_date?: string | null;
}

// First 4-digit run in the season's start date (ISO "YYYY-…" or similar).
const seasonYear = (startDate?: string | null): string | null => {
    const match = /(\d{4})/.exec(startDate ?? '');
    return match ? match[1] : null;
};

interface ClanBattleSeasonScatterSVGProps {
    playerId: number;
    isLoading?: boolean;
    svgWidth?: number;
    svgHeight?: number;
    theme?: ChartTheme;
}

const CB_SEASONS_PENDING_RETRY_DELAY_MS = 1500;
const CB_SEASONS_PENDING_RETRY_LIMIT = 12;

// y-domain: pad the observed WR range to 5-point gridlines, clamp to [0,100],
// and hold a 15-point minimum span so a single (or two near-equal) season(s)
// don't get their spread stretched across the whole height.
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
    seasons: ClanBattleSeasonPoint[],
    svgWidth: number,
    svgHeight: number,
    theme: ChartTheme,
    emptyMessage: string,
): void => {
    const colors = chartColors[theme];
    const plot = seasons.filter((season) => (season.battles || 0) > 0);

    d3.select(container).selectAll('*').remove();
    if (plot.length === 0) {
        drawSvgMessage(container, emptyMessage, { width: svgWidth, height: 120, color: colors.labelMuted });
        return;
    }

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

    const maxBattles = Math.max(...plot.map((season) => season.battles));
    const x = d3.scaleLinear()
        .domain([0, Math.max(maxBattles * 1.08, maxBattles + 1)])
        .range([0, width]);
    const [yLo, yHi] = winRateDomain(plot.map((season) => season.win_rate));
    const y = d3.scaleLinear().domain([yLo, yHi]).range([height, 0]);

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

    svg.append('text')
        .attr('x', width / 2).attr('y', height + (compact ? 32 : 36))
        .attr('text-anchor', 'middle')
        .style('font-size', axisFontSize)
        .style('fill', colors.labelMuted)
        .text('Clan Battles');
    svg.append('text')
        .attr('transform', 'rotate(-90)')
        .attr('x', -height / 2).attr('y', compact ? -28 : -40)
        .attr('text-anchor', 'middle')
        .style('font-size', axisFontSize)
        .style('fill', colors.labelMuted)
        .text('Win Rate');

    const detail = svg.append('g')
        .attr('class', 'hover-detail')
        .attr('transform', `translate(${width}, ${compact ? -14 : -16})`)
        .style('opacity', 0)
        .style('pointer-events', 'none');
    const detailText = detail.append('text').attr('x', 0).attr('y', 0)
        .attr('dominant-baseline', 'hanging').attr('text-anchor', 'end');

    const showDetail = (season: ClanBattleSeasonPoint) => {
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
        detailText.append('tspan')
            .attr('dx', 12).style('font-size', '13px').style('fill', colors.labelText)
            .text(`${season.battles.toLocaleString()} Battles`);
        detailText.append('tspan')
            .attr('dx', 12).style('font-size', '13px').style('fill', colors.labelText)
            .text(`${season.win_rate.toFixed(1)}% WR`);
        detail.style('opacity', 1);
    };

    const dots = svg.append('g')
        .selectAll('circle')
        .data(plot)
        .enter()
        .append('circle')
        .attr('cx', (season: ClanBattleSeasonPoint) => x(season.battles))
        .attr('cy', (season: ClanBattleSeasonPoint) => y(season.win_rate))
        .attr('r', 5)
        .attr('fill', (season: ClanBattleSeasonPoint) => wrColor(season.win_rate))
        .attr('stroke', colors.barBg)
        .attr('stroke-width', 1.5)
        .style('cursor', 'pointer');

    dots.append('title')
        .text((season: ClanBattleSeasonPoint) => `${season.season_label}: ${season.battles.toLocaleString()} battles, ${season.win_rate.toFixed(1)}% WR`);

    dots
        .on('mouseover', function onOver(this: SVGCircleElement, _event: MouseEvent, season: ClanBattleSeasonPoint) {
            d3.select(this).attr('r', 7).attr('stroke', colors.labelText);
            showDetail(season);
        })
        .on('mouseout', function onOut(this: SVGCircleElement) {
            d3.select(this).attr('r', 5).attr('stroke', colors.barBg);
            detail.style('opacity', 0);
        });
};

const ClanBattleSeasonScatterSVG: React.FC<ClanBattleSeasonScatterSVGProps> = ({
    playerId,
    isLoading = false,
    svgWidth = 600,
    svgHeight = 240,
    theme = 'light',
}) => {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const { realm } = useRealm();
    const requestSignal = usePlayerRequestSignal();
    const [seasons, setSeasons] = useState<ClanBattleSeasonPoint[] | null>(null);
    // True while the endpoint is still serving []+pending (cold cache, background
    // WG fetch). Distinguishes "loading" from a genuine settled-empty.
    const [pending, setPending] = useState(true);

    // Fetch CB seasons (same endpoint + pending-retry as PlayerClanBattleSeasons;
    // fetchSharedJson dedups the two callers into one request).
    useEffect(() => {
        if (isLoading) return undefined;
        let cancelled = false;
        let timeoutId: ReturnType<typeof setTimeout> | null = null;
        let pendingAttempts = 0;

        const fetchSeasons = async () => {
            timeoutId = null;
            try {
                const { data, headers } = await fetchSharedJson<unknown>(withRealm(`/api/fetch/player_clan_battle_seasons/${playerId}/`, realm), {
                    label: `Player clan battle seasons ${playerId}`,
                    ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                    signal: requestSignal,
                    cacheKey: `clan-cb-seasons:${realm}:${playerId}:${pendingAttempts}`,
                    responseHeaders: ['X-Clan-Battle-Seasons-Pending'],
                });
                if (cancelled) return;
                setSeasons(Array.isArray(data) ? (data as ClanBattleSeasonPoint[]) : []);
                const isPending = headers['X-Clan-Battle-Seasons-Pending'] === 'true';
                if (isPending && pendingAttempts < CB_SEASONS_PENDING_RETRY_LIMIT) {
                    setPending(true);
                    pendingAttempts += 1;
                    timeoutId = setTimeout(() => { void fetchSeasons(); }, CB_SEASONS_PENDING_RETRY_DELAY_MS * degradationMonitor.getPollIntervalMultiplier());
                } else {
                    setPending(false);
                }
            } catch (err) {
                if (isAbortError(err) || cancelled) return;
                setSeasons([]);
                setPending(false);
            }
        };

        void fetchSeasons();
        return () => {
            cancelled = true;
            if (timeoutId) clearTimeout(timeoutId);
        };
    }, [playerId, realm, isLoading, requestSignal]);

    useEffect(() => {
        if (!containerRef.current) return undefined;
        const resolveWidth = () => resolveContainerChartWidth(containerRef.current?.clientWidth, svgWidth);
        const redraw = () => {
            if (!containerRef.current) return;
            // Still loading (no data yet, or []+pending): show a quiet loading
            // note rather than the "no seasons" empty state.
            if (seasons === null || (seasons.length === 0 && pending)) {
                drawChart(containerRef.current, [], resolveWidth(), svgHeight, theme, 'Loading clan battle seasons…');
                return;
            }
            drawChart(containerRef.current, seasons, resolveWidth(), svgHeight, theme, 'No clan battle seasons to plot yet.');
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
    }, [seasons, pending, theme, svgHeight, svgWidth]);

    return (
        <div
            ref={containerRef}
            className="w-full overflow-hidden rounded-md bg-[var(--bg-surface)]"
            role="img"
            aria-label="Clan battle win rate versus battles played, one point per season"
        />
    );
};

export default ClanBattleSeasonScatterSVG;
