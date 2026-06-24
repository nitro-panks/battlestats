import React, { useEffect, useState } from 'react';
import wrColor from '../lib/wrColor';
import { useRealm } from '../context/RealmContext';
import { useTheme } from '../context/ThemeContext';
import { withRealm } from '../lib/realmParams';
import { fetchSharedJson, getChartFetchesInFlight, isAbortError } from '../lib/sharedJsonFetch';
import { degradationMonitor } from '../lib/degradationMonitor';
import ClanBattleSeasonsSVG from './ClanBattleSeasonsSVG';

// Cold-cache poll: a cold clan serves [] + X-Clan-Battles-Pending while a
// background roster-wide CB aggregation runs. That warm fans out across the
// whole roster, so it can outlast a single player's fetch — size the budget
// generously (mirrors PlayerClanBattleSeasons) and, on timeout-while-pending,
// show a "still warming" hint instead of the definitive empty state (which read
// as "No data" then flipped to real data on reload).
const CB_SEASONS_PENDING_RETRY_DELAY_MS = 1500;
const CB_SEASONS_PENDING_RETRY_LIMIT = 12;

interface ClanBattleSeason {
    season_id: number;
    season_name: string;
    season_label: string;
    start_date: string | null;
    end_date: string | null;
    ship_tier_min: number | null;
    ship_tier_max: number | null;
    participants: number;
    roster_battles: number;
    roster_wins: number;
    roster_losses: number;
    roster_win_rate: number;
    clan_battles: number;
    clan_wins: number;
}

interface ClanBattleSeasonsProps {
    clanId: number;
    memberCount: number;
}

type SortKey = 'season' | 'start_date' | 'ships' | 'participants' | 'active_rate' | 'roster_win_rate';
type SortDirection = 'asc' | 'desc';

const formatTierRange = (minTier: number | null, maxTier: number | null): string => {
    if (minTier == null && maxTier == null) return '—';
    if (minTier === maxTier) return `Tier ${minTier}`;
    if (minTier == null) return `Up to Tier ${maxTier}`;
    if (maxTier == null) return `Tier ${minTier}+`;
    return `Tiers ${minTier}-${maxTier}`;
};


const ClanBattleSeasons: React.FC<ClanBattleSeasonsProps> = ({ clanId, memberCount }) => {
    const { realm } = useRealm();
    const { theme } = useTheme();
    const [seasons, setSeasons] = useState<ClanBattleSeason[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [isPendingRefresh, setIsPendingRefresh] = useState(false);
    const [sortKey, setSortKey] = useState<SortKey>('start_date');
    const [sortDirection, setSortDirection] = useState<SortDirection>('desc');

    useEffect(() => {
        let cancelled = false;
        let timeoutId: ReturnType<typeof setTimeout> | null = null;
        let gateIntervalId: ReturnType<typeof setInterval> | null = null;
        let attempts = 0;
        const controller = new AbortController();

        const fetchSeasons = async () => {
            // Clear previous timeout ref so the finally block can detect
            // whether a NEW timeout was scheduled during this iteration.
            timeoutId = null;

            if (!cancelled && attempts === 0) {
                setLoading(true);
                setError('');
                setIsPendingRefresh(false);
            }

            try {
                const { data, headers } = await fetchSharedJson<ClanBattleSeason[]>(
                    withRealm(`/api/fetch/clan_battle_seasons/${clanId}`, realm),
                    {
                        label: `Clan battle seasons ${clanId}`,
                        responseHeaders: ['X-Clan-Battles-Pending'],
                        ttlMs: 0, // polled cold-cache freshness
                        priority: 'high',
                        signal: controller.signal,
                        cacheKey: `clan-battle-seasons:${clanId}:${realm}:${attempts}`,
                    },
                );
                if (cancelled) {
                    return;
                }

                setSeasons(Array.isArray(data) ? data : []);

                const isPending = headers['X-Clan-Battles-Pending'] === 'true';
                setIsPendingRefresh(isPending);
                if (isPending && attempts < CB_SEASONS_PENDING_RETRY_LIMIT) {
                    attempts += 1;
                    timeoutId = setTimeout(() => {
                        void fetchSeasons();
                    }, CB_SEASONS_PENDING_RETRY_DELAY_MS * degradationMonitor.getPollIntervalMultiplier());
                    return;
                }
            } catch (err) {
                if (isAbortError(err)) {
                    return;
                }
                console.error('Error fetching clan battle seasons:', err);
                if (!cancelled) {
                    setError('Unable to load clan battles seasons.');
                    setIsPendingRefresh(false);
                }
            } finally {
                if (!cancelled && !timeoutId) {
                    setLoading(false);
                }
            }
        };

        if (getChartFetchesInFlight() > 0) {
            gateIntervalId = setInterval(() => {
                if (getChartFetchesInFlight() === 0) {
                    clearInterval(gateIntervalId!);
                    gateIntervalId = null;
                    void fetchSeasons();
                }
            }, 500);
        } else {
            void fetchSeasons();
        }

        return () => {
            cancelled = true;
            controller.abort();
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
            if (gateIntervalId) {
                clearInterval(gateIntervalId);
            }
        };
    }, [clanId, realm]);

    const handleSort = (nextSortKey: SortKey) => {
        if (sortKey === nextSortKey) {
            setSortDirection(sortDirection === 'desc' ? 'asc' : 'desc');
            return;
        }

        setSortKey(nextSortKey);
        // Every sort key defaults to descending on first click.
        setSortDirection('desc');
    };

    const sortedSeasons = [...seasons].sort((left, right) => {
        const directionMultiplier = sortDirection === 'asc' ? 1 : -1;

        const shipRangeLeft = `${left.ship_tier_min ?? -1}-${left.ship_tier_max ?? -1}`;
        const shipRangeRight = `${right.ship_tier_min ?? -1}-${right.ship_tier_max ?? -1}`;

        const activeRateLeft = memberCount > 0 ? (left.participants / memberCount) * 100 : 0;
        const activeRateRight = memberCount > 0 ? (right.participants / memberCount) * 100 : 0;

        const valueMap = {
            season: left.season_id - right.season_id,
            start_date: (left.start_date || '').localeCompare(right.start_date || ''),
            ships: shipRangeLeft.localeCompare(shipRangeRight),
            participants: left.participants - right.participants,
            active_rate: activeRateLeft - activeRateRight,
            roster_win_rate: left.roster_win_rate - right.roster_win_rate,
        };

        const primary = valueMap[sortKey] * directionMultiplier;
        if (primary !== 0) {
            return primary;
        }

        return (right.season_id - left.season_id);
    });

    const sortIndicator = (key: SortKey): string => {
        if (sortKey !== key) return '';
        return sortDirection === 'desc' ? ' ↓' : ' ↑';
    };

    const ariaSortFor = (key: SortKey): 'none' | 'ascending' | 'descending' => {
        if (sortKey !== key) return 'none';
        return sortDirection === 'asc' ? 'ascending' : 'descending';
    };

    return (
        <div>
            {loading && <p className="text-sm text-[var(--text-secondary)]">Loading clan battles seasons...</p>}
            {!loading && error && <p className="text-sm text-[var(--text-secondary)]">{error}</p>}
            {!loading && !error && seasons.length === 0 && isPendingRefresh && (
                <p className="text-sm text-[var(--text-secondary)]">Clan battle data is still loading — refresh in a moment.</p>
            )}
            {!loading && !error && seasons.length === 0 && !isPendingRefresh && (
                <p className="text-sm text-[var(--text-secondary)]">No clan battles season data available.</p>
            )}

            {!loading && !error && seasons.length > 0 && (
                /* Pull left so the y-axis tick labels' left edge is flush with the
                   body's left edge (not the axis line — the labels sit ~31px in from
                   the SVG edge, so shift by that, leaving the axis naturally inset). */
                <div className="mb-4 md:-ml-[31px]">
                    <ClanBattleSeasonsSVG
                        seasons={seasons}
                        memberCount={memberCount}
                        theme={theme}
                    />
                </div>
            )}

            {!loading && !error && seasons.length > 0 && (
                <div className="max-h-[28rem] overflow-y-auto overflow-x-auto rounded-sm">
                    <table className="min-w-full border-collapse text-sm tabular-nums text-[var(--text-primary)]">
                        <thead>
                            <tr className="border-b border-[var(--border)] bg-[var(--bg-surface)] text-[11px] uppercase tracking-[0.14em] text-[var(--text-secondary)]">
                                <th className="sticky top-0 bg-[var(--bg-surface)] py-2 pr-4 text-left font-semibold" aria-sort={ariaSortFor('season')}>
                                    <button type="button" onClick={() => handleSort('season')} className="text-left font-semibold text-inherit hover:text-[var(--text-primary)]">
                                        Season{sortIndicator('season')}
                                    </button>
                                </th>
                                <th className="sticky top-0 bg-[var(--bg-surface)] py-2 pr-4 text-left font-semibold" aria-sort={ariaSortFor('start_date')}>
                                    <button type="button" onClick={() => handleSort('start_date')} className="text-left font-semibold text-inherit hover:text-[var(--text-primary)]">
                                        Date Start{sortIndicator('start_date')}
                                    </button>
                                </th>
                                <th className="sticky top-0 bg-[var(--bg-surface)] py-2 pr-4 text-left font-semibold" aria-sort={ariaSortFor('ships')}>
                                    <button type="button" onClick={() => handleSort('ships')} className="text-left font-semibold text-inherit hover:text-[var(--text-primary)]">
                                        Ships{sortIndicator('ships')}
                                    </button>
                                </th>
                                <th className="sticky top-0 bg-[var(--bg-surface)] py-2 pr-4 text-right font-semibold" aria-sort={ariaSortFor('participants')}>
                                    <button type="button" onClick={() => handleSort('participants')} className="text-right font-semibold text-inherit hover:text-[var(--text-primary)]">
                                        Players{sortIndicator('participants')}
                                    </button>
                                </th>
                                <th className="sticky top-0 bg-[var(--bg-surface)] py-2 pr-4 text-right font-semibold" aria-sort={ariaSortFor('active_rate')}>
                                    <button type="button" onClick={() => handleSort('active_rate')} className="text-right font-semibold text-inherit hover:text-[var(--text-primary)]">
                                        Clan Activity % {sortIndicator('active_rate')}
                                    </button>
                                </th>
                                <th className="sticky top-0 bg-[var(--bg-surface)] py-2 pr-3 text-right font-semibold" aria-sort={ariaSortFor('roster_win_rate')}>
                                    <button type="button" onClick={() => handleSort('roster_win_rate')} className="text-right font-semibold text-inherit hover:text-[var(--text-primary)]">
                                        WR{sortIndicator('roster_win_rate')}
                                    </button>
                                </th>
                            </tr>
                        </thead>
                        <tbody>
                            {sortedSeasons.map((season) => (
                                <tr key={season.season_id} className="border-b border-[var(--border)] align-top last:border-b-0">
                                    <td className="py-2 pr-4 text-left text-[var(--accent-dark)]">
                                        <div className="font-medium">{season.season_name}</div>
                                        <div className="text-xs text-[var(--text-secondary)]">{season.season_label}</div>
                                    </td>
                                    <td className="py-2 pr-4 text-left text-[var(--text-secondary)]">{season.start_date || '—'}</td>
                                    <td className="py-2 pr-4 text-left text-[var(--text-secondary)]">{formatTierRange(season.ship_tier_min, season.ship_tier_max)}</td>
                                    <td className="py-2 pr-4 text-right">{season.participants.toLocaleString()}</td>
                                    <td className="py-2 pr-4 text-right">{memberCount > 0 ? `${((season.participants / memberCount) * 100).toFixed(0)}%` : '—'}</td>
                                    <td className="py-2 pr-3 text-right font-medium" style={{ color: wrColor(season.roster_win_rate) }}>{season.roster_win_rate.toFixed(1)}%</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
};

export default ClanBattleSeasons;