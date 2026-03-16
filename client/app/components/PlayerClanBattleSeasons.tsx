import React, { useEffect, useMemo, useState } from 'react';

interface PlayerClanBattleSeason {
    season_id: number;
    season_name: string;
    season_label: string;
    start_date: string | null;
    end_date: string | null;
    ship_tier_min: number | null;
    ship_tier_max: number | null;
    battles: number;
    wins: number;
    losses: number;
    win_rate: number;
}

interface PlayerClanBattleSeasonsProps {
    playerId: number;
    onSummaryChange?: (summary: PlayerClanBattleSummary | null) => void;
}

const CLAN_BATTLE_TABLE_VISIBLE_ROWS = 5;
const CLAN_BATTLE_TABLE_HEADER_HEIGHT_REM = 2.25;
const CLAN_BATTLE_TABLE_ROW_HEIGHT_REM = 3.25;

export interface PlayerClanBattleSummary {
    seasonsPlayed: number;
    totalBattles: number;
    overallWinRate: number;
}

const formatTierRange = (minTier: number | null, maxTier: number | null): string => {
    if (minTier == null && maxTier == null) return '-';
    if (minTier === maxTier) return `T${minTier}`;
    if (minTier == null) return `<=T${maxTier}`;
    if (maxTier == null) return `T${minTier}+`;
    return `T${minTier}-${maxTier}`;
};

const selectColorByWR = (winRatio: number): string => {
    if (winRatio > 65) return '#810c9e';
    if (winRatio >= 60) return '#D042F3';
    if (winRatio >= 56) return '#3182bd';
    if (winRatio >= 54) return '#74c476';
    if (winRatio >= 52) return '#a1d99b';
    if (winRatio >= 50) return '#fed976';
    if (winRatio >= 45) return '#fd8d3c';
    return '#a50f15';
};

const PlayerClanBattleSeasons: React.FC<PlayerClanBattleSeasonsProps> = ({ playerId, onSummaryChange }) => {
    const [seasons, setSeasons] = useState<PlayerClanBattleSeason[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    useEffect(() => {
        let cancelled = false;

        const fetchSeasons = async () => {
            setLoading(true);
            setError('');
            try {
                const response = await fetch(`http://localhost:8888/api/fetch/player_clan_battle_seasons/${playerId}/`);
                if (!response.ok) {
                    throw new Error(`Failed to fetch player clan battle seasons: ${response.status}`);
                }

                const data = await response.json();
                if (!cancelled) {
                    setSeasons(Array.isArray(data) ? data : []);
                }
            } catch (fetchError) {
                console.error('Error fetching player clan battle seasons:', fetchError);
                if (!cancelled) {
                    setError('Unable to load clan battle seasons right now.');
                }
            } finally {
                if (!cancelled) {
                    setLoading(false);
                }
            }
        };

        void fetchSeasons();
        return () => {
            cancelled = true;
        };
    }, [playerId]);

    const summary = useMemo<PlayerClanBattleSummary>(() => {
        const totalBattles = seasons.reduce((sum, season) => sum + season.battles, 0);
        const totalWins = seasons.reduce((sum, season) => sum + season.wins, 0);
        return {
            seasonsPlayed: seasons.length,
            totalBattles,
            overallWinRate: totalBattles > 0 ? (totalWins / totalBattles) * 100 : 0,
        };
    }, [seasons]);

    useEffect(() => {
        if (!onSummaryChange) {
            return;
        }

        if (loading || error || seasons.length === 0) {
            onSummaryChange(null);
            return;
        }

        onSummaryChange(summary);
    }, [error, loading, onSummaryChange, seasons.length, summary]);

    return (
        <div>
            <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-600">Clan Battle Seasons</h3>
            <p className="mt-1 text-xs text-gray-500">Player-specific clan battle participation by season.</p>

            {loading && <p className="mt-3 text-sm text-gray-500">Loading clan battle seasons...</p>}
            {!loading && error && <p className="mt-3 text-sm text-gray-500">{error}</p>}
            {!loading && !error && seasons.length === 0 && (
                <p className="mt-3 text-sm text-gray-500">No clan battle season data available for this player.</p>
            )}

            {!loading && !error && seasons.length > 0 && (
                <>
                    <div className="mt-3 grid grid-cols-3 gap-2">
                        <div className="rounded-md bg-[#eff3ff] px-3 py-2">
                            <p className="text-[10px] uppercase tracking-wide text-[#4292c6]">Seasons</p>
                            <p className="mt-1 text-lg font-semibold text-[#084594]">{summary.seasonsPlayed}</p>
                        </div>
                        <div className="rounded-md bg-[#eff3ff] px-3 py-2">
                            <p className="text-[10px] uppercase tracking-wide text-[#4292c6]">Battles</p>
                            <p className="mt-1 text-lg font-semibold text-[#084594]">{summary.totalBattles.toLocaleString()}</p>
                        </div>
                        <div className="rounded-md bg-[#eff3ff] px-3 py-2">
                            <p className="text-[10px] uppercase tracking-wide text-[#4292c6]">WR</p>
                            <p className="mt-1 text-lg font-semibold" style={{ color: selectColorByWR(summary.overallWinRate) }}>
                                {summary.overallWinRate.toFixed(1)}%
                            </p>
                        </div>
                    </div>

                    <div className="mt-3 overflow-x-auto rounded-sm">
                        <div
                            className="overflow-y-auto"
                            style={{
                                maxHeight: `calc(${CLAN_BATTLE_TABLE_HEADER_HEIGHT_REM}rem + (${CLAN_BATTLE_TABLE_VISIBLE_ROWS} * ${CLAN_BATTLE_TABLE_ROW_HEIGHT_REM}rem))`,
                            }}
                        >
                        <table className="min-w-full border-collapse text-xs tabular-nums text-gray-700">
                            <thead>
                                <tr className="border-b border-gray-200 bg-white uppercase tracking-[0.12em] text-gray-500">
                                    <th className="sticky top-0 bg-white py-2 pr-3 text-left font-semibold">Season</th>
                                    <th className="sticky top-0 bg-white py-2 pr-3 text-left font-semibold">Ships</th>
                                    <th className="sticky top-0 bg-white py-2 pr-3 text-right font-semibold">Battles</th>
                                    <th className="sticky top-0 bg-white py-2 text-right font-semibold">WR</th>
                                </tr>
                            </thead>
                            <tbody>
                                {seasons.map((season) => (
                                    <tr key={season.season_id} className="border-b border-gray-100 align-top last:border-b-0">
                                        <td className="py-2 pr-3 text-left text-[#084594]">
                                            <div className="font-medium">{season.season_label}</div>
                                            <div className="text-[11px] text-gray-500">{season.start_date || season.season_name}</div>
                                        </td>
                                        <td className="py-2 pr-3 text-left text-gray-600">{formatTierRange(season.ship_tier_min, season.ship_tier_max)}</td>
                                        <td className="py-2 pr-3 text-right">{season.battles.toLocaleString()}</td>
                                        <td className="py-2 text-right font-medium" style={{ color: selectColorByWR(season.win_rate) }}>
                                            {season.win_rate.toFixed(1)}%
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        </div>
                    </div>
                </>
            )}
        </div>
    );
};

export default PlayerClanBattleSeasons;