import React, { useEffect, useMemo, useState } from 'react';
import wrColor from '../lib/wrColor';
import { PLAYER_ROUTE_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';

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

const CLAN_BATTLE_TABLE_VISIBLE_ROWS = 10;
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


const PlayerClanBattleSeasons: React.FC<PlayerClanBattleSeasonsProps> = ({ playerId, onSummaryChange }) => {
    const { realm } = useRealm();
    const [seasons, setSeasons] = useState<PlayerClanBattleSeason[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    useEffect(() => {
        let cancelled = false;

        const fetchSeasons = async () => {
            setLoading(true);
            setError('');
            try {
                const { data } = await fetchSharedJson<unknown>(withRealm(`/api/fetch/player_clan_battle_seasons/${playerId}/`, realm), {
                    label: `Player clan battle seasons ${playerId}`,
                    ttlMs: PLAYER_ROUTE_FETCH_TTL_MS,
                });
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
    }, [playerId, realm]);

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

        if (loading || error) {
            return;
        }

        onSummaryChange(summary);
    }, [error, loading, onSummaryChange, summary]);

    return (
        <div>
            {loading && <p className="text-sm text-[var(--text-secondary)]">Loading clan battle seasons...</p>}
            {!loading && error && <p className="text-sm text-[var(--text-secondary)]">{error}</p>}
            {!loading && !error && seasons.length === 0 && (
                <p className="text-sm text-[var(--text-secondary)]">No clan battle season data available for this player.</p>
            )}

            {!loading && !error && seasons.length > 0 && (
                <>
                    <div className="grid grid-cols-3 gap-2">
                        <div className="rounded-md bg-[var(--accent-faint)] px-3 py-2">
                            <p className="text-[10px] uppercase tracking-wide text-[var(--accent-light)]">Seasons</p>
                            <p className="mt-1 text-lg font-semibold text-[var(--accent-dark)]">{summary.seasonsPlayed}</p>
                        </div>
                        <div className="rounded-md bg-[var(--accent-faint)] px-3 py-2">
                            <p className="text-[10px] uppercase tracking-wide text-[var(--accent-light)]">Battles</p>
                            <p className="mt-1 text-lg font-semibold text-[var(--accent-dark)]">{summary.totalBattles.toLocaleString()}</p>
                        </div>
                        <div className="rounded-md bg-[var(--accent-faint)] px-3 py-2">
                            <p className="text-[10px] uppercase tracking-wide text-[var(--accent-light)]">WR</p>
                            <p className="mt-1 text-lg font-semibold" style={{ color: wrColor(summary.overallWinRate) }}>
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
                            <table className="min-w-full border-collapse text-xs tabular-nums text-[var(--text-primary)]">
                                <thead>
                                    <tr className="border-b border-[var(--border)] bg-[var(--bg-surface)] uppercase tracking-[0.12em] text-[var(--text-secondary)]">
                                        <th className="sticky top-0 bg-[var(--bg-surface)] py-2 pr-3 text-left font-semibold">Season</th>
                                        <th className="sticky top-0 bg-[var(--bg-surface)] py-2 pr-3 text-left font-semibold">Ships</th>
                                        <th className="sticky top-0 bg-[var(--bg-surface)] py-2 pr-3 text-right font-semibold">Battles</th>
                                        <th className="sticky top-0 bg-[var(--bg-surface)] py-2 pr-3 text-right font-semibold">WR</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {seasons.map((season) => (
                                        <tr key={season.season_id} className="border-b border-[var(--border)] align-top last:border-b-0">
                                            <td className="py-2 pr-3 text-left text-[var(--accent-dark)]">
                                                <div className="font-medium">{season.season_label}</div>
                                                <div className="text-[11px] text-[var(--text-secondary)]">{season.start_date || season.season_name}</div>
                                            </td>
                                            <td className="py-2 pr-3 text-left text-[var(--text-secondary)]">{formatTierRange(season.ship_tier_min, season.ship_tier_max)}</td>
                                            <td className="py-2 pr-3 text-right">{season.battles.toLocaleString()}</td>
                                            <td className="py-2 pr-3 text-right font-medium" style={{ color: wrColor(season.win_rate) }}>
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