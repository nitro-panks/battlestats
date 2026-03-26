import React, { useEffect, useState } from 'react';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson } from '../lib/sharedJsonFetch';

interface RankedSeasonsProps {
    playerId: number;
    isLoading?: boolean;
}

const RANKED_TABLE_VISIBLE_ROWS = 8;
const RANKED_TABLE_HEADER_HEIGHT_REM = 2.5;
const RANKED_TABLE_ROW_HEIGHT_REM = 3.5;

interface RankedSprint {
    sprint_number: number;
    league: number;
    league_name: string;
    rank: number;
    best_rank: number;
    battles: number;
    wins: number;
}

interface RankedSeason {
    season_id: number;
    season_name: string;
    season_label: string;
    start_date: string | null;
    end_date: string | null;
    highest_league: number;
    highest_league_name: string;
    total_battles: number;
    total_wins: number;
    win_rate: number;
    top_ship_name?: string | null;
    best_sprint: RankedSprint | null;
    sprints: RankedSprint[];
}

type SortKey = 'season' | 'highestRank' | 'battles' | 'wins' | 'winRate';
type SortDirection = 'asc' | 'desc';

const leagueColors: Record<string, string> = {
    Gold: 'border-amber-300 bg-amber-50 text-amber-800',
    Silver: 'border-slate-300 bg-slate-50 text-slate-700',
    Bronze: 'border-orange-300 bg-orange-50 text-orange-800',
};

const formatWinRate = (winRate: number): string => `${(winRate * 100).toFixed(1)}%`;

const getWinRateColorClass = (winRate: number): string => {
    const percent = winRate * 100;

    if (percent >= 60) {
        return 'text-violet-700';
    }

    if (percent >= 56) {
        return 'text-sky-700';
    }

    if (percent >= 52) {
        return 'text-emerald-700';
    }

    if (percent >= 48) {
        return 'text-amber-700';
    }

    return 'text-rose-700';
};

const getRankOrderValue = (leagueName: string, fallbackValue: number): number => {
    const normalized = leagueName.trim().toLowerCase();
    const map: Record<string, number> = {
        bronze: 1,
        silver: 2,
        gold: 3,
        typhoon: 4,
        hurricane: 5,
    };

    return map[normalized] ?? fallbackValue;
};

const formatSeasonStartDate = (startDate: string | null): string => {
    if (startDate) {
        return startDate;
    }

    return 'Start date unavailable';
};

const RANKED_FETCH_RETRY_DELAY_MS = 350;
const RANKED_PENDING_RETRY_DELAY_MS = 1500;
const RANKED_PENDING_RETRY_LIMIT = 5;

const delay = (timeoutMs: number): Promise<void> => new Promise((resolve) => {
    window.setTimeout(resolve, timeoutMs);
});

