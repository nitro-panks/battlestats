"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCircleInfo } from '@fortawesome/free-solid-svg-icons';
import { useRouter } from 'next/navigation';
import EfficiencyRankIcon, { resolveEfficiencyRankTier } from './EfficiencyRankIcon';
import PlayerDetail from './PlayerDetail';
import LoadingPanel from './LoadingPanel';
import RealmTopShipsTreemapSVG from './RealmTopShipsTreemapSVG';
import ShipLeaderboard, { type ShipLeaderboardHandle } from './ShipLeaderboard';
import { resilientDynamicImport } from './resilientDynamicImport';
import type { LandingClan, LandingPlayer, PlayerData } from './entityTypes';
import { buildClanPath, buildPlayerPath } from '../lib/entityRoutes';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import HiddenAccountIcon from './HiddenAccountIcon';
import TwitchStreamerIcon from './TwitchStreamerIcon';
import PveEnjoyerIcon from './PveEnjoyerIcon';
import InactiveIcon from './InactiveIcon';
import RankedPlayerIcon from './RankedPlayerIcon';
import ClanBattleShieldIcon from './ClanBattleShieldIcon';
import TopShipBadges from './TopShipBadges';
import useIntervalRefresh from './useIntervalRefresh';
import useClanHydrationPoll from './useClanHydrationPoll';
import { useTheme } from '../context/ThemeContext';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';
import { trackEvent } from '../lib/umami';
import wrColor from '../lib/wrColor';


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
    realm: string;
}> = ({ players, onSelectMember, ariaLabelPrefix, realm }) => (
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
                    {player.is_streamer ? <TwitchStreamerIcon size="search" /> : null}
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
                    <TopShipBadges badges={player.ship_badges} realm={realm} size="search" />
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

const LANDING_CLAN_LIMIT = 30;
const LANDING_PLAYER_LIMIT = 25;
const CLAN_HYDRATION_POLL_LIMIT = 6;
const CLAN_HYDRATION_POLL_INTERVAL_MS = 2500;
// Backend caches best/random for 6 h, so a 60-second client-side TTL lets SPA
// back-navigations to the landing page hit the in-memory cache (no network,
// no empty-state flash) while still catching meaningful churn within a
// single session.
const LANDING_FETCH_TTL_MS = 60_000;

type ClanBestSort = 'overall' | 'wr';
type PlayerBestSort = 'overall' | 'ranked' | 'efficiency' | 'wr' | 'cb';

const LANDING_PLAYER_REFRESH_INTERVAL_MS = 60_000;

const BEST_FORMULA_APPROXIMATION = 'Best ≈ (0.40·WR_5-10 + 0.22·Score + 0.18·Eff + 0.10·Vol_5-10 + 0.06·Ranked + 0.04·Clan) × M_share';
const PLAYER_BEST_RANKED_FORMULA_APPROXIMATION = 'Ranked ≈ Gold medals, then Ranked WR, then Silver medals, then Bronze medals';
const PLAYER_BEST_EFFICIENCY_FORMULA_APPROXIMATION = 'Efficiency ≈ published efficiency percentile, then Score, then WR';
const PLAYER_BEST_WR_FORMULA_APPROXIMATION = 'WR ≈ WR_5-10, then Battles_5-10, then Score, then Eff';
const PLAYER_BEST_CB_FORMULA_APPROXIMATION = 'CB ≈ 0.55·CB_WR + 0.25·CB_Volume + 0.20·CB_Seasons';
const CLAN_BEST_OVERALL_FORMULA_APPROXIMATION = 'Overall ≈ 0.30·WR + 0.25·Activity + 0.20·MemberScore + 0.15·CB + 0.10·log(Battles)';
const CLAN_BEST_WR_FORMULA_APPROXIMATION = 'WR ≈ WR + 0.40·max(CB_WR - WR, 0)·min(CB_battles/200, 1)·min(Active/25, 1)·min(MemberScore/6, 1)';
const BEST_CLAN_WARMUP_NOTICE = 'Best clan rankings are still warming up for this realm. Check back shortly.';

const PlayerSearch: React.FC = () => {
    const { theme } = useTheme();
    const { realm } = useRealm();
    const router = useRouter();
    const [playerData, setPlayerData] = useState<PlayerData | null>(null);
    const [error, setError] = useState('');
    const [isLoadingPlayer, setIsLoadingPlayer] = useState(false);
    const [clans, setClans] = useState<LandingClan[]>([]);
    const [clansFetched, setClansFetched] = useState(false);
    const [clanBestSort, setClanBestSort] = useState<ClanBestSort>('overall');
    const [players, setPlayers] = useState<LandingPlayer[]>([]);
    const [playerBestSort, setPlayerBestSort] = useState<PlayerBestSort>('overall');
    const lastSubmittedSearchRef = useRef<string>('');
    const bestLandingWarmupRequestedRef = useRef(false);
    const shipLeaderboardRef = useRef<ShipLeaderboardHandle>(null);

    const fetchLandingClans = useCallback(async (sort: ClanBestSort = 'overall') => {
        const params = new URLSearchParams({
            mode: 'best',
            limit: String(LANDING_CLAN_LIMIT),
            sort,
        });

        try {
            const { data: payload } = await fetchSharedJson<LandingClan[]>(
                withRealm(`/api/landing/clans?${params.toString()}`, realm),
                {
                    label: `Landing clans (best:${sort})`,
                    ttlMs: LANDING_FETCH_TTL_MS,
                },
            );
            setClans(Array.isArray(payload) ? payload : []);
        } finally {
            // Mark the surface as fetched so the warm-up notice only appears
            // after a real (empty) response, not during the pre-fetch flash.
            setClansFetched(true);
        }
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

    const fetchLandingPlayers = useCallback(async (sort: PlayerBestSort = 'overall') => {
        try {
            const params = new URLSearchParams({
                mode: 'best',
                limit: String(LANDING_PLAYER_LIMIT),
                sort,
            });
            const { data: payload } = await fetchSharedJson<LandingPlayer[]>(
                withRealm(`/api/landing/players/?${params.toString()}`, realm),
                {
                    label: `Landing players (best:${sort})`,
                    ttlMs: LANDING_FETCH_TTL_MS,
                },
            );
            setPlayers(Array.isArray(payload) ? payload : []);
        } catch (err) {
            console.error('Error fetching landing players:', err);
            setPlayers([]);
        }
    }, [realm]);

    // Best players + clans are the only landing surfaces now. This refreshes
    // both (used on focus/visibility regain and on back-from-profile) and
    // (re)triggers the best-entity warm-up.
    const refreshLandingBest = useCallback(() => {
        triggerBestLandingWarmup();
        void fetchLandingPlayers(playerBestSort);
        void fetchLandingClans(clanBestSort).catch((err) => {
            console.error('Error fetching landing clans:', err);
            setClans([]);
        });
    }, [triggerBestLandingWarmup, fetchLandingPlayers, fetchLandingClans, playerBestSort, clanBestSort]);

    useEffect(() => {
        triggerBestLandingWarmup();
    }, [triggerBestLandingWarmup]);

    useEffect(() => {
        const refreshLandingData = () => {
            if (typeof document !== 'undefined' && document.visibilityState !== 'visible') {
                return;
            }

            void refreshLandingBest();
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
    }, [refreshLandingBest]);

    useEffect(() => {
        void fetchLandingClans(clanBestSort).catch((err) => {
            console.error('Error fetching landing clans:', err);
            setClans([]);
        });
    }, [clanBestSort, fetchLandingClans]);

    useEffect(() => {
        setClanBestSort('overall');
    }, [realm]);

    useEffect(() => {
        void fetchLandingPlayers(playerBestSort);
    }, [fetchLandingPlayers, playerBestSort]);

    useEffect(() => {
        setPlayerBestSort('overall');
    }, [realm]);

    useIntervalRefresh(() => {
        void fetchLandingClans(clanBestSort);
    }, LANDING_PLAYER_REFRESH_INTERVAL_MS);

    useIntervalRefresh(() => {
        void fetchLandingPlayers(playerBestSort);
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

    const visibleLandingClans = useMemo(
        () => clans.slice(0, LANDING_CLAN_LIMIT),
        [clans],
    );

    // Best clans haven't warmed yet for this realm — show the warm-up notice
    // above an empty board (there is no Recent fallback list to fall back to).
    // Gated on a completed fetch so the notice doesn't flash on cold load.
    const isClanWarmupActive = clansFetched && clans.length === 0;

    const visibleLandingPlayers = players;

    const handleSelectMember = useCallback(async (memberName: string) => {
        router.push(buildPlayerPath(memberName, realm));
    }, [router, realm]);

    useEffect(() => {
        const query = typeof window !== 'undefined'
            ? (new URLSearchParams(window.location.search).get('q') || '').trim()
            : '';
        if (!query || query === lastSubmittedSearchRef.current) {
            return;
        }

        void executePlayerSearch(query);
    }, [executePlayerSearch]);

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
        refreshLandingBest();
    }, [refreshLandingBest, resetClanHydrationAttempts]);

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
                    isLoading={isLoadingPlayer}
                />
            ) : (
                <div>
                    {error && <p className="text-red-600 dark:text-red-400">{error}</p>}

                    {/* Realm most-played-ships treemap, above the Players
                        (Recent/Best) list. Stays within the landing's
                        max-w-5xl column (the xl+ full-bleed breakout was
                        reverted — it glued the strip to the viewport's left
                        edge; see runbook-audience-device-optimization). */}
                    <div className="mt-2 pt-6">
                        <RealmTopShipsTreemapSVG
                            onSelect={(sel) => shipLeaderboardRef.current?.selectShip(sel)}
                        />
                    </div>

                    {/* Inline ship leaderboard: filter by tier+type, rank ships by
                        win rate, drill into any ship's player board in place. A
                        treemap tile click hands off here via the ref (in place);
                        tiles the board can't represent fall back to /ship/<id>. */}
                    <ShipLeaderboard ref={shipLeaderboardRef} />

                    {/* Toolbar is always visible once the landing pane is mounted —
                     keeps the empty-state UX coherent when the Best list returns
                     zero rows. Best is the only filter (no Recent toggle); its
                     sub-sort bar sits inline to the right. */}
                    {true && (
                        <div className={`${error ? 'mt-6' : 'mt-2'} pt-12`}>
                            <div className="flex flex-wrap items-center gap-2">
                                <h3 className="mr-2 text-sm font-semibold uppercase tracking-wide text-[var(--accent-mid)]">Players</h3>
                                <span
                                    className="inline-flex items-center rounded-md border border-[var(--accent-mid)] bg-[var(--accent-mid)] px-3 py-1.5 text-sm font-semibold uppercase tracking-wide text-white"
                                    aria-current="true"
                                    data-testid="player-best-pill"
                                >
                                    Best
                                </span>
                                <div
                                    className="flex items-center gap-1.5"
                                    data-testid="player-best-sort-bar"
                                >
                                    {(['overall', 'ranked', 'efficiency', 'wr', 'cb'] as const).map((sort, i) => (
                                        <React.Fragment key={sort}>
                                            {i > 0 && <span className="text-xs text-[var(--text-secondary)]">&middot;</span>}
                                            <button
                                                type="button"
                                                onClick={() => { if (playerBestSort !== sort) { setPlayerBestSort(sort); trackEvent('landing-best-sort', { entity: 'player', sort, realm }); } }}
                                                className={`text-sm font-medium transition-colors ${playerBestSort === sort ? 'text-[var(--accent-mid)] underline decoration-[var(--accent-mid)] underline-offset-4' : 'text-[var(--text-secondary)] hover:text-[var(--accent-mid)] hover:underline hover:underline-offset-4'}`}
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
                            {/* 964 = ~900px chart + the SVG container's 64px (pr-16) right gutter. */}
                            <div className="mt-1 max-w-[964px]">
                                <LandingPlayerSVG
                                    players={visibleLandingPlayers}
                                    onSelectPlayer={(player) => handleSelectMember(player.name)}
                                    theme={theme}
                                    sort={playerBestSort}
                                />
                            </div>
                            <PlayerNameGrid
                                players={visibleLandingPlayers}
                                onSelectMember={handleSelectMember}
                                ariaLabelPrefix="Show"
                                realm={realm}
                            />
                        </div>
                    )}

                    {true && (
                        <div className="mt-6 border-t border-[var(--border)] pt-6">
                            <div className="flex flex-wrap items-center gap-2">
                                <h3 className="mr-2 text-sm font-semibold uppercase tracking-wide text-[var(--accent-mid)]">Active Clans</h3>
                                <span
                                    className="inline-flex items-center rounded-md border border-[var(--accent-mid)] bg-[var(--accent-mid)] px-3 py-1.5 text-sm font-semibold uppercase tracking-wide text-white"
                                    aria-current="true"
                                    data-testid="clan-best-pill"
                                >
                                    Best
                                </span>
                                <div
                                    className="flex items-center gap-1.5"
                                    data-testid="clan-best-sort-bar"
                                >
                                    {(['overall', 'wr'] as const).map((sort, i) => (
                                        <React.Fragment key={sort}>
                                            {i > 0 && <span className="text-xs text-[var(--text-secondary)]">&middot;</span>}
                                            <button
                                                type="button"
                                                onClick={() => { if (clanBestSort !== sort) { setClanBestSort(sort); trackEvent('landing-best-sort', { entity: 'clan', sort, realm }); } }}
                                                className={`text-sm font-medium transition-colors ${clanBestSort === sort ? 'text-[var(--accent-mid)] underline decoration-[var(--accent-mid)] underline-offset-4' : 'text-[var(--text-secondary)] hover:text-[var(--accent-mid)] hover:underline hover:underline-offset-4'}`}
                                            >
                                                {sort === 'overall' ? 'Overall' : 'WR'}
                                            </button>
                                        </React.Fragment>
                                    ))}
                                    <div className="group relative inline-flex items-center">
                                        <button
                                            type="button"
                                            className="inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] text-[var(--accent-light)] transition-colors hover:text-[var(--accent-mid)] focus:outline-none focus-visible:text-[var(--accent-mid)]"
                                            aria-label="Clan ranking formula details"
                                        >
                                            <FontAwesomeIcon icon={faCircleInfo} className="text-[10px]" aria-hidden="true" />
                                        </button>
                                        <div className="pointer-events-none absolute left-0 top-full z-20 mt-2 hidden w-[27rem] max-w-[calc(100vw-2rem)] rounded-md border border-[var(--border)] bg-[var(--bg-page)] px-3 py-3 text-left text-xs normal-case tracking-normal text-[var(--text-primary)] shadow-lg group-hover:block group-focus-within:block">
                                            <p className="font-semibold uppercase tracking-wide text-[var(--accent-mid)]">Clan ranking approximations</p>
                                            <p className="mt-2 leading-5 text-[var(--text-secondary)]">
                                                Overall and WR require &gt;10 members, &ge;40% active share, &ge;5 tracked players, and &ge;50k total battles. The WR view may add a qualified clan-battle lift only when CB performance is backed by enough volume, active members, and member quality.
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
                                </div>
                            </div>
                            <div className="mt-3 max-w-[964px]">
                                {isClanWarmupActive ? (
                                    <p className="mb-3 rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-2 text-sm text-[var(--text-secondary)]">
                                        {BEST_CLAN_WARMUP_NOTICE}
                                    </p>
                                ) : null}
                                <LandingClanSVG
                                    clans={visibleLandingClans}
                                    onSelectClan={handleSelectClan}
                                    theme={theme}
                                    sort={clanBestSort}
                                />
                            </div>
                            <ClanTagGrid
                                clans={visibleLandingClans}
                                onSelectClan={handleSelectClan}
                                ariaLabelPrefix="Show"
                            />
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default PlayerSearch;