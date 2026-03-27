import React, { useEffect, useState } from 'react';

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

const ClanBattleSeasons: React.FC<ClanBattleSeasonsProps> = ({ clanId, memberCount }) => {
    const [seasons, setSeasons] = useState<ClanBattleSeason[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [sortKey, setSortKey] = useState<SortKey>('start_date');
    const [sortDirection, setSortDirection] = useState<SortDirection>('desc');

    useEffect(() => {
        let cancelled = false;
        let timeoutId: ReturnType<typeof setTimeout> | null = null;
        let attempts = 0;

        const fetchSeasons = async () => {
            // Clear previous timeout ref so the finally block can detect
            // whether a NEW timeout was scheduled during this iteration.
            timeoutId = null;

            if (!cancelled && attempts === 0) {
                setLoading(true);
                setError('');
            }

            try {
                const response = await fetch(`/api/fetch/clan_battle_seasons/${clanId}`);
                if (!response.ok) {
                    throw new Error(`Failed to fetch clan battle seasons: ${response.status}`);
                }

                const data = await response.json();
                if (cancelled) {
                    return;
                }

                setSeasons(Array.isArray(data) ? data : []);

                const isPending = response.headers.get('X-Clan-Battles-Pending') === 'true';
                if (isPending && attempts < 5) {
                    attempts += 1;
                    timeoutId = setTimeout(() => {
                        void fetchSeasons();
                    }, 1500);
                    return;
                }
            } catch (err) {
                console.error('Error fetching clan battle seasons:', err);
                if (!cancelled) {
                    setError('Unable to load clan battles seasons.');
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
    }, [clanId]);

    const handleSort = (nextSortKey: SortKey) => {
        if (sortKey === nextSortKey) {
            setSortDirection(sortDirection === 'desc' ? 'asc' : 'desc');
            return;
        }

        setSortKey(nextSortKey);
        setSortDirection(nextSortKey === 'start_date' || nextSortKey === 'season' ? 'desc' : 'desc');
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
            {!loading && !error && seasons.length === 0 && (
                <p className="text-sm text-[var(--text-secondary)]">No clan battles season data available.</p>
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
                                    <td className="py-2 pr-3 text-right font-medium" style={{ color: selectColorByWR(season.roster_win_rate) }}>{season.roster_win_rate.toFixed(1)}%</td>
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