const RankedSeasons: React.FC<RankedSeasonsProps> = ({ playerId, isLoading = false }) => {
    const [seasons, setSeasons] = useState<RankedSeason[]>([]);
    const [isChartLoading, setIsChartLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [isPendingRefresh, setIsPendingRefresh] = useState(false);
    const [sortKey, setSortKey] = useState<SortKey>('season');
    const [sortDirection, setSortDirection] = useState<SortDirection>('desc');

    useEffect(() => {
        let isMounted = true;
        let timeoutId: ReturnType<typeof setTimeout> | null = null;
        let pendingAttempts = 0;

        const requestRankedData = async (): Promise<{ data: RankedSeason[]; pending: boolean } | null> => {
            for (let attempt = 0; attempt < 2; attempt += 1) {
                try {
                    const payload = await fetchSharedJson<RankedSeason[]>(`/api/fetch/ranked_data/${playerId}/`, {
                        label: `Ranked data ${playerId}`,
                        ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                        cacheKey: `ranked-data:${playerId}:${pendingAttempts}:${attempt}`,
                        responseHeaders: ['X-Ranked-Pending'],
                    });

                    return {
                        data: payload.data,
                        pending: payload.headers['X-Ranked-Pending'] === 'true',
                    };
                } catch {
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
            setIsChartLoading(true);
            if (pendingAttempts === 0) {
                setError(null);
                setIsPendingRefresh(false);
            }

            try {
                const result = await requestRankedData();

                if (!isMounted) {
                    return;
                }

                if (result === null) {
                    setError('Unable to load ranked data right now.');
                    setSeasons([]);
                    setIsPendingRefresh(false);
                    return;
                }

                setSeasons(result.data.slice().sort((left, right) => right.season_id - left.season_id));
                setIsPendingRefresh(result.pending);

                if (result.pending && pendingAttempts < RANKED_PENDING_RETRY_LIMIT) {
                    pendingAttempts += 1;
                    timeoutId = setTimeout(() => {
                        void fetchData();
                    }, RANKED_PENDING_RETRY_DELAY_MS);
                    return;
                }
            } catch {
                if (!isMounted) {
                    return;
                }

                setError('Unable to load ranked data right now.');
                setSeasons([]);
                setIsPendingRefresh(false);
            } finally {
                if (isMounted && !timeoutId) {
                    setIsChartLoading(false);
                }
            }
        };

        void fetchData();

        return () => {
            isMounted = false;
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
        };
    }, [playerId]);

    const shouldGrayOut = isLoading || isChartLoading;
    const sortedSeasons = seasons.slice().sort((left, right) => {
        let comparison = 0;

        if (sortKey === 'season') {
            comparison = left.season_id - right.season_id;
        } else if (sortKey === 'highestRank') {
            comparison = getRankOrderValue(left.highest_league_name, left.highest_league) - getRankOrderValue(right.highest_league_name, right.highest_league);
        } else if (sortKey === 'battles') {
            comparison = left.total_battles - right.total_battles;
        } else if (sortKey === 'wins') {
            comparison = left.total_wins - right.total_wins;
        } else if (sortKey === 'winRate') {
            comparison = left.win_rate - right.win_rate;
        }

        return sortDirection === 'asc' ? comparison : -comparison;
    });
    const shouldShowTable = shouldGrayOut || sortedSeasons.length > 0;

    const handleSort = (nextKey: SortKey): void => {
        if (sortKey === nextKey) {
            setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
            return;
        }

        setSortKey(nextKey);
        setSortDirection(nextKey === 'season' ? 'desc' : 'asc');
    };

    const getSortMarker = (columnKey: SortKey): string => {
        if (sortKey !== columnKey) {
            return '↕';
        }

        return sortDirection === 'asc' ? '▲' : '▼';
    };

    return (
        <div>
            {error ? (
                <p className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                    {error}
                </p>
            ) : null}

            {isPendingRefresh && seasons.length === 0 && !error ? (
                <p className="mb-3 rounded-md border border-[#c6dbef] bg-[#f0f7ff] px-3 py-2 text-sm text-[#2171b5]">
                    Refreshing ranked seasons...
                </p>
            ) : null}

            {seasons.length === 0 && !shouldGrayOut && !error && !isPendingRefresh ? (
                <p className="text-sm text-gray-500">No ranked seasons found for this player.</p>
            ) : null}

            {shouldShowTable ? (
                <div className="relative">
                    <div className={shouldGrayOut ? 'pointer-events-none opacity-60 grayscale transition' : 'transition'} aria-busy={shouldGrayOut}>
                        <div className="overflow-x-auto rounded-lg border border-[#c6dbef] bg-white">
                            <div
                                className="overflow-y-auto"
                                style={{
                                    maxHeight: `calc(${RANKED_TABLE_HEADER_HEIGHT_REM}rem + (${RANKED_TABLE_VISIBLE_ROWS} * ${RANKED_TABLE_ROW_HEIGHT_REM}rem))`,
                                }}
                            >
                                <table className="min-w-full divide-y divide-[#dbe9f6] text-sm">
                                    <thead className="sticky top-0 bg-[#f0f7ff]">
                                        <tr>
                                            <th scope="col" className="px-3 py-2 text-left text-xs font-semibold tracking-wide text-[#2171b5]">
                                                <button type="button" className="inline-flex items-center gap-1" onClick={() => handleSort('season')}>
                                                    Season <span aria-hidden="true">{getSortMarker('season')}</span>
                                                </button>
                                            </th>
                                            <th scope="col" className="px-3 py-2 text-left text-xs font-semibold tracking-wide text-[#2171b5]">
                                                <button type="button" className="inline-flex items-center gap-1" onClick={() => handleSort('highestRank')}>
                                                    Highest Rank <span aria-hidden="true">{getSortMarker('highestRank')}</span>
                                                </button>
                                            </th>
                                            <th scope="col" className="px-3 py-2 text-left text-xs font-semibold tracking-wide text-[#2171b5]">
                                                Top Ship
                                            </th>
                                            <th scope="col" className="px-3 py-2 text-right text-xs font-semibold tracking-wide text-[#2171b5]">
                                                <button type="button" className="ml-auto inline-flex items-center gap-1" onClick={() => handleSort('battles')}>
                                                    Battles <span aria-hidden="true">{getSortMarker('battles')}</span>
                                                </button>
                                            </th>
                                            <th scope="col" className="px-3 py-2 text-right text-xs font-semibold tracking-wide text-[#2171b5]">
                                                <button type="button" className="ml-auto inline-flex items-center gap-1" onClick={() => handleSort('wins')}>
                                                    Wins <span aria-hidden="true">{getSortMarker('wins')}</span>
                                                </button>
                                            </th>
                                            <th scope="col" className="px-3 py-2 text-right text-xs font-semibold tracking-wide text-[#2171b5]">
                                                <button type="button" className="ml-auto inline-flex items-center gap-1" onClick={() => handleSort('winRate')}>
                                                    WR <span aria-hidden="true">{getSortMarker('winRate')}</span>
                                                </button>
                                            </th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-[#edf4fb]">
                                        {sortedSeasons.map((season) => {
                                            const badgeClassName = leagueColors[season.highest_league_name] || leagueColors.Bronze;

                                            return (
                                                <tr key={season.season_id} className="align-top">
                                                    <td className="px-3 py-3 text-[#084594]">
                                                        <p className="font-semibold">{season.season_label}</p>
                                                        <p className="mt-1 text-xs text-[#6baed6]">{formatSeasonStartDate(season.start_date)}</p>
                                                    </td>
                                                    <td className="px-3 py-3">
                                                        <span className={`inline-flex rounded-full border px-2 py-1 text-xs font-semibold ${badgeClassName}`}>
                                                            {season.highest_league_name}
                                                        </span>
                                                    </td>
                                                    <td className="px-3 py-3 text-[#084594]">
                                                        <span className="font-medium">{season.top_ship_name || '—'}</span>
                                                    </td>
                                                    <td className="px-3 py-3 text-right font-medium text-[#084594]">{season.total_battles.toLocaleString()}</td>
                                                    <td className="px-3 py-3 text-right font-medium text-[#084594]">{season.total_wins.toLocaleString()}</td>
                                                    <td className={`px-3 py-3 text-right font-semibold ${getWinRateColorClass(season.win_rate)}`}>{formatWinRate(season.win_rate)}</td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                    {shouldGrayOut ? (
                        <div className="absolute inset-0 flex items-center justify-center rounded bg-gray-100/65">
                            <span className="rounded border border-gray-300 bg-white px-2 py-1 text-xs font-medium text-gray-600">
                                Loading ranked data...
                            </span>
                        </div>
                    ) : null}
                </div>
            ) : null}
        </div>
    );
};

export default RankedSeasons;