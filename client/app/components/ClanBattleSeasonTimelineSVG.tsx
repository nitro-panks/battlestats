import React, { useEffect, useRef, useState } from 'react';
import { resolveContainerChartWidth, type ChartTheme } from '../lib/chartTheme';
import { drawSeasonTimeline, fractionalYear, type TimelineMark } from '../lib/seasonTimeline';
import { PLAYER_ROUTE_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson, isAbortError } from '../lib/sharedJsonFetch';
import { degradationMonitor } from '../lib/degradationMonitor';
import { usePlayerRequestSignal } from '../context/PlayerRequestScopeContext';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';

interface ClanBattleSeasonRow {
    season_label: string;
    battles: number;
    win_rate: number; // percent
    start_date?: string | null;
}

interface ClanBattleSeasonTimelineSVGProps {
    playerId: number;
    isLoading?: boolean;
    svgWidth?: number;
    svgHeight?: number;
    theme?: ChartTheme;
}

const CB_SEASONS_PENDING_RETRY_DELAY_MS = 1500;
const CB_SEASONS_PENDING_RETRY_LIMIT = 12;

// Played, dated seasons → timeline marks. win_rate is already a percent here.
const toMarks = (seasons: ClanBattleSeasonRow[]): TimelineMark[] => seasons
    .filter((season) => (season.battles || 0) > 0)
    .map((season) => {
        const frac = fractionalYear(season.start_date);
        return frac == null ? null : { label: season.season_label, battles: season.battles, winRate: season.win_rate, frac };
    })
    .filter((mark): mark is TimelineMark => mark !== null);

const ClanBattleSeasonTimelineSVG: React.FC<ClanBattleSeasonTimelineSVGProps> = ({
    playerId,
    isLoading = false,
    svgWidth = 600,
    svgHeight = 128,
    theme = 'light',
}) => {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const { realm } = useRealm();
    const requestSignal = usePlayerRequestSignal();
    const [seasons, setSeasons] = useState<ClanBattleSeasonRow[] | null>(null);

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
                    ttlMs: PLAYER_ROUTE_FETCH_TTL_MS,
                    signal: requestSignal,
                    cacheKey: `clan-cb-seasons:${playerId}:${pendingAttempts}`,
                    responseHeaders: ['X-Clan-Battle-Seasons-Pending'],
                });
                if (cancelled) return;
                setSeasons(Array.isArray(data) ? (data as ClanBattleSeasonRow[]) : []);
                if (headers['X-Clan-Battle-Seasons-Pending'] === 'true' && pendingAttempts < CB_SEASONS_PENDING_RETRY_LIMIT) {
                    pendingAttempts += 1;
                    timeoutId = setTimeout(() => { void fetchSeasons(); }, CB_SEASONS_PENDING_RETRY_DELAY_MS * degradationMonitor.getPollIntervalMultiplier());
                }
            } catch (err) {
                if (isAbortError(err) || cancelled) return;
                setSeasons([]);
            }
        };

        void fetchSeasons();
        return () => {
            cancelled = true;
            if (timeoutId) clearTimeout(timeoutId);
        };
    }, [playerId, realm, isLoading, requestSignal]);

    useEffect(() => {
        if (seasons === null || !containerRef.current) return undefined;
        const marks = toMarks(seasons);
        const resolveWidth = () => resolveContainerChartWidth(containerRef.current?.clientWidth, svgWidth);
        const redraw = () => {
            if (containerRef.current) {
                drawSeasonTimeline(containerRef.current, marks, resolveWidth(), svgHeight, theme, 'No dated clan battle seasons to plot yet.');
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
            aria-label="Clan battle season activity timeline by year"
        />
    );
};

export default ClanBattleSeasonTimelineSVG;
