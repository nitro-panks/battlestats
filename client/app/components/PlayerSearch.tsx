import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faBed, faRobot, faShieldHalved, faStar } from '@fortawesome/free-solid-svg-icons';
import { useRouter, useSearchParams } from 'next/navigation';
import ClanDetail from './ClanDetail';
import PlayerDetail from './PlayerDetail';
import { resilientDynamicImport } from './resilientDynamicImport';
import { getRankedLeagueColor, getRankedLeagueTooltip, type RankedLeagueName } from './rankedLeague';
import type { LandingClan, PlayerData } from './entityTypes';
import { buildClanPath, buildPlayerPath } from '../lib/entityRoutes';
import HiddenAccountIcon from './HiddenAccountIcon';

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
            const iconRow = (
                <>
                    {player.is_ranked_player ? <LandingRankedStar league={player.highest_ranked_league} /> : null}
                    {player.is_pve_player ? <LandingPveRobot /> : null}
                    {player.is_sleepy_player ? <LandingSleepyBed /> : null}
                    {player.is_clan_battle_player ? <LandingClanBattleShield winRate={player.clan_battle_win_rate} /> : null}
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

type LandingClanMode = 'random' | 'best';
type LandingPlayerMode = 'random' | 'best';

const readJsonOrThrow = async <T,>(response: Response, label: string): Promise<T> => {
    const contentType = response.headers.get('content-type') || '';

    if (!response.ok) {
        const body = await response.text();
        throw new Error(`${label} failed with ${response.status}: ${body.slice(0, 120)}`);
    }

    if (!contentType.toLowerCase().includes('application/json')) {
        const body = await response.text();
        throw new Error(`${label} returned non-JSON content: ${body.slice(0, 120)}`);
    }

    return response.json() as Promise<T>;
};

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
    const clanHydrationAttemptsRef = useRef<Record<string, number>>({});
    const lastSubmittedSearchRef = useRef<string>('');

    const fetchLandingData = useCallback(async () => {
        try {
            const [clansRes, recentClansRes, recentRes] = await Promise.all([
                fetch('http://localhost:8888/api/landing/clans/'),
                fetch('http://localhost:8888/api/landing/recent-clans/'),
                fetch('http://localhost:8888/api/landing/recent/'),
            ]);
            const [clansPayload, recentClansPayload, recentPlayersPayload] = await Promise.all([
                readJsonOrThrow<LandingClan[]>(clansRes, 'Landing clans'),
                readJsonOrThrow<LandingClan[]>(recentClansRes, 'Recent clans'),
                readJsonOrThrow<LandingPlayer[]>(recentRes, 'Recent players'),
            ]);
            setClans(Array.isArray(clansPayload) ? clansPayload : []);
            setRecentClans(Array.isArray(recentClansPayload) ? recentClansPayload : []);
            setRecentPlayers(Array.isArray(recentPlayersPayload) ? recentPlayersPayload : []);
        } catch (err) {
            console.error('Error fetching landing data:', err);
        }
    }, []);

    const fetchLandingPlayers = useCallback(async (mode: LandingPlayerMode) => {
        try {
            const response = await fetch(`http://localhost:8888/api/landing/players/?mode=${mode}&limit=${LANDING_LIMIT}`);
            const payload = await readJsonOrThrow<LandingPlayer[]>(response, `Landing players (${mode})`);
            setPlayers(Array.isArray(payload) ? payload : []);
        } catch (err) {
            console.error('Error fetching landing players:', err);
            setPlayers([]);
        }
    }, []);

    const handleBack = useCallback(() => {
        setPlayerData(null);
        setError('');
        setIsLoadingPlayer(false);
        clanHydrationAttemptsRef.current = {};
        fetchLandingData();
    }, [fetchLandingData]);

    useEffect(() => {
        const onReset = () => handleBack();
        window.addEventListener('resetApp', onReset);
        return () => window.removeEventListener('resetApp', onReset);
    }, [handleBack]);

    useEffect(() => {
        fetchLandingData();
    }, [fetchLandingData]);

    useEffect(() => {
        void fetchLandingPlayers(playerMode);
    }, [fetchLandingPlayers, playerMode]);

    const fetchPlayerByName = async (playerName: string): Promise<PlayerData | null> => {
        const response = await fetch(`http://localhost:8888/api/player/${encodeURIComponent(playerName)}/`);
        return readJsonOrThrow<PlayerData>(response, `Player ${playerName}`);
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

    useEffect(() => {
        if (!playerData?.name) {
            return;
        }

        // Poll only when clan_id is present but clan_name is still missing
        // (clan record being hydrated in background). Skip for clanless players.
        const needsHydration = playerData.clan_id && !playerData.clan_name;
        if (!needsHydration) {
            return;
        }

        const playerName = playerData.name;
        const attemptsUsed = clanHydrationAttemptsRef.current[playerName] || 0;
        if (attemptsUsed >= CLAN_HYDRATION_POLL_LIMIT) {
            return;
        }

        const interval = setInterval(async () => {
            const currentAttempts = clanHydrationAttemptsRef.current[playerName] || 0;
            if (currentAttempts >= CLAN_HYDRATION_POLL_LIMIT) {
                clearInterval(interval);
                return;
            }

            clanHydrationAttemptsRef.current[playerName] = currentAttempts + 1;

            try {
                const refreshed = await fetchPlayerByName(playerName);
                if (!refreshed) {
                    return;
                }

                setPlayerData(refreshed);

                if (refreshed.clan_id && refreshed.clan_name) {
                    clearInterval(interval);
                }
            } catch (err) {
                if ((clanHydrationAttemptsRef.current[playerName] || 0) >= CLAN_HYDRATION_POLL_LIMIT) {
                    clearInterval(interval);
                }
            }
        }, CLAN_HYDRATION_POLL_INTERVAL_MS);

        return () => clearInterval(interval);
    }, [playerData?.name, playerData?.clan_id, playerData?.clan_name]);

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