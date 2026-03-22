import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faBed, faCircleInfo, faRobot, faShieldHalved, faStar } from '@fortawesome/free-solid-svg-icons';
import { useRouter, useSearchParams } from 'next/navigation';
import ClanDetail from './ClanDetail';
import EfficiencyRankIcon, { resolveEfficiencyRankTier, type EfficiencyRankTier } from './EfficiencyRankIcon';
import PlayerDetail from './PlayerDetail';
import { resilientDynamicImport } from './resilientDynamicImport';
import { getRankedLeagueColor, getRankedLeagueTooltip, type RankedLeagueName } from './rankedLeague';
import type { LandingClan, PlayerData } from './entityTypes';
import { buildClanPath, buildPlayerPath } from '../lib/entityRoutes';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import HiddenAccountIcon from './HiddenAccountIcon';
import useIntervalRefresh from './useIntervalRefresh';
import useClanHydrationPoll from './useClanHydrationPoll';

interface LandingPlayer {
    name: string;
    pvp_ratio: number | null;
    is_hidden?: boolean;
    pvp_battles?: number | null;
    high_tier_pvp_ratio?: number | null;
    high_tier_pvp_battles?: number | null;
    is_pve_player?: boolean;
    is_sleepy_player?: boolean;
    is_ranked_player?: boolean;
    is_clan_battle_player?: boolean;
    clan_battle_win_rate?: number | null;
    highest_ranked_league?: RankedLeagueName | null;
    efficiency_rank_percentile?: number | null;
    efficiency_rank_tier?: EfficiencyRankTier | null;
    has_efficiency_rank_icon?: boolean;
    efficiency_rank_population_size?: number | null;
    efficiency_rank_updated_at?: string | null;
}

const wrColor = (r: number | null): string => {
    if (r == null) return '#c6dbef';
    if (r > 65) return '#810c9e';
    if (r >= 60) return '#D042F3';
    if (r >= 56) return '#3182bd';
    if (r >= 54) return '#74c476';
    if (r >= 52) return '#a1d99b';
    if (r >= 50) return '#fed976';
    if (r >= 45) return '#fd8d3c';
    return '#a50f15';
}

const LoadingPanel: React.FC<{ label: string; minHeight?: number }> = ({ label, minHeight = 220 }) => (
    <div
        className="flex animate-pulse items-center justify-center rounded-md border border-[#dbe9f6] bg-[#f7fbff] text-sm text-[#6baed6]"
        style={{ minHeight }}
    >
        {label}
    </div>
);

const LandingPveRobot = () => (
    <span
        title="pve enjoyer"
        aria-label="pve enjoyer"
        className="inline-flex items-center cursor-help"
    >
        <FontAwesomeIcon
            icon={faRobot}
            className="text-xs text-slate-500"
            aria-hidden="true"
        />
    </span>
);

const LandingSleepyBed = () => (
    <span
        title="inactive for over a year"
        aria-label="inactive for over a year"
        className="inline-flex items-center cursor-help"
    >
        <FontAwesomeIcon
            icon={faBed}
            className="text-xs text-slate-400"
            aria-hidden="true"
        />
    </span>
);

const LandingRankedStar: React.FC<{ league: RankedLeagueName | null | undefined }> = ({ league }) => (
    <span
        title={getRankedLeagueTooltip(league)}
        aria-label={getRankedLeagueTooltip(league)}
        className="inline-flex items-center cursor-help"
    >
        <FontAwesomeIcon
            icon={faStar}
            className="text-xs"
            style={{ color: getRankedLeagueColor(league) }}
            aria-hidden="true"
        />
    </span>
);

const LandingClanBattleShield: React.FC<{ winRate: number | null | undefined }> = ({ winRate }) => (
    <span
        title={winRate == null ? 'clan battle enjoyer' : `clan battle enjoyer · ${winRate.toFixed(1)}% WR`}
        aria-label={winRate == null ? 'clan battle enjoyer' : `clan battle enjoyer ${winRate.toFixed(1)} percent WR`}
        className="inline-flex items-center cursor-help"
    >
        <FontAwesomeIcon
            icon={faShieldHalved}
            className="text-xs"
            style={{ color: wrColor(winRate ?? null) }}
            aria-hidden="true"
        />
    </span>
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
                className="inline-flex min-w-0 items-center gap-1 rounded-sm py-1 text-left font-medium text-[#334155]"
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
                    {player.is_ranked_player ? <LandingRankedStar league={player.highest_ranked_league} /> : null}
                    {player.is_pve_player ? <LandingPveRobot /> : null}
                    {player.is_sleepy_player ? <LandingSleepyBed /> : null}
                    {player.is_clan_battle_player ? <LandingClanBattleShield winRate={player.clan_battle_win_rate} /> : null}
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
                        className="inline-flex min-w-0 items-center gap-1 rounded-sm py-1 font-medium text-[#334155]"
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
                    className="inline-flex min-w-0 items-center gap-1 rounded-sm py-1 font-medium text-[#334155]"
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

const PlayerExplorer = dynamic(() => resilientDynamicImport(() => import('./PlayerExplorer'), 'PlayerExplorer'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading player explorer..." minHeight={360} />,
});

const LANDING_LIMIT = 40;
const BEST_CLAN_MIN_TOTAL_BATTLES = 100000;
const BEST_CLAN_MIN_ACTIVE_SHARE = 0.3;
const RANDOM_PLAYER_MIN_PVP_BATTLES = 500;
const BEST_PLAYER_MIN_PVP_BATTLES = 2500;
const CLAN_HYDRATION_POLL_LIMIT = 6;
const CLAN_HYDRATION_POLL_INTERVAL_MS = 2500;
const SHOW_PLAYER_EXPLORER = false;
const LANDING_FETCH_TTL_MS = 1500;

type LandingClanMode = 'random' | 'best';
type LandingPlayerMode = 'random' | 'best' | 'sigma';

const LANDING_PLAYER_REFRESH_INTERVAL_MS = 60_000;

const BEST_FORMULA_APPROXIMATION = 'Best ≈ (0.40·WR_5-10 + 0.22·Score + 0.18·Eff + 0.10·Vol_5-10 + 0.06·Ranked + 0.04·Clan) × M_share';
const CLAN_BEST_FORMULA_APPROXIMATION = 'Best_clan ≈ WR × I(Battles ≥ 100k) × I(ActiveShare ≥ 0.30), tie → Battles';

const PlayerSearch: React.FC = () => {
    const router = useRouter();
    const searchParams = useSearchParams();
    const [playerData, setPlayerData] = useState<PlayerData | null>(null);
    const [error, setError] = useState('');
    const [isLoadingPlayer, setIsLoadingPlayer] = useState(false);
    const [clans, setClans] = useState<LandingClan[]>([]);
    const [clanMode, setClanMode] = useState<LandingClanMode>('random');
    const [recentClans, setRecentClans] = useState<LandingClan[]>([]);
    const [players, setPlayers] = useState<LandingPlayer[]>([]);
    const [playerMode, setPlayerMode] = useState<LandingPlayerMode>('random');
    const [recentPlayers, setRecentPlayers] = useState<LandingPlayer[]>([]);
    const lastSubmittedSearchRef = useRef<string>('');

    const fetchLandingClans = useCallback(async (mode: LandingClanMode) => {
        const { data: payload } = await fetchSharedJson<LandingClan[]>(
            `/api/landing/clans/?mode=${mode}&limit=${LANDING_LIMIT}`,
            {
                label: `Landing clans (${mode})`,
                ttlMs: LANDING_FETCH_TTL_MS,
            },
        );
        setClans(Array.isArray(payload) ? payload : []);
    }, []);

    const fetchLandingData = useCallback(async () => {
        try {
            const [{ data: recentClansPayload }, { data: recentPlayersPayload }] = await Promise.all([
                fetchSharedJson<LandingClan[]>('/api/landing/recent-clans/', {
                    label: 'Recent clans',
                    ttlMs: LANDING_FETCH_TTL_MS,
                }),
                fetchSharedJson<LandingPlayer[]>('/api/landing/recent/', {
                    label: 'Recent players',
                    ttlMs: LANDING_FETCH_TTL_MS,
                }),
            ]);
            setRecentClans(Array.isArray(recentClansPayload) ? recentClansPayload : []);
            setRecentPlayers(Array.isArray(recentPlayersPayload) ? recentPlayersPayload : []);
        } catch (err) {
            console.error('Error fetching landing data:', err);
        }
    }, []);

    const fetchLandingPlayers = useCallback(async (mode: LandingPlayerMode) => {
        try {
            const { data: payload } = await fetchSharedJson<LandingPlayer[]>(
                `/api/landing/players/?mode=${mode}&limit=${LANDING_LIMIT}`,
                {
                    label: `Landing players (${mode})`,
                    ttlMs: LANDING_FETCH_TTL_MS,
                },
            );
            setPlayers(Array.isArray(payload) ? payload : []);
        } catch (err) {
            console.error('Error fetching landing players:', err);
            setPlayers([]);
        }
    }, []);

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
        void fetchLandingClans(clanMode).catch((err) => {
            console.error('Error fetching landing clans:', err);
            setClans([]);
        });
    }, [clanMode, fetchLandingClans]);

    useEffect(() => {
        void fetchLandingPlayers(playerMode);
    }, [fetchLandingPlayers, playerMode]);

    useIntervalRefresh(() => {
        void fetchLandingClans(clanMode);
    }, LANDING_PLAYER_REFRESH_INTERVAL_MS);

    useIntervalRefresh(() => {
        void fetchLandingPlayers(playerMode);
    }, LANDING_PLAYER_REFRESH_INTERVAL_MS);

    const fetchPlayerByName = async (playerName: string): Promise<PlayerData | null> => {
        const { data } = await fetchSharedJson<PlayerData>(
            `/api/player/${encodeURIComponent(playerName)}/`,
            {
                label: `Player ${playerName}`,
                ttlMs: LANDING_FETCH_TTL_MS,
            },
        );
        return data;
    };

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
    }, []);

    const handleSelectClan = useCallback((clan: LandingClan) => {
        router.push(buildClanPath(clan.clan_id, clan.name || clan.tag));
    }, [router]);

    const handleSelectClanById = async (clanId: number, clanName: string) => {
        router.push(buildClanPath(clanId, clanName));
    };

    const visibleLandingClans = useMemo(() => {
        if (clanMode === 'best') {
            return [...clans]
                .filter((clan) => {
                    if ((clan.total_battles ?? 0) < BEST_CLAN_MIN_TOTAL_BATTLES) {
                        return false;
                    }

                    const activeMembers = clan.active_members ?? 0;
                    return (activeMembers / Math.max(clan.members_count, 1)) >= BEST_CLAN_MIN_ACTIVE_SHARE;
                })
                .sort((left, right) => {
                    const leftWr = left.clan_wr ?? Number.NEGATIVE_INFINITY;
                    const rightWr = right.clan_wr ?? Number.NEGATIVE_INFINITY;
                    if (rightWr !== leftWr) {
                        return rightWr - leftWr;
                    }

                    const leftBattles = left.total_battles ?? Number.NEGATIVE_INFINITY;
                    const rightBattles = right.total_battles ?? Number.NEGATIVE_INFINITY;
                    if (rightBattles !== leftBattles) {
                        return rightBattles - leftBattles;
                    }

                    return left.name.localeCompare(right.name);
                })
                .slice(0, LANDING_LIMIT);
        }

        return clans.slice(0, LANDING_LIMIT);
    }, [clanMode, clans]);

    const handleSelectMember = useCallback(async (memberName: string) => {
        router.push(buildPlayerPath(memberName));
    }, [router]);

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
                    {error && <p className="text-red-600">{error}</p>}

                    {clans.length > 0 && (
                        <div className={`${error ? 'mt-6' : 'mt-2'} pt-6`}>
                            <div className="flex flex-wrap items-center gap-2">
                                <button
                                    type="button"
                                    onClick={() => setClanMode('random')}
                                    className={`inline-flex items-center rounded-md border px-3 py-1.5 text-sm font-semibold uppercase tracking-wide transition-colors ${clanMode === 'random' ? 'border-[#2171b5] bg-[#2171b5] text-white' : 'border-[#c6dbef] bg-white text-[#2171b5] hover:bg-[#eff3ff]'}`}
                                    aria-pressed={clanMode === 'random'}
                                >
                                    Random
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setClanMode('best')}
                                    className={`inline-flex items-center rounded-md border px-3 py-1.5 text-sm font-semibold uppercase tracking-wide transition-colors ${clanMode === 'best' ? 'border-[#2171b5] bg-[#2171b5] text-white' : 'border-[#c6dbef] bg-white text-[#2171b5] hover:bg-[#eff3ff]'}`}
                                    aria-pressed={clanMode === 'best'}
                                >
                                    Best
                                </button>
                                <div className="group relative inline-flex items-center">
                                    <button
                                        type="button"
                                        className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-[#c6dbef] bg-white text-[#6baed6] transition-colors hover:bg-[#eff3ff] hover:text-[#2171b5] focus:outline-none focus-visible:text-[#2171b5]"
                                        aria-label="Clan ranking formula details"
                                    >
                                        <FontAwesomeIcon icon={faCircleInfo} className="text-sm" aria-hidden="true" />
                                    </button>
                                    <div className="pointer-events-none absolute left-0 top-full z-20 mt-2 hidden w-[22rem] max-w-[calc(100vw-2rem)] rounded-md border border-[#c6dbef] bg-white px-3 py-3 text-left text-xs normal-case tracking-normal text-[#334155] shadow-lg group-hover:block group-focus-within:block">
                                        <p className="font-semibold uppercase tracking-wide text-[#2171b5]">Best approximation</p>
                                        <p className="mt-2 font-mono text-[11px] leading-5 text-[#1e3a5f]">{CLAN_BEST_FORMULA_APPROXIMATION}</p>
                                        <p className="mt-2 leading-5 text-[#475569]">
                                            Current clan Best is a thresholded competitive surface: require at least 100k total battles and at least 30% active members, then rank by clan WR with total battles as the first tie-break.
                                        </p>
                                    </div>
                                </div>
                            </div>
                            <div className="mt-3">
                                <LandingClanSVG
                                    clans={visibleLandingClans}
                                    heatmapClans={clans}
                                    onSelectClan={handleSelectClan}
                                />
                            </div>
                            <ClanTagGrid
                                clans={visibleLandingClans}
                                onSelectClan={handleSelectClan}
                                ariaLabelPrefix="Show"
                            />

                            <h3 className="mt-5 text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Recently Viewed Clans</h3>
                            {recentClans.length > 0 ? (
                                <ClanTagGrid
                                    clans={recentClans.slice(0, LANDING_LIMIT)}
                                    onSelectClan={handleSelectClan}
                                    ariaLabelPrefix="Show recent"
                                />
                            ) : (
                                <p className="mt-2 text-sm text-[#6baed6]">No recently viewed clans yet.</p>
                            )}
                        </div>
                    )}

                    {players.length > 0 && (
                        <div className="mt-6 border-t border-[#c6dbef] pt-6">
                            <div className="flex flex-wrap items-center gap-2">
                                <h3 className="mr-2 text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Active Players</h3>
                                <button
                                    type="button"
                                    onClick={() => setPlayerMode('random')}
                                    className={`inline-flex items-center rounded-md border px-3 py-1.5 text-sm font-semibold uppercase tracking-wide transition-colors ${playerMode === 'random' ? 'border-[#2171b5] bg-[#2171b5] text-white' : 'border-[#c6dbef] bg-white text-[#2171b5] hover:bg-[#eff3ff]'}`}
                                    aria-pressed={playerMode === 'random'}
                                >
                                    Random
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setPlayerMode('best')}
                                    className={`inline-flex items-center rounded-md border px-3 py-1.5 text-sm font-semibold uppercase tracking-wide transition-colors ${playerMode === 'best' ? 'border-[#2171b5] bg-[#2171b5] text-white' : 'border-[#c6dbef] bg-white text-[#2171b5] hover:bg-[#eff3ff]'}`}
                                    aria-pressed={playerMode === 'best'}
                                >
                                    Best
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setPlayerMode('sigma')}
                                    className={`inline-flex items-center rounded-md border px-3 py-1.5 text-sm font-semibold uppercase tracking-wide transition-colors ${playerMode === 'sigma' ? 'border-[#2171b5] bg-[#2171b5] text-white' : 'border-[#c6dbef] bg-white text-[#2171b5] hover:bg-[#eff3ff]'}`}
                                    aria-pressed={playerMode === 'sigma'}
                                >
                                    Sigma
                                </button>
                                <div className="group relative inline-flex items-center">
                                    <button
                                        type="button"
                                        className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-[#c6dbef] bg-white text-[#6baed6] transition-colors hover:bg-[#eff3ff] hover:text-[#2171b5] focus:outline-none focus-visible:text-[#2171b5]"
                                        aria-label="Best ranking formula details"
                                    >
                                        <FontAwesomeIcon icon={faCircleInfo} className="text-sm" aria-hidden="true" />
                                    </button>
                                    <div className="pointer-events-none absolute left-0 top-full z-20 mt-2 hidden w-[22rem] max-w-[calc(100vw-2rem)] rounded-md border border-[#c6dbef] bg-white px-3 py-3 text-left text-xs normal-case tracking-normal text-[#334155] shadow-lg group-hover:block group-focus-within:block">
                                        <p className="font-semibold uppercase tracking-wide text-[#2171b5]">Best approximation</p>
                                        <p className="mt-2 font-mono text-[11px] leading-5 text-[#1e3a5f]">{BEST_FORMULA_APPROXIMATION}</p>
                                        <p className="mt-2 leading-5 text-[#475569]">
                                            Uses tier 5-10 win rate as the anchor, then blends Battlestats score, published efficiency, competitive volume, ranked, and clan battles. Player detail now shows literal KDR separately, but Best still uses the composite score rather than overall KDR directly. Low-tier-heavy profiles are discounted by a competitive-share multiplier.
                                        </p>
                                    </div>
                                </div>
                            </div>
                            <PlayerNameGrid
                                players={players}
                                onSelectMember={handleSelectMember}
                                ariaLabelPrefix="Show"
                            />

                            <h3 className="mt-5 text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Recently Viewed</h3>
                            {recentPlayers.length > 0 ? (
                                <PlayerNameGrid
                                    players={recentPlayers.slice(0, LANDING_LIMIT)}
                                    onSelectMember={handleSelectMember}
                                    ariaLabelPrefix="Show recent"
                                />
                            ) : (
                                <p className="mt-2 text-sm text-[#6baed6]">No recently viewed players yet.</p>
                            )}
                        </div>
                    )}

                    {SHOW_PLAYER_EXPLORER ? <PlayerExplorer onSelectMember={handleSelectMember} /> : null}
                </div>
            )}
        </div>
    );
};

export default PlayerSearch;