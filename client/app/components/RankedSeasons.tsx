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
    sprints_played: number;
    best_sprint: RankedSprint | null;
    sprints: RankedSprint[];
}

const leagueColors: Record<string, string> = {
    Gold: 'border-amber-300 bg-amber-50 text-amber-800',
    Silver: 'border-slate-300 bg-slate-50 text-slate-700',
    Bronze: 'border-orange-300 bg-orange-50 text-orange-800',
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

const getFreshnessStatus = (timestamp: string | null): 'fresh' | 'stale' | 'unknown' => {
    if (!timestamp) {
        return 'unknown';
    }

    const updatedAt = new Date(timestamp).getTime();
    if (Number.isNaN(updatedAt)) {
        return 'unknown';
    }

    return Date.now() - updatedAt <= 24 * 60 * 60 * 1000 ? 'fresh' : 'stale';
};

const formatWinRate = (winRate: number): string => `${(winRate * 100).toFixed(1)}%`;

const formatDateRange = (startDate: string | null, endDate: string | null): string => {
    if (startDate && endDate) {
        return `${startDate} to ${endDate}`;
    }

    if (startDate) {
        return `Started ${startDate}`;
    }

    if (endDate) {
        return `Ended ${endDate}`;
    }

    return 'Dates unavailable';
};

const RankedSeasons: React.FC<RankedSeasonsProps> = ({ playerId, isLoading = false }) => {
    const [seasons, setSeasons] = useState<RankedSeason[]>([]);
    const [rankedUpdatedAt, setRankedUpdatedAt] = useState<string | null>(null);
    const [isChartLoading, setIsChartLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

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
                setRankedUpdatedAt(response.headers.get('X-Ranked-Updated-At'));
            } catch (fetchError) {
                if (!isMounted) {
                    return;
                }

                console.error('Error fetching ranked data:', fetchError);
                setError('Unable to load ranked data right now.');
                setSeasons([]);
                setRankedUpdatedAt(null);
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

    const rankedFreshness = getFreshnessStatus(rankedUpdatedAt);
    const shouldGrayOut = isLoading || isChartLoading;

    return (
        <div>
            <div className="mb-2 text-xs text-gray-600">
                Ranked data last refreshed: {formatTimestamp(rankedUpdatedAt)}
                {' '}
                <span className={rankedFreshness === 'fresh' ? 'text-green-700' : rankedFreshness === 'stale' ? 'text-red-700' : 'text-gray-500'}>
                    {rankedFreshness === 'fresh' ? 'fresh' : rankedFreshness === 'stale' ? 'stale' : 'unknown'}
                </span>
            </div>

            {error ? (
                <p className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                    {error}
                </p>
            ) : null}

            {seasons.length === 0 && !shouldGrayOut && !error ? (
                <p className="text-sm text-gray-500">No ranked seasons found for this player.</p>
            ) : null}

            <div className="relative">
                <div className={shouldGrayOut ? 'pointer-events-none opacity-60 grayscale transition' : 'transition'} aria-busy={shouldGrayOut}>
                    <div className="grid gap-3 lg:grid-cols-2">
                        {seasons.map((season) => {
                            const badgeClassName = leagueColors[season.highest_league_name] || leagueColors.Bronze;

                            return (
                                <article key={season.season_id} className="rounded-lg border border-[#c6dbef] bg-[#f8fbff] p-4 shadow-sm">
                                    <div className="flex flex-wrap items-start justify-between gap-2">
                                        <div>
                                            <h4 className="text-base font-semibold text-[#084594]">{season.season_label} - {season.season_name}</h4>
                                            <p className="mt-1 text-xs text-[#6baed6]">{formatDateRange(season.start_date, season.end_date)}</p>
                                        </div>
                                        <span className={`rounded-full border px-2 py-1 text-xs font-semibold ${badgeClassName}`}>
                                            {season.highest_league_name}
                                        </span>
                                    </div>

                                    <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
                                        <div className="rounded-md bg-white px-3 py-2">
                                            <p className="text-xs uppercase tracking-wide text-[#4292c6]">Win Rate</p>
                                            <p className="mt-1 text-lg font-semibold text-[#084594]">{formatWinRate(season.win_rate)}</p>
                                        </div>
                                        <div className="rounded-md bg-white px-3 py-2">
                                            <p className="text-xs uppercase tracking-wide text-[#4292c6]">Battles</p>
                                            <p className="mt-1 text-lg font-semibold text-[#084594]">{season.total_battles.toLocaleString()}</p>
                                        </div>
                                        <div className="rounded-md bg-white px-3 py-2">
                                            <p className="text-xs uppercase tracking-wide text-[#4292c6]">Wins</p>
                                            <p className="mt-1 text-lg font-semibold text-[#084594]">{season.total_wins.toLocaleString()}</p>
                                        </div>
                                        <div className="rounded-md bg-white px-3 py-2">
                                            <p className="text-xs uppercase tracking-wide text-[#4292c6]">Sprints Played</p>
                                            <p className="mt-1 text-lg font-semibold text-[#084594]">{season.sprints_played}</p>
                                        </div>
                                    </div>

                                    <div className="mt-3 rounded-md border border-[#dbe9f6] bg-white px-3 py-2 text-sm text-[#2171b5]">
                                        {season.best_sprint ? (
                                            <p>
                                                Best sprint: Sprint {season.best_sprint.sprint_number} - {season.best_sprint.league_name} league, rank {season.best_sprint.best_rank}
                                            </p>
                                        ) : (
                                            <p>Best sprint unavailable.</p>
                                        )}
                                    </div>

                                    {season.sprints.length > 0 ? (
                                        <div className="mt-3 flex flex-wrap gap-2">
                                            {season.sprints.map((sprint) => (
                                                <span key={`${season.season_id}-${sprint.sprint_number}`} className="rounded-full border border-[#c6dbef] bg-white px-2 py-1 text-xs text-[#2171b5]">
                                                    Sprint {sprint.sprint_number}: {sprint.league_name} r{sprint.best_rank} ({sprint.battles} battles)
                                                </span>
                                            ))}
                                        </div>
                                    ) : null}
                                </article>
                            );
                        })}
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
        </div>
    );
};

export default RankedSeasons;