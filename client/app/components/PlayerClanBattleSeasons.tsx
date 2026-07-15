import React, { useEffect, useMemo, useState } from 'react';
import wrColor from '../lib/wrColor';
import { PLAYER_ROUTE_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson, isAbortError } from '../lib/sharedJsonFetch';
import { degradationMonitor } from '../lib/degradationMonitor';
import { usePlayerRequestSignal } from '../context/PlayerRequestScopeContext';
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
    // Server-computed currency marker (current CB season resolution lives
    // backend-side; see runbook-cb-icon-current-season-2026-07-15.md).
    is_current?: boolean;
}

interface PlayerClanBattleSeasonsProps {
    playerId: number;
    onSummaryChange?: (summary: PlayerClanBattleSummary | null) => void;
}

// Rows visible before the table scrolls. Sized so the scroll viewport fills the
// Insights tab's ~800px of vertical room (2.25rem header + 14 * 3.25rem ≈ 764px),
// matching the Activity table / Ships chart instead of a short pinned box.
const CLAN_BATTLE_TABLE_VISIBLE_ROWS = 14;
const CLAN_BATTLE_TABLE_HEADER_HEIGHT_REM = 2.25;
// Row = py-2 (1rem) + text-sm label line (1.25rem) + text-xs sub line (1rem)
// + border ≈ 3.375rem; the visible-rows clamp derives from this.
const CLAN_BATTLE_TABLE_ROW_HEIGHT_REM = 3.375;

export interface PlayerClanBattleSummary {
    seasonsPlayed: number;
    totalBattles: number;
    overallWinRate: number;
    // Current-season slice behind the header CB shield: battles logged in
    // the row the server flagged `is_current` (0 when the player is sitting
    // the season out), and that row's WR (null without battles).
    currentSeasonBattles: number;
    currentSeasonWinRate: number | null;
}

const formatTierRange = (minTier: number | null, maxTier: number | null): string => {
    if (minTier == null && maxTier == null) return '-';
    if (minTier === maxTier) return `T${minTier}`;
    if (minTier == null) return `<=T${maxTier}`;
    if (maxTier == null) return `T${minTier}+`;
    return `T${minTier}-${maxTier}`;
};


// Cold-cache poll: the endpoint now serves [] + X-Clan-Battle-Seasons-Pending
// while a background WG fetch is in flight (instead of blocking the request).
// Re-poll until the fetch lands. Sized to outlast a worst-case cold fetch
// (Celery pickup + ~5s WG + background rate-limiter budget) while staying
// bounded — mirrors RankedSeasons.tsx.
const CB_SEASONS_PENDING_RETRY_DELAY_MS = 1500;
const CB_SEASONS_PENDING_RETRY_LIMIT = 12;

const PlayerClanBattleSeasons: React.FC<PlayerClanBattleSeasonsProps> = ({ playerId, onSummaryChange }) => {
    const { realm } = useRealm();
    const requestSignal = usePlayerRequestSignal();
    const [seasons, setSeasons] = useState<PlayerClanBattleSeason[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [isPendingRefresh, setIsPendingRefresh] = useState(false);

    useEffect(() => {
        let cancelled = false;
        let timeoutId: ReturnType<typeof setTimeout> | null = null;
        let pendingAttempts = 0;

        const fetchSeasons = async () => {
            timeoutId = null;
            if (pendingAttempts === 0) {
                setLoading(true);
                setError('');
                setIsPendingRefresh(false);
            }
            try {
                const { data, headers } = await fetchSharedJson<unknown>(withRealm(`/api/fetch/player_clan_battle_seasons/${playerId}/`, realm), {
                    label: `Player clan battle seasons ${playerId}`,
                    ttlMs: PLAYER_ROUTE_FETCH_TTL_MS,
                    signal: requestSignal,
                    cacheKey: `clan-cb-seasons:${playerId}:${pendingAttempts}`,
                    responseHeaders: ['X-Clan-Battle-Seasons-Pending'],
                });
                if (cancelled) {
                    return;
                }

                setSeasons(Array.isArray(data) ? data : []);

                const pending = headers['X-Clan-Battle-Seasons-Pending'] === 'true';
                setIsPendingRefresh(pending);
                if (pending && pendingAttempts < CB_SEASONS_PENDING_RETRY_LIMIT) {
                    pendingAttempts += 1;
                    timeoutId = setTimeout(() => {
                        void fetchSeasons();
                    }, CB_SEASONS_PENDING_RETRY_DELAY_MS * degradationMonitor.getPollIntervalMultiplier());
                    return;
                }
            } catch (fetchError) {
                // Benign cancellation (nav / realm switch) — leave state untouched.
                if (isAbortError(fetchError)) {
                    return;
                }
                console.error('Error fetching player clan battle seasons:', fetchError);
                if (!cancelled) {
                    setError('Unable to load clan battle seasons right now.');
                    setIsPendingRefresh(false);
                }
            } finally {
                if (!cancelled && !timeoutId) {
                    setLoading(false);
                }
            }
        };

        void fetchSeasons();
        return () => {
            cancelled = true;
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
        };
    }, [playerId, realm, requestSignal]);

    const summary = useMemo<PlayerClanBattleSummary>(() => {
        const totalBattles = seasons.reduce((sum, season) => sum + season.battles, 0);
        const totalWins = seasons.reduce((sum, season) => sum + season.wins, 0);
        const currentSeason = seasons.find((season) => season.is_current);
        const currentSeasonBattles = currentSeason?.battles ?? 0;
        return {
            seasonsPlayed: seasons.length,
            totalBattles,
            overallWinRate: totalBattles > 0 ? (totalWins / totalBattles) * 100 : 0,
            currentSeasonBattles,
            currentSeasonWinRate: currentSeasonBattles > 0 ? (currentSeason?.win_rate ?? null) : null,
        };
    }, [seasons]);

    useEffect(() => {
        if (!onSummaryChange) {
            return;
        }

        if (loading || error || isPendingRefresh) {
            // Don't emit a zero summary while a cold fetch is still pending —
            // it would briefly clear the clan-battle header badge.
            return;
        }

        onSummaryChange(summary);
    }, [error, isPendingRefresh, loading, onSummaryChange, summary]);

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
                            <table className="min-w-full border-collapse text-sm tabular-nums text-[var(--text-primary)]">
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
                                                <div className="text-xs text-[var(--text-secondary)]">{season.start_date || season.season_name}</div>
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