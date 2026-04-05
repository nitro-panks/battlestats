import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCircleInfo } from '@fortawesome/free-solid-svg-icons';
import { useRouter, useSearchParams } from 'next/navigation';
import ClanDetail from './ClanDetail';
import EfficiencyRankIcon, { resolveEfficiencyRankTier, type EfficiencyRankTier } from './EfficiencyRankIcon';
import PlayerDetail from './PlayerDetail';
import { resilientDynamicImport } from './resilientDynamicImport';
import type { LandingClan, LandingPlayer, PlayerData } from './entityTypes';
import { buildClanPath, buildPlayerPath } from '../lib/entityRoutes';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import HiddenAccountIcon from './HiddenAccountIcon';
import PveEnjoyerIcon from './PveEnjoyerIcon';
import InactiveIcon from './InactiveIcon';
import RankedPlayerIcon from './RankedPlayerIcon';
import ClanBattleShieldIcon from './ClanBattleShieldIcon';
import useIntervalRefresh from './useIntervalRefresh';
import useClanHydrationPoll from './useClanHydrationPoll';
import { useTheme } from '../context/ThemeContext';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';
import wrColor from '../lib/wrColor';

const LoadingPanel: React.FC<{ label: string; minHeight?: number }> = ({ label, minHeight = 220 }) => (
    <div
        className="flex animate-pulse items-center justify-center rounded-md border border-[var(--border)] bg-[var(--bg-surface)] text-sm text-[var(--accent-light)]"
        style={{ minHeight }}
    >
        {label}
    </div>
);


const ClanTagGrid: React.FC<{
    clans: LandingClan[];
    onSelectClan: (clan: LandingClan) => void;
    ariaLabelPrefix: string;
}> = ({ clans, onSelectClan, ariaLabelPrefix }) => (
    <div
        className="mt-4 flex max-w-[910px] flex-wrap items-center gap-x-4 gap-y-2 rounded-md py-1 text-sm"
        style={{ paddingInline: '0.3rem' }}
    >
        {clans.map((clan) => (
            <button
                key={`${ariaLabelPrefix}-${clan.clan_id}`}
                type="button"
                onClick={() => onSelectClan(clan)}
                className="inline-flex min-w-0 items-center gap-1 rounded-sm py-1 text-left font-medium text-[var(--text-primary)]"
                style={{ paddingInline: '0.3rem' }}
                aria-label={`${ariaLabelPrefix} clan ${clan.name}`}
                title={clan.tag || clan.name}
            >
                <span style={{ color: wrColor(clan.clan_wr) }} aria-hidden="true">{"\u{1F79C}"}</span>
                <span
                    className="truncate underline decoration-2 underline-offset-4"
                    style={{ textDecorationColor: wrColor(clan.clan_wr) }}
                >
                    {clan.tag || '---'}
                </span>
                {clan.is_clan_battle_active ? (
                    <ClanBattleShieldIcon
                        winRate={null}
                        size="search"
                        titleText="clan battle enjoyers"
                        ariaLabel="clan battle enjoyers"
                        color="var(--accent-mid)"
                    />
                ) : null}
            </button>
        ))}
    </div>
);

