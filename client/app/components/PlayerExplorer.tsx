import React, { useEffect, useState } from 'react';
import HiddenAccountIcon from './HiddenAccountIcon';

interface PlayerExplorerProps {
    onSelectMember: (memberName: string) => void;
}

interface PlayerExplorerRow {
    name: string;
    player_id: number;
    is_hidden: boolean;
    pvp_ratio: number | null;
    pvp_battles: number | null;
    account_age_days: number | null;
    ships_played_total: number | null;
    ranked_seasons_participated: number | null;
    kill_ratio: number | null;
    player_score: number | null;
    pvp_survival_rate: number | null;
}

interface PlayerExplorerResponse {
    count: number;
    page: number;
    page_size: number;
    results: PlayerExplorerRow[];
}

type SortKey = 'pvp_ratio' | 'pvp_battles' | 'account_age_days' | 'ships_played_total' | 'ranked_seasons_participated' | 'kill_ratio' | 'player_score' | 'pvp_survival_rate';
type SortDirection = 'asc' | 'desc';
type HiddenFilter = 'all' | 'visible' | 'hidden';
type RankedFilter = 'all' | 'yes' | 'no';
type ActivityBucket = 'all' | '7d' | '30d' | '90d' | 'dormant90plus';

const PAGE_SIZE = 10;

const formatMetric = (value: number | null | undefined): string => {
    if (value == null) {
        return '—';
    }

    return value.toLocaleString();
};

const formatWinRate = (value: number | null | undefined): string => {
    if (value == null) {
        return '—';
    }
    return `${value.toFixed(1)}%`;
};

const formatPercent = (value: number | null | undefined): string => {
    if (value == null) {
        return '—';
    }
    return `${value.toFixed(1)}%`;
};

const PlayerExplorer: React.FC<PlayerExplorerProps> = ({ onSelectMember }) => {
    const [query, setQuery] = useState('');
    const [hiddenFilter, setHiddenFilter] = useState<HiddenFilter>('visible');
    const [rankedFilter, setRankedFilter] = useState<RankedFilter>('all');
    const [activityBucket, setActivityBucket] = useState<ActivityBucket>('30d');
    const [sort, setSort] = useState<SortKey>('pvp_battles');
    const [direction, setDirection] = useState<SortDirection>('desc');
    const [page, setPage] = useState(1);
    const [data, setData] = useState<PlayerExplorerResponse | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const controller = new AbortController();
        const timeoutId = setTimeout(async () => {
            setIsLoading(true);
            setError(null);

            try {
                const params = new URLSearchParams({
                    q: query,
                    hidden: hiddenFilter,
                    ranked: rankedFilter,
                    activity_bucket: activityBucket,
                    sort,
                    direction,
                    page: String(page),
                    page_size: String(PAGE_SIZE),
                });
                const response = await fetch(`http://localhost:8888/api/players/explorer/?${params.toString()}`, {
                    signal: controller.signal,
                });

                if (!response.ok) {
                    throw new Error('Failed to load explorer data.');
                }

                const result: PlayerExplorerResponse = await response.json();
                setData(result);
            } catch (fetchError) {
                if (controller.signal.aborted) {
                    return;
                }

                console.error('Error fetching player explorer data:', fetchError);
                setError('Unable to load explorer data right now.');
                setData(null);
            } finally {
                if (!controller.signal.aborted) {
                    setIsLoading(false);
                }
            }
        }, 180);

        return () => {
            controller.abort();
            clearTimeout(timeoutId);
        };
    }, [activityBucket, direction, hiddenFilter, page, query, rankedFilter, sort]);

    const totalPages = data ? Math.max(1, Math.ceil(data.count / data.page_size)) : 1;

    return (
        <div className="mt-8 border-t border-[#c6dbef] pt-6">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
                <div>
                    <h3 className="text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Player Explorer</h3>
                    <p className="mt-1 text-xs text-[#6baed6]">Compare known players by recent activity, performance, longevity, and breadth.</p>
                </div>
                <p className="text-xs text-[#6baed6]">Visible dataset slice, not a universal leaderboard. Weighted KDR is tier-weighted and player score blends performance with recency.</p>
            </div>

            <div className="mt-4 grid gap-3 md:grid-cols-5">
                <input
                    type="text"
                    value={query}
                    onChange={(event) => {
                        setQuery(event.target.value);
                        setPage(1);
                    }}
                    placeholder="Filter players"
                    className="rounded-md border border-[#c6dbef] px-3 py-2 text-sm focus:border-[#4292c6] focus:outline-none focus:ring-1 focus:ring-[#4292c6]"
                />
                <select
                    value={hiddenFilter}
                    onChange={(event) => {
                        setHiddenFilter(event.target.value as HiddenFilter);
                        setPage(1);
                    }}
                    className="rounded-md border border-[#c6dbef] px-3 py-2 text-sm"
                >
                    <option value="visible">Visible only</option>
                    <option value="all">Visible + hidden</option>
                    <option value="hidden">Hidden only</option>
                </select>
                <select
                    value={activityBucket}
                    onChange={(event) => {
                        setActivityBucket(event.target.value as ActivityBucket);
                        setPage(1);
                    }}
                    className="rounded-md border border-[#c6dbef] px-3 py-2 text-sm"
                >
                    <option value="30d">Active in last 30 days</option>
                    <option value="7d">Active in last 7 days</option>
                    <option value="90d">Active in last 90 days</option>
                    <option value="dormant90plus">Dormant 90+ days</option>
                    <option value="all">All activity states</option>
                </select>
                <select
                    value={rankedFilter}
                    onChange={(event) => {
                        setRankedFilter(event.target.value as RankedFilter);
                        setPage(1);
                    }}
                    className="rounded-md border border-[#c6dbef] px-3 py-2 text-sm"
                >
                    <option value="all">All ranked states</option>
                    <option value="yes">Ranked only</option>
                    <option value="no">No ranked history</option>
                </select>
                <div className="flex gap-2">
                    <select
                        value={sort}
                        onChange={(event) => {
                            setSort(event.target.value as SortKey);
                            setPage(1);
                        }}
                        className="w-full rounded-md border border-[#c6dbef] px-3 py-2 text-sm"
                    >
                        <option value="pvp_ratio">PvP WR</option>
                        <option value="pvp_battles">Total battles</option>
                        <option value="player_score">Player score</option>
                        <option value="pvp_survival_rate">Survive %</option>
                        <option value="kill_ratio">Weighted KDR</option>
                        <option value="account_age_days">Account age</option>
                        <option value="ships_played_total">Ships played</option>
                        <option value="ranked_seasons_participated">Ranked seasons</option>
                    </select>
                    <select
                        value={direction}
                        onChange={(event) => {
                            setDirection(event.target.value as SortDirection);
                            setPage(1);
                        }}
                        className="rounded-md border border-[#c6dbef] px-3 py-2 text-sm"
                    >
                        <option value="desc">Desc</option>
                        <option value="asc">Asc</option>
                    </select>
                </div>
            </div>

            {error ? (
                <p className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
            ) : null}

            <div className="mt-4 overflow-x-auto rounded-lg border border-[#c6dbef] bg-white">
                <table className="min-w-full divide-y divide-[#dbe9f6] text-sm">
                    <thead className="bg-[#f0f7ff]">
                        <tr>
                            <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-[#2171b5]">Player</th>
                            <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-[#2171b5]">Score</th>
                            <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-[#2171b5]">Total Battles</th>
                            <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-[#2171b5]">Survive %</th>
                            <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-[#2171b5]">Weighted KDR</th>
                            <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-[#2171b5]">PvP WR</th>
                            <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-[#2171b5]">Ships</th>
                            <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-[#2171b5]">Ranked</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-[#edf4fb]">
                        {data?.results.map((row) => (
                            <tr key={row.player_id} className="align-top">
                                <td className="px-3 py-3 text-[#084594]">
                                    <button
                                        type="button"
                                        onClick={() => onSelectMember(row.name)}
                                        className="inline-flex items-center gap-1 text-left font-semibold underline-offset-2 hover:underline"
                                    >
                                        {row.name}
                                        {row.is_hidden ? <HiddenAccountIcon /> : null}
                                    </button>
                                </td>
                                <td className="px-3 py-3 text-right text-[#084594]">{formatMetric(row.player_score)}</td>
                                <td className="px-3 py-3 text-right text-[#084594]">{formatMetric(row.pvp_battles)}</td>
                                <td className="px-3 py-3 text-right text-[#084594]">{formatPercent(row.pvp_survival_rate)}</td>
                                <td className="px-3 py-3 text-right text-[#084594]">{formatMetric(row.kill_ratio)}</td>
                                <td className="px-3 py-3 text-right font-medium text-[#084594]">{formatWinRate(row.pvp_ratio)}</td>
                                <td className="px-3 py-3 text-right text-[#084594]">{formatMetric(row.ships_played_total)}</td>
                                <td className="px-3 py-3 text-right text-[#084594]">{formatMetric(row.ranked_seasons_participated)}</td>
                            </tr>
                        ))}
                        {!isLoading && (data?.results.length || 0) === 0 ? (
                            <tr>
                                <td colSpan={8} className="px-3 py-6 text-center text-sm text-gray-500">No players matched the current explorer filters.</td>
                            </tr>
                        ) : null}
                    </tbody>
                </table>
                {isLoading ? (
                    <div className="border-t border-[#dbe9f6] bg-[#f7fbff] px-3 py-2 text-xs text-[#6baed6]">Loading explorer data...</div>
                ) : null}
            </div>

            <div className="mt-3 flex items-center justify-between text-sm text-[#4292c6]">
                <p>{data ? `${data.count.toLocaleString()} matching players` : 'No explorer data yet'}</p>
                <div className="flex items-center gap-2">
                    <button
                        type="button"
                        onClick={() => setPage((current) => Math.max(1, current - 1))}
                        disabled={page <= 1 || isLoading}
                        className="rounded-md border border-[#c6dbef] px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        Prev
                    </button>
                    <span>Page {page} of {totalPages}</span>
                    <button
                        type="button"
                        onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                        disabled={page >= totalPages || isLoading}
                        className="rounded-md border border-[#c6dbef] px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        Next
                    </button>
                </div>
            </div>
        </div>
    );
};

export default PlayerExplorer;