import React, { useEffect, useRef, useState } from 'react';
import { resolveContainerChartWidth, type ChartTheme } from '../lib/chartTheme';
import { drawSeasonTimeline, fractionalYear, type TimelineMark } from '../lib/seasonTimeline';
import { leagueOrderFrom } from '../lib/rankedLeagueGlyph';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson, isAbortError } from '../lib/sharedJsonFetch';
import { degradationMonitor } from '../lib/degradationMonitor';
import { usePlayerRequestSignal } from '../context/PlayerRequestScopeContext';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';

interface RankedSeasonRow {
    season_label: string;
    total_battles: number;
    win_rate: number; // 0..1 fraction
    start_date?: string | null;
    highest_league?: number;
    highest_league_name?: string;
}

interface RankedSeasonTimelineSVGProps {
    playerId: number;
    isLoading?: boolean;
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

// Played, dated seasons → timeline marks. Ranked win_rate is a 0..1 fraction,
// so scale to percent for the shared timeline.
const toMarks = (seasons: RankedSeasonRow[]): TimelineMark[] => seasons
    .filter((season) => (season.total_battles || 0) > 0)
    .map((season): TimelineMark | null => {
        const frac = fractionalYear(season.start_date);
        return frac == null ? null : {
            label: season.season_label,
            battles: season.total_battles,
            winRate: season.win_rate * 100,
            frac,
            leagueOrder: leagueOrderFrom(season.highest_league_name, season.highest_league),
        };
    })
    .filter((mark): mark is TimelineMark => mark !== null);

const RankedSeasonTimelineSVG: React.FC<RankedSeasonTimelineSVGProps> = ({
    playerId,
    isLoading = false,
    svgWidth = 600,
    svgHeight = 128,
    theme = 'light',
}) => {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const { realm } = useRealm();
    const requestSignal = usePlayerRequestSignal();
    const [seasons, setSeasons] = useState<RankedSeasonRow[] | null>(null);
    // True while the endpoint is still serving []+pending (cold cache).
    const [pending, setPending] = useState(true);

    useEffect(() => {
        if (isLoading) return undefined;
        let isMounted = true;
        let timeoutId: ReturnType<typeof setTimeout> | null = null;
        let pendingAttempts = 0;

        const requestRankedData = async (): Promise<{ data: RankedSeasonRow[]; pending: boolean } | null> => {
            for (let attempt = 0; attempt < 2; attempt += 1) {
                try {
                    const payload = await fetchSharedJson<RankedSeasonRow[]>(withRealm(`/api/fetch/ranked_data/${playerId}/`, realm), {
                        label: `Ranked data ${playerId}`,
                        ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                        signal: requestSignal,
                        cacheKey: `ranked-data:${realm}:${playerId}:${pendingAttempts}:${attempt}`,
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
                    setPending(false);
                    return;
                }
                setSeasons(result.data);
                if (result.pending && pendingAttempts < RANKED_PENDING_RETRY_LIMIT) {
                    setPending(true);
                    pendingAttempts += 1;
                    timeoutId = setTimeout(() => { void fetchData(); }, RANKED_PENDING_RETRY_DELAY_MS * degradationMonitor.getPollIntervalMultiplier());
                } else {
                    setPending(false);
                }
            } catch (err) {
                if (isAbortError(err) || !isMounted) return;
                setSeasons([]);
                setPending(false);
            }
        };

        void fetchData();
        return () => {
            isMounted = false;
            if (timeoutId) clearTimeout(timeoutId);
        };
    }, [playerId, realm, isLoading, requestSignal]);

    useEffect(() => {
        if (!containerRef.current) return undefined;
        const resolveWidth = () => resolveContainerChartWidth(containerRef.current?.clientWidth, svgWidth);
        const redraw = () => {
            if (!containerRef.current) return;
            if (seasons === null || (seasons.length === 0 && pending)) {
                drawSeasonTimeline(containerRef.current, [], resolveWidth(), svgHeight, theme, 'Loading ranked seasons…', true);
                return;
            }
            drawSeasonTimeline(containerRef.current, toMarks(seasons), resolveWidth(), svgHeight, theme, 'No dated ranked seasons to plot yet.', true);
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
            aria-label="Ranked season activity timeline by year"
        />
    );
};

export default RankedSeasonTimelineSVG;