const PlayerNameGrid: React.FC<{
    players: LandingPlayer[];
    onSelectMember: (playerName: string) => void;
    ariaLabelPrefix: string;
}> = ({ players, onSelectMember, ariaLabelPrefix }) => (
    <div
        className="mt-4 flex max-w-[910px] flex-wrap items-center gap-x-4 gap-y-2 rounded-md py-1 text-sm"
        style={{ paddingInline: '0.3rem' }}
    >
        {players.map((player) => {
            const label = player.name;
            const color = wrColor(player.pvp_ratio);
            const efficiencyTier = resolveEfficiencyRankTier(
                player.efficiency_rank_tier,
                player.has_efficiency_rank_icon,
            );
            const iconRow = (
                <>
                    {player.is_ranked_player ? <RankedPlayerIcon league={player.highest_ranked_league} size="search" /> : null}
                    {player.is_pve_player ? <PveEnjoyerIcon size="search" /> : null}
                    {player.is_sleepy_player ? <InactiveIcon size="search" /> : null}
                    {player.is_clan_battle_player ? <ClanBattleShieldIcon winRate={player.clan_battle_win_rate ?? null} size="search" /> : null}
                    {efficiencyTier === 'E' ? (
                        <EfficiencyRankIcon
                            tier={efficiencyTier}
                            percentile={player.efficiency_rank_percentile}
                            populationSize={player.efficiency_rank_population_size}
                            size="inline"
                        />
                    ) : null}
                </>
            );

            if (player.is_hidden) {
                return (
                    <span
                        key={`${ariaLabelPrefix}-${label}`}
                        className="inline-flex min-w-0 items-center gap-1 rounded-sm py-1 font-medium text-[var(--text-primary)]"
                        style={{ paddingInline: '0.3rem' }}
                        aria-label={`${label} has hidden stats`}
                        title={label}
                    >
                        <span style={{ color }} aria-hidden="true">{"\u{1F79C}"}</span>
                        <span
                            className="truncate underline decoration-2 underline-offset-4"
                            style={{ textDecorationColor: color }}
                        >
                            {label}
                        </span>
                        <HiddenAccountIcon />
                        {iconRow}
                    </span>
                );
            }

            return (
                <button
                    key={`${ariaLabelPrefix}-${label}`}
                    type="button"
                    onClick={() => onSelectMember(label)}
                    className="inline-flex min-w-0 items-center gap-1 rounded-sm py-1 font-medium text-[var(--text-primary)]"
                    style={{ paddingInline: '0.3rem' }}
                    aria-label={`${ariaLabelPrefix} player ${label}`}
                    title={label}
                >
                    <span style={{ color }} aria-hidden="true">{"\u{1F79C}"}</span>
                    <span
                        className="truncate underline decoration-2 underline-offset-4"
                        style={{ textDecorationColor: color }}
                    >
                        {label}
                    </span>
                    {iconRow}
                </button>
            );
        })}
    </div>
);

const LandingClanSVG = dynamic(
    () => resilientDynamicImport(() => import('./LandingClanSVG'), 'LandingClanSVG'),
    {
        ssr: false,
        loading: () => <LoadingPanel label="Loading clan landscape..." minHeight={360} />,
    },
);

const LandingPlayerSVG = dynamic(
    () => resilientDynamicImport(() => import('./LandingPlayerSVG'), 'LandingPlayerSVG'),
    {
        ssr: false,
        loading: () => <LoadingPanel label="Loading player landscape..." minHeight={360} />,
    },
);

const PlayerExplorer = dynamic(() => resilientDynamicImport(() => import('./PlayerExplorer'), 'PlayerExplorer'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading player explorer..." minHeight={360} />,
});

const LANDING_CLAN_LIMIT = 30;
const LANDING_PLAYER_LIMIT = 25;
const RANDOM_PLAYER_MIN_PVP_BATTLES = 500;
const BEST_PLAYER_MIN_PVP_BATTLES = 2500;
const CLAN_HYDRATION_POLL_LIMIT = 6;
const CLAN_HYDRATION_POLL_INTERVAL_MS = 2500;
const SHOW_PLAYER_EXPLORER = false;
const LANDING_FETCH_TTL_MS = 1500;

type LandingClanMode = 'random' | 'best' | 'recent';
type ClanBestSort = 'overall' | 'wr' | 'cb';
type LandingPlayerMode = 'best' | 'random' | 'recent';
type PlayerBestSort = 'overall' | 'ranked' | 'efficiency' | 'wr' | 'cb';

const LANDING_PLAYER_REFRESH_INTERVAL_MS = 60_000;

const BEST_FORMULA_APPROXIMATION = 'Best ≈ (0.40·WR_5-10 + 0.22·Score + 0.18·Eff + 0.10·Vol_5-10 + 0.06·Ranked + 0.04·Clan) × M_share';
const PLAYER_BEST_RANKED_FORMULA_APPROXIMATION = 'Ranked ≈ Gold medals, then Ranked WR, then Silver medals, then Bronze medals';
const PLAYER_BEST_EFFICIENCY_FORMULA_APPROXIMATION = 'Efficiency ≈ published efficiency percentile, then Score, then WR';
const PLAYER_BEST_WR_FORMULA_APPROXIMATION = 'WR ≈ WR_5-10, then Battles_5-10, then Score, then Eff';
const PLAYER_BEST_CB_FORMULA_APPROXIMATION = 'CB ≈ 0.55·CB_WR + 0.25·CB_Volume + 0.20·CB_Seasons';
const CLAN_BEST_OVERALL_FORMULA_APPROXIMATION = 'Overall ≈ 0.30·WR + 0.25·Activity + 0.20·MemberScore + 0.15·CB + 0.10·log(Battles)';
const CLAN_BEST_WR_FORMULA_APPROXIMATION = 'WR ≈ WR + 0.40·max(CB_WR - WR, 0)·min(CB_battles/200, 1)·min(Active/25, 1)·min(MemberScore/6, 1)';
const CLAN_BEST_CB_FORMULA_APPROXIMATION = 'CB ≈ average(last 10 completed season WR × min(season battles/30, 1) × min(season participants/clan members, 1); skipped seasons = 0)';
const BEST_CLAN_FALLBACK_NOTICE = 'Best clan rankings are still warming up for this realm. Showing recent clans until enough tracked data is available.';

const PlayerSearch: React.FC = () => {
    const { theme } = useTheme();
    const { realm } = useRealm();
    const router = useRouter();
    const searchParams = useSearchParams();
    const [playerData, setPlayerData] = useState<PlayerData | null>(null);
    const [error, setError] = useState('');
    const [isLoadingPlayer, setIsLoadingPlayer] = useState(false);
    const [clans, setClans] = useState<LandingClan[]>([]);
    const [clanMode, setClanMode] = useState<LandingClanMode>('best');
    const [clanBestSort, setClanBestSort] = useState<ClanBestSort>('overall');
    const [recentClans, setRecentClans] = useState<LandingClan[]>([]);
    const [players, setPlayers] = useState<LandingPlayer[]>([]);
    const [playerMode, setPlayerMode] = useState<LandingPlayerMode>('best');
    const [playerBestSort, setPlayerBestSort] = useState<PlayerBestSort>('overall');
    const [recentPlayers, setRecentPlayers] = useState<LandingPlayer[]>([]);
    const lastSubmittedSearchRef = useRef<string>('');
    const bestLandingWarmupRequestedRef = useRef(false);

    const fetchLandingClans = useCallback(async (mode: LandingClanMode, sort: ClanBestSort = 'overall') => {
        const params = new URLSearchParams({
            mode,
            limit: String(LANDING_CLAN_LIMIT),
        });
        if (mode === 'best') {
            params.set('sort', sort);
        }

        const { data: payload } = await fetchSharedJson<LandingClan[]>(
            withRealm(`/api/landing/clans?${params.toString()}`, realm),
            {
                label: `Landing clans (${mode}${mode === 'best' ? `:${sort}` : ''})`,
                ttlMs: LANDING_FETCH_TTL_MS,
            },
        );
        setClans(Array.isArray(payload) ? payload : []);
    }, [realm]);

    const triggerBestLandingWarmup = useCallback(() => {
        if (bestLandingWarmupRequestedRef.current) {
            return;
        }

        bestLandingWarmupRequestedRef.current = true;
        void fetchSharedJson(withRealm('/api/landing/warm-best/', realm), {
            label: 'Landing best warmup',
            ttlMs: 0,
        }).catch((err) => {
            console.warn('Error warming landing best entities:', err);
        });
    }, [realm]);

    const fetchLandingData = useCallback(async () => {
        triggerBestLandingWarmup();

        try {
            const [{ data: recentClansPayload }, { data: recentPlayersPayload }] = await Promise.all([
                fetchSharedJson<LandingClan[]>(withRealm('/api/landing/recent-clans/', realm), {
                    label: 'Recent clans',
                    ttlMs: LANDING_FETCH_TTL_MS,
                }),
                fetchSharedJson<LandingPlayer[]>(withRealm('/api/landing/recent/', realm), {
                    label: 'Recent players',
                    ttlMs: LANDING_FETCH_TTL_MS,
                }),
            ]);
            setRecentClans(Array.isArray(recentClansPayload) ? recentClansPayload : []);
            setRecentPlayers(Array.isArray(recentPlayersPayload) ? recentPlayersPayload : []);
        } catch (err) {
            console.error('Error fetching landing data:', err);
        }
    }, [realm, triggerBestLandingWarmup]);

    const fetchLandingPlayers = useCallback(async (mode: LandingPlayerMode, sort: PlayerBestSort = 'overall') => {
        try {
            const params = new URLSearchParams({
                mode,
                limit: String(LANDING_PLAYER_LIMIT),
            });
            if (mode === 'best') {
                params.set('sort', sort);
            }
            const { data: payload } = await fetchSharedJson<LandingPlayer[]>(
                withRealm(`/api/landing/players/?${params.toString()}`, realm),
                {
                    label: `Landing players (${mode}${mode === 'best' ? `:${sort}` : ''})`,
                    ttlMs: LANDING_FETCH_TTL_MS,
                },
            );
            setPlayers(Array.isArray(payload) ? payload : []);
        } catch (err) {
            console.error('Error fetching landing players:', err);
            setPlayers([]);
        }
    }, [realm]);

    useEffect(() => {
        fetchLandingData();
    }, [fetchLandingData]);

    useEffect(() => {
        const refreshLandingData = () => {
            if (typeof document !== 'undefined' && document.visibilityState !== 'visible') {
                return;
            }

            void fetchLandingData();
        };

        const handleVisibilityChange = () => {
            if (document.visibilityState === 'visible') {
                refreshLandingData();
            }
        };

        window.addEventListener('focus', refreshLandingData);
        window.addEventListener('pageshow', refreshLandingData);
        document.addEventListener('visibilitychange', handleVisibilityChange);

        return () => {
            window.removeEventListener('focus', refreshLandingData);
            window.removeEventListener('pageshow', refreshLandingData);
            document.removeEventListener('visibilitychange', handleVisibilityChange);
        };
    }, [fetchLandingData]);

    useEffect(() => {
        if (clanMode === 'recent') return;
        const sort = clanMode === 'best' ? clanBestSort : 'overall';
        void fetchLandingClans(clanMode, sort).catch((err) => {
            console.error('Error fetching landing clans:', err);
            setClans([]);
        });
    }, [clanBestSort, clanMode, fetchLandingClans]);

    useEffect(() => {
        setClanBestSort('overall');
    }, [realm]);

    useEffect(() => {
        if (playerMode === 'recent') return;
        void fetchLandingPlayers(playerMode, playerMode === 'best' ? playerBestSort : 'overall');
    }, [fetchLandingPlayers, playerBestSort, playerMode]);

    useEffect(() => {
        setPlayerBestSort('overall');
    }, [realm]);

    useIntervalRefresh(() => {
        if (clanMode !== 'recent') {
            void fetchLandingClans(clanMode, clanMode === 'best' ? clanBestSort : 'overall');
        }
    }, LANDING_PLAYER_REFRESH_INTERVAL_MS);

    useIntervalRefresh(() => {
        if (playerMode !== 'recent') {
            void fetchLandingPlayers(playerMode, playerMode === 'best' ? playerBestSort : 'overall');
        }
    }, LANDING_PLAYER_REFRESH_INTERVAL_MS);

    const fetchPlayerByName = useCallback(async (playerName: string): Promise<PlayerData | null> => {
        const { data } = await fetchSharedJson<PlayerData>(
            withRealm(`/api/player/${encodeURIComponent(playerName)}/`, realm),
            {
                label: `Player ${playerName}`,
                ttlMs: LANDING_FETCH_TTL_MS,
            },
        );
        return data;
    }, [realm]);

    const executePlayerSearch = useCallback(async (playerName: string) => {
        const trimmedPlayerName = playerName.trim();
        if (!trimmedPlayerName) {
            return;
        }

        lastSubmittedSearchRef.current = trimmedPlayerName;
        setIsLoadingPlayer(true);
        setError('');
        try {
            const data = await fetchPlayerByName(trimmedPlayerName);
            setPlayerData(data);
        } catch (err) {
            setError('Player not found');
            setPlayerData(null);
        } finally {
            setIsLoadingPlayer(false);
        }
    }, [fetchPlayerByName]);

    const handleSelectClan = useCallback((clan: LandingClan) => {
        router.push(buildClanPath(clan.clan_id, clan.name || clan.tag, realm));
    }, [router, realm]);

    const handleSelectClanById = async (clanId: number, clanName: string) => {
        router.push(buildClanPath(clanId, clanName, realm));
    };

    const visibleLandingClans = useMemo(() => {
        if (clanMode === 'recent') {
            return recentClans.slice(0, LANDING_CLAN_LIMIT);
        }

        if (clanMode === 'best' && clans.length === 0) {
            return recentClans.slice(0, LANDING_CLAN_LIMIT);
        }

        return clans.slice(0, LANDING_CLAN_LIMIT);
    }, [clanMode, clans, recentClans]);

    const isBestClanFallbackActive = clanMode === 'best' && clans.length === 0 && recentClans.length > 0;
    const showClanBestSortBar = clanMode === 'best';

    const visibleLandingPlayers = useMemo(() => {
        if (playerMode === 'recent') {
            return recentPlayers.slice(0, LANDING_PLAYER_LIMIT);
        }

        return players;
    }, [playerMode, players, recentPlayers]);
    const showPlayerBestSortBar = playerMode === 'best';

    const handleSelectMember = useCallback(async (memberName: string) => {
        router.push(buildPlayerPath(memberName, realm));
    }, [router, realm]);

    useEffect(() => {
        const query = (searchParams.get('q') || '').trim();
        if (!query || query === lastSubmittedSearchRef.current) {
            return;
        }

        void executePlayerSearch(query);
    }, [executePlayerSearch, searchParams]);

    useEffect(() => {
        const onNavSearch = (event: Event) => {
            const customEvent = event as CustomEvent<{ query?: string }>;
            const query = customEvent.detail?.query?.trim() || '';
            if (!query) {
                return;
            }

            void executePlayerSearch(query);
        };

        window.addEventListener('navSearch', onNavSearch as EventListener);
        return () => window.removeEventListener('navSearch', onNavSearch as EventListener);
    }, [executePlayerSearch]);

    const { resetAttempts: resetClanHydrationAttempts } = useClanHydrationPoll({
        playerData,
        fetchPlayerByName,
        setPlayerData,
        pollLimit: CLAN_HYDRATION_POLL_LIMIT,
        intervalMs: CLAN_HYDRATION_POLL_INTERVAL_MS,
    });

    const handleBack = useCallback(() => {
        setPlayerData(null);
        setError('');
        setIsLoadingPlayer(false);
        resetClanHydrationAttempts();
        fetchLandingData();
    }, [fetchLandingData, resetClanHydrationAttempts]);

    useEffect(() => {
        const onReset = () => handleBack();
        window.addEventListener('resetApp', onReset);
        return () => window.removeEventListener('resetApp', onReset);
    }, [handleBack]);

    return (
        <div className="p-4">
            {playerData ? (
                <PlayerDetail
                    player={playerData}
                    onBack={handleBack}
                    onSelectMember={handleSelectMember}
                    onSelectClan={handleSelectClanById}
                    isLoading={isLoadingPlayer}
                />
            ) : (
                <div>
                    {error && <p className="text-red-600 dark:text-red-400">{error}</p>}

                    {(clans.length > 0 || recentClans.length > 0) && (
                        <div className={`${error ? 'mt-6' : 'mt-2'} pt-6`}>
                            <div className="flex flex-wrap items-center gap-2">
                                <h3 className="mr-2 text-sm font-semibold uppercase tracking-wide text-[var(--accent-mid)]">Active Clans</h3>
                                <button
                                    type="button"
                                    onClick={() => setClanMode('best')}
                                    className={`inline-flex items-center rounded-md border px-3 py-1.5 text-sm font-semibold uppercase tracking-wide transition-colors ${clanMode === 'best' ? 'border-[var(--accent-mid)] bg-[var(--accent-mid)] text-white' : 'border-[var(--border)] bg-[var(--bg-page)] text-[var(--accent-mid)] hover:bg-[var(--accent-faint)]'}`}
                                    aria-pressed={clanMode === 'best'}
                                >
                                    Best
                                </button>
                                <button
                                    type="button"
                                    onClick={() => { setClanMode('random'); setClanBestSort('overall'); }}
                                    className={`inline-flex items-center rounded-md border px-3 py-1.5 text-sm font-semibold uppercase tracking-wide transition-colors ${clanMode === 'random' ? 'border-[var(--accent-mid)] bg-[var(--accent-mid)] text-white' : 'border-[var(--border)] bg-[var(--bg-page)] text-[var(--accent-mid)] hover:bg-[var(--accent-faint)]'}`}
                                    aria-pressed={clanMode === 'random'}
                                >
                                    Random
                                </button>
                                <button
                                    type="button"
                                    onClick={() => { setClanMode('recent'); setClanBestSort('overall'); }}
                                    className={`inline-flex items-center rounded-md border px-3 py-1.5 text-sm font-semibold uppercase tracking-wide transition-colors ${clanMode === 'recent' ? 'border-[var(--accent-mid)] bg-[var(--accent-mid)] text-white' : 'border-[var(--border)] bg-[var(--bg-page)] text-[var(--accent-mid)] hover:bg-[var(--accent-faint)]'}`}
                                    aria-pressed={clanMode === 'recent'}
                                >
                                    Recent
                                </button>
                            </div>
                            <div className="mt-1.5 min-h-7 pl-1" data-testid="clan-best-sort-bar-shell">
                                <div
                                    className={`flex items-center gap-1.5 transition-opacity ${showClanBestSortBar ? 'visible opacity-100' : 'invisible pointer-events-none opacity-0'}`}
                                    aria-hidden={!showClanBestSortBar}
                                    data-testid="clan-best-sort-bar"
                                >
                                    {(['overall', 'wr'] as const).map((sort, i) => (
                                        <React.Fragment key={sort}>
                                            {i > 0 && <span className="text-xs text-[var(--text-secondary)]">&middot;</span>}
                                            <button
                                                type="button"
                                                onClick={() => setClanBestSort(sort)}
                                                disabled={!showClanBestSortBar}
                                                tabIndex={showClanBestSortBar ? 0 : -1}
                                                className={`text-sm font-medium transition-colors disabled:cursor-default ${clanBestSort === sort ? 'text-[var(--accent-mid)] underline decoration-[var(--accent-mid)] underline-offset-4' : 'text-[var(--text-secondary)] hover:text-[var(--accent-mid)] hover:underline hover:underline-offset-4'}`}
                                            >
                                                {sort === 'overall' ? 'Overall' : 'WR'}
                                            </button>
                                            {sort === 'wr' ? (
                                                <div className="group relative inline-flex items-center">
                                                    <button
                                                        type="button"
                                                        className="inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] text-[var(--accent-light)] transition-colors hover:text-[var(--accent-mid)] focus:outline-none focus-visible:text-[var(--accent-mid)]"
                                                        aria-label="Clan ranking formula details"
                                                    >
                                                        <FontAwesomeIcon icon={faCircleInfo} className="text-[10px]" aria-hidden="true" />
                                                    </button>
                                                    <div className="pointer-events-none absolute left-1/2 top-full z-20 mt-2 hidden w-[27rem] max-w-[calc(100vw-2rem)] -translate-x-1/2 rounded-md border border-[var(--border)] bg-[var(--bg-page)] px-3 py-3 text-left text-xs normal-case tracking-normal text-[var(--text-primary)] shadow-lg group-hover:block group-focus-within:block">
                                                        <p className="font-semibold uppercase tracking-wide text-[var(--accent-mid)]">Clan ranking approximations</p>
                                                        <p className="mt-2 leading-5 text-[var(--text-secondary)]">
                                                            Hard filters require &gt;10 members, &ge;40% active share, &ge;5 tracked players, and &ge;50k total battles. Backend ranking owns both Best sub-sorts.
                                                        </p>
                                                        <div className="mt-3 space-y-3">
                                                            <div>
                                                                <p className="font-semibold uppercase tracking-wide text-[var(--accent-mid)]">Overall</p>
                                                                <p className="mt-1 font-mono text-[11px] leading-5 text-[var(--accent-dark)]">{CLAN_BEST_OVERALL_FORMULA_APPROXIMATION}</p>
                                                            </div>
                                                            <div>
                                                                <p className="font-semibold uppercase tracking-wide text-[var(--accent-mid)]">WR</p>
                                                                <p className="mt-1 font-mono text-[11px] leading-5 text-[var(--accent-dark)]">{CLAN_BEST_WR_FORMULA_APPROXIMATION}</p>
                                                            </div>
                                                        </div>
                                                    </div>
                                                </div>
                                            ) : null}
                                        </React.Fragment>
                                    ))}
                                </div>
                            </div>
                            <div className="mt-3">
                                {isBestClanFallbackActive ? (
                                    <p className="mb-3 rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-2 text-sm text-[var(--text-secondary)]">
                                        {BEST_CLAN_FALLBACK_NOTICE}
                                    </p>
                                ) : null}
                                <LandingClanSVG
                                    clans={visibleLandingClans}
                                    heatmapClans={clans}
                                    onSelectClan={handleSelectClan}
                                    theme={theme}
                                />
                            </div>
                            <ClanTagGrid
                                clans={visibleLandingClans}
                                onSelectClan={handleSelectClan}
                                ariaLabelPrefix="Show"
                            />
                        </div>
                    )}

                    {(players.length > 0 || recentPlayers.length > 0) && (
                        <div className="mt-6 border-t border-[var(--border)] pt-6">
                            <div className="flex flex-wrap items-center gap-2">
                                <h3 className="mr-2 text-sm font-semibold uppercase tracking-wide text-[var(--accent-mid)]">Active Players</h3>
                                <button
                                    type="button"
                                    onClick={() => setPlayerMode('best')}
                                    className={`inline-flex items-center rounded-md border px-3 py-1.5 text-sm font-semibold uppercase tracking-wide transition-colors ${playerMode === 'best' ? 'border-[var(--accent-mid)] bg-[var(--accent-mid)] text-white' : 'border-[var(--border)] bg-[var(--bg-page)] text-[var(--accent-mid)] hover:bg-[var(--accent-faint)]'}`}
                                    aria-pressed={playerMode === 'best'}
                                >
                                    Best
                                </button>
                                <button
                                    type="button"
                                    onClick={() => { setPlayerMode('random'); setPlayerBestSort('overall'); }}
                                    className={`inline-flex items-center rounded-md border px-3 py-1.5 text-sm font-semibold uppercase tracking-wide transition-colors ${playerMode === 'random' ? 'border-[var(--accent-mid)] bg-[var(--accent-mid)] text-white' : 'border-[var(--border)] bg-[var(--bg-page)] text-[var(--accent-mid)] hover:bg-[var(--accent-faint)]'}`}
                                    aria-pressed={playerMode === 'random'}
                                >
                                    Random
                                </button>
                                <button
                                    type="button"
                                    onClick={() => { setPlayerMode('recent'); setPlayerBestSort('overall'); }}
                                    className={`inline-flex items-center rounded-md border px-3 py-1.5 text-sm font-semibold uppercase tracking-wide transition-colors ${playerMode === 'recent' ? 'border-[var(--accent-mid)] bg-[var(--accent-mid)] text-white' : 'border-[var(--border)] bg-[var(--bg-page)] text-[var(--accent-mid)] hover:bg-[var(--accent-faint)]'}`}
                                    aria-pressed={playerMode === 'recent'}
                                >
                                    Recent
                                </button>
                            </div>
                            <div className="mt-1.5 min-h-7 pl-1" data-testid="player-best-sort-bar-shell">
                                <div
                                    className={`flex items-center gap-1.5 transition-opacity ${showPlayerBestSortBar ? 'visible opacity-100' : 'invisible pointer-events-none opacity-0'}`}
                                    aria-hidden={!showPlayerBestSortBar}
                                    data-testid="player-best-sort-bar"
                                >
                                    {(['overall', 'ranked', 'efficiency', 'wr', 'cb'] as const).map((sort, i) => (
                                        <React.Fragment key={sort}>
                                            {i > 0 && <span className="text-xs text-[var(--text-secondary)]">&middot;</span>}
                                            <button
                                                type="button"
                                                onClick={() => setPlayerBestSort(sort)}
                                                disabled={!showPlayerBestSortBar}
                                                tabIndex={showPlayerBestSortBar ? 0 : -1}
                                                className={`text-sm font-medium transition-colors disabled:cursor-default ${playerBestSort === sort ? 'text-[var(--accent-mid)] underline decoration-[var(--accent-mid)] underline-offset-4' : 'text-[var(--text-secondary)] hover:text-[var(--accent-mid)] hover:underline hover:underline-offset-4'}`}
                                            >
                                                {sort === 'overall' ? 'Overall' : sort === 'ranked' ? 'Ranked' : sort === 'efficiency' ? 'Efficiency' : sort === 'wr' ? 'WR' : 'CB'}
                                            </button>
                                        </React.Fragment>
                                    ))}
                                    <div className="group relative inline-flex items-center">
                                        <button
                                            type="button"
                                            className="inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] text-[var(--accent-light)] transition-colors hover:text-[var(--accent-mid)] focus:outline-none focus-visible:text-[var(--accent-mid)]"
                                            aria-label="Best player ranking formula details"
                                        >
                                            <FontAwesomeIcon icon={faCircleInfo} className="text-[10px]" aria-hidden="true" />
                                        </button>
                                        <div className="pointer-events-none absolute left-0 top-full z-20 mt-2 hidden w-[27rem] max-w-[calc(100vw-2rem)] rounded-md border border-[var(--border)] bg-[var(--bg-page)] px-3 py-3 text-left text-xs normal-case tracking-normal text-[var(--text-primary)] shadow-lg group-hover:block group-focus-within:block">
                                            <p className="font-semibold uppercase tracking-wide text-[var(--accent-mid)]">Player ranking approximations</p>
                                            <div className="mt-3 space-y-3">
                                                <div>
                                                    <p className="font-semibold uppercase tracking-wide text-[var(--accent-mid)]">Overall</p>
                                                    <p className="mt-1 font-mono text-[11px] leading-5 text-[var(--accent-dark)]">{BEST_FORMULA_APPROXIMATION}</p>
                                                </div>
                                                <div>
                                                    <p className="font-semibold uppercase tracking-wide text-[var(--accent-mid)]">Ranked</p>
                                                    <p className="mt-1 font-mono text-[11px] leading-5 text-[var(--accent-dark)]">{PLAYER_BEST_RANKED_FORMULA_APPROXIMATION}</p>
                                                    <p className="mt-2 text-[11px] leading-5 text-[var(--text-secondary)]">Sorted lexicographically by medal history: more Gold finishes always wins first, then higher aggregate ranked WR, then more Silver, then more Bronze. Freshness, volume, and score only break later ties.</p>
                                                </div>
                                                <div>
                                                    <p className="font-semibold uppercase tracking-wide text-[var(--accent-mid)]">Efficiency</p>
                                                    <p className="mt-1 font-mono text-[11px] leading-5 text-[var(--accent-dark)]">{PLAYER_BEST_EFFICIENCY_FORMULA_APPROXIMATION}</p>
                                                </div>
                                                <div>
                                                    <p className="font-semibold uppercase tracking-wide text-[var(--accent-mid)]">WR</p>
                                                    <p className="mt-1 font-mono text-[11px] leading-5 text-[var(--accent-dark)]">{PLAYER_BEST_WR_FORMULA_APPROXIMATION}</p>
                                                </div>
                                                <div>
                                                    <p className="font-semibold uppercase tracking-wide text-[var(--accent-mid)]">CB</p>
                                                    <p className="mt-1 font-mono text-[11px] leading-5 text-[var(--accent-dark)]">{PLAYER_BEST_CB_FORMULA_APPROXIMATION}</p>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <div className="mt-3">
                                <LandingPlayerSVG
                                    players={visibleLandingPlayers}
                                    onSelectPlayer={(player) => handleSelectMember(player.name)}
                                    theme={theme}
                                />
                            </div>
                            <PlayerNameGrid
                                players={visibleLandingPlayers}
                                onSelectMember={handleSelectMember}
                                ariaLabelPrefix="Show"
                            />
                        </div>
                    )}

                    {SHOW_PLAYER_EXPLORER ? <PlayerExplorer onSelectMember={handleSelectMember} /> : null}
                </div>
            )}
        </div>
    );
};

export default PlayerSearch;