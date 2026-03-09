import React, { useEffect, useState } from 'react';

interface RankedSeasonsProps {
    playerId: number;
    isLoading?: boolean;
}

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

const RankedSeasons: React.FC<RankedSeasonsProps> = ({ playerId, isLoading = false }) => {
    const [seasons, setSeasons] = useState<RankedSeason[]>([]);
    const [isChartLoading, setIsChartLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [sortKey, setSortKey] = useState<SortKey>('season');
    const [sortDirection, setSortDirection] = useState<SortDirection>('desc');

    useEffect(() => {
        let isMounted = true;

        const fetchData = async () => {
            setIsChartLoading(true);
            setError(null);

            try {
                const response = await fetch(`http://localhost:8888/api/fetch/ranked_data/${playerId}/`);
                if (!response.ok) {
                    throw new Error(`Failed to fetch ranked data for ${playerId}`);
                }

                const result: RankedSeason[] = await response.json();
                if (!isMounted) {
                    return;
                }

                setSeasons(result.slice().sort((left, right) => right.season_id - left.season_id));
            } catch (fetchError) {
                if (!isMounted) {
                    return;
                }

                console.error('Error fetching ranked data:', fetchError);
                setError('Unable to load ranked data right now.');
                setSeasons([]);
            } finally {
                if (isMounted) {
                    setIsChartLoading(false);
                }
            }
        };

        fetchData();

        return () => {
            isMounted = false;
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
    const visibleSeasons = sortedSeasons.slice(0, 6);
    const shouldShowTable = shouldGrayOut || visibleSeasons.length > 0;

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

            {seasons.length === 0 && !shouldGrayOut && !error ? (
                <p className="text-sm text-gray-500">No ranked seasons found for this player.</p>
            ) : null}

            {shouldShowTable ? (
                <div className="relative">
                    <div className={shouldGrayOut ? 'pointer-events-none opacity-60 grayscale transition' : 'transition'} aria-busy={shouldGrayOut}>
                        <div className="overflow-x-auto rounded-lg border border-[#c6dbef] bg-white">
                            <div className="max-h-[21rem] overflow-y-auto">
                                <table className="min-w-full divide-y divide-[#dbe9f6] text-sm">
                                    <thead className="sticky top-0 bg-[#f0f7ff]">
                                        <tr>
                                            <th scope="col" className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-[#2171b5]">
                                                <button type="button" className="inline-flex items-center gap-1" onClick={() => handleSort('season')}>
                                                    Season <span aria-hidden="true">{getSortMarker('season')}</span>
                                                </button>
                                            </th>
                                            <th scope="col" className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-[#2171b5]">
                                                <button type="button" className="inline-flex items-center gap-1" onClick={() => handleSort('highestRank')}>
                                                    Highest Rank <span aria-hidden="true">{getSortMarker('highestRank')}</span>
                                                </button>
                                            </th>
                                            <th scope="col" className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-[#2171b5]">
                                                Top Ship
                                            </th>
                                            <th scope="col" className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-[#2171b5]">
                                                <button type="button" className="ml-auto inline-flex items-center gap-1" onClick={() => handleSort('battles')}>
                                                    Battles <span aria-hidden="true">{getSortMarker('battles')}</span>
                                                </button>
                                            </th>
                                            <th scope="col" className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-[#2171b5]">
                                                <button type="button" className="ml-auto inline-flex items-center gap-1" onClick={() => handleSort('wins')}>
                                                    Wins <span aria-hidden="true">{getSortMarker('wins')}</span>
                                                </button>
                                            </th>
                                            <th scope="col" className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-[#2171b5]">
                                                <button type="button" className="ml-auto inline-flex items-center gap-1" onClick={() => handleSort('winRate')}>
                                                    WR <span aria-hidden="true">{getSortMarker('winRate')}</span>
                                                </button>
                                            </th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-[#edf4fb]">
                                        {visibleSeasons.map((season) => {
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