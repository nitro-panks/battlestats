import React, { useEffect, useState } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faBed, faCrown, faRobot, faShieldHalved, faStar } from '@fortawesome/free-solid-svg-icons';
import dynamic from 'next/dynamic';
import ClanSVG from './ClanSVG';
import DeferredSection from './DeferredSection';
import { resilientDynamicImport } from './resilientDynamicImport';
import { getHighestRankedLeagueName, getRankedLeagueTooltip, getRankedLeagueColor, type RankedLeagueName } from './rankedLeague';
import PlayerDetailInsightsTabs from './PlayerDetailInsightsTabs';
import { useClanMembers } from './useClanMembers';
import HiddenAccountIcon from './HiddenAccountIcon';
import EfficiencyRankIcon, { resolveEfficiencyRankTier } from './EfficiencyRankIcon';
import type { PlayerClanBattleSummary } from './PlayerClanBattleSeasons';
import { dispatchPlayerRouteSectionRendered, usePlayerRouteDiagnostics } from './usePlayerRouteDiagnostics';

interface PlayerDetailProps {
    player: {
        id: number;
        name: string;
        player_id: number;
        kill_ratio: number | null;
        actual_kdr?: number | null;
        player_score: number | null;
        total_battles: number;
        pvp_battles: number;
        pvp_wins: number;
        pvp_losses: number;
        pvp_ratio: number;
        pvp_survival_rate: number;
        wins_survival_rate: number | null;
        creation_date: string;
        days_since_last_battle: number;
        last_battle_date: string;
        recent_games: object;
        is_hidden: boolean;
        stats_updated_at: string;
        last_fetch: string;
        last_lookup: string | null;
        clan: number;
        clan_name: string;
        clan_tag: string | null;
        clan_id: number;
        is_clan_leader?: boolean;
        is_pve_player?: boolean;
        highest_ranked_league?: RankedLeagueName | null;
        efficiency_rank_percentile?: number | null;
        efficiency_rank_tier?: 'E' | 'I' | 'II' | 'III' | null;
        has_efficiency_rank_icon?: boolean;
        efficiency_rank_population_size?: number | null;
        efficiency_rank_updated_at?: string | null;
        clan_battle_header_eligible?: boolean;
        clan_battle_header_total_battles?: number | null;
        clan_battle_header_seasons_played?: number | null;
        clan_battle_header_overall_win_rate?: number | null;
        clan_battle_header_updated_at?: string | null;
        verdict: string | null;
        randoms_json?: Array<{
            ship_name?: string | null;
            ship_chart_name?: string | null;
            ship_type?: string | null;
            ship_tier?: number | null;
            pvp_battles?: number | null;
            wins?: number | null;
            win_ratio?: number | null;
        }> | null;
        efficiency_json?: Array<{
            ship_id?: number | null;
            top_grade_class?: number | null;
            top_grade_label?: string | null;
            badge_label?: string | null;
            ship_name?: string | null;
            ship_chart_name?: string | null;
            ship_type?: string | null;
            ship_tier?: number | null;
            nation?: string | null;
        }> | null;
        ranked_json?: Array<{
            total_battles?: number | null;
            total_wins?: number | null;
            win_rate?: number | null;
            highest_league?: number | null;
            highest_league_name?: RankedLeagueName | null;
        }> | null;
    };
    onBack: () => void;
    onSelectMember: (memberName: string) => void;
    onSelectClan: (clanId: number, clanName: string) => void;
    isLoading?: boolean;
}

const LoadingPanel: React.FC<{ label: string; minHeight?: number }> = ({ label, minHeight = 220 }) => (
    <div
        className="flex animate-pulse items-center justify-center rounded-md border border-[#dbe9f6] bg-[#f7fbff] text-sm text-[#6baed6]"
        style={{ minHeight }}
    >
        {label}
    </div>
);

const ClanMembers = dynamic(() => resilientDynamicImport(() => import('./ClanMembers'), 'PlayerDetail-ClanMembers'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading clan members..." minHeight={96} />,
});

const PlayerClanBattleSeasons = dynamic(() => resilientDynamicImport(() => import('./PlayerClanBattleSeasons'), 'PlayerDetail-PlayerClanBattleSeasons'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading clan battle seasons..." minHeight={180} />,
});

const selectColorByWR = (winRatio: number): string => {
    if (winRatio > 65) return "#810c9e";  // super unicum
    if (winRatio >= 60) return "#D042F3";  // unicum
    if (winRatio >= 56) return "#3182bd";  // great
    if (winRatio >= 54) return "#74c476";  // very good
    if (winRatio >= 52) return "#a1d99b";  // good
    if (winRatio >= 50) return "#fed976";  // average
    if (winRatio >= 45) return "#fd8d3c";  // below average
    return "#a50f15";                       // bad
};

const formatKillRatio = (killRatio: number | null): string => {
    if (killRatio == null) {
        return '—';
    }

    return killRatio.toFixed(2);
};

const PLAYSTYLE_HELPER_TEXT: Record<string, string> = {
    Sealord: 'Owns the map, dictates the pace, dominates, turns tables and wins.',
    Assassin: 'Wins relentlessly, wastes little, and closes games with intent.',
    Kraken: 'Wins violently,and stacks kills before disappearing into the depths.',
    Stalwart: 'Steady under pressure, useful in every phase, and good for more than raw damage.',
    Daredevil: 'Pushes recklessly, burns brightly, and still finds ways to win.',
    Warrior: 'Performs well, stays alive, and keeps steady pressure on the fight.',
    Raider: 'Strikes where the line is thin, trades fast, and lives off opportunism more than control.',
    Flotsam: 'Stays afloat, contributes enough, and remains useful in most fights.',
    Jetsam: 'Gets chewed up early, loses impact fast, and rarely shapes the outcome.',
    Survivor: 'Stays alive, avoids disaster, sometimes the deciding factor.',
    Drifter: 'Floats through the match, avoids some danger, but rarely shapes the outcome.',
    Pirate: 'Hangs around longer than expected, steals value, and survives on nuisance more than strength.',
    Potato: 'Sinks early, lands little, and leaves the team short-handed.',
    'Hot Potato': 'Stays alive longer than they should, given the circumstances.',
    'Leroy Jenkins': 'Charges in blind, detonates early, and flames the whole team in chat for the rest of the game.',
    Recruit: 'Has too few battles to read; the story is just beginning.',
};

const SHOW_PLAYSTYLE_PANEL = false;

const HeaderLeaderCrown = () => (
    <span
        title="Clan leader"
        aria-label="Clan leader"
        className="inline-flex items-center cursor-help"
    >
        <FontAwesomeIcon
            icon={faCrown}
            className="text-sm text-amber-500"
            aria-hidden="true"
        />
    </span>
);

const HeaderPveRobot = () => (
    <span
        title="pve enjoyer"
        aria-label="pve enjoyer"
        className="inline-flex items-center cursor-help"
    >
        <FontAwesomeIcon
            icon={faRobot}
            className="text-sm text-slate-500"
            aria-hidden="true"
        />
    </span>
);

const HeaderSleepyBed = () => (
    <span
        title="inactive for over a year"
        aria-label="inactive for over a year"
        className="inline-flex items-center cursor-help"
    >
        <FontAwesomeIcon
            icon={faBed}
            className="text-sm text-slate-400"
            aria-hidden="true"
        />
    </span>
);

const HeaderRankedStar: React.FC<{ league: RankedLeagueName | null }> = ({ league }) => (
    <span
        title={getRankedLeagueTooltip(league)}
        aria-label={getRankedLeagueTooltip(league)}
        className="inline-flex items-center cursor-help"
    >
        <FontAwesomeIcon
            icon={faStar}
            className="text-sm"
            style={{ color: getRankedLeagueColor(league) }}
            aria-hidden="true"
        />
    </span>
);

const HeaderClanBattleShield: React.FC<{ winRate: number }> = ({ winRate }) => (
    <span
        title={`clan battle enjoyer · ${winRate.toFixed(1)}% WR`}
        aria-label={`clan battle enjoyer ${winRate.toFixed(1)} percent WR`}
        className="inline-flex items-center cursor-help"
    >
        <FontAwesomeIcon
            icon={faShieldHalved}
            className="text-sm"
            style={{ color: selectColorByWR(winRate) }}
            aria-hidden="true"
        />
    </span>
);

const buildClanBattleHeaderState = (
    summary: PlayerClanBattleSummary | null | undefined,
): PlayerClanBattleSummary | null => {
    if (!summary) {
        return null;
    }

    if (summary.totalBattles < 40 || summary.seasonsPlayed < 2) {
        return null;
    }

    return {
        seasonsPlayed: summary.seasonsPlayed,
        totalBattles: summary.totalBattles,
        overallWinRate: Number(summary.overallWinRate.toFixed(1)),
    };
};

const getInitialClanBattleHeaderState = (
    player: PlayerDetailProps['player'],
): PlayerClanBattleSummary | null => {
    if (!player.clan_battle_header_eligible) {
        return null;
    }

    const totalBattles = Number(player.clan_battle_header_total_battles ?? 0);
    const seasonsPlayed = Number(player.clan_battle_header_seasons_played ?? 0);
    const overallWinRate = player.clan_battle_header_overall_win_rate;

    if (!Number.isFinite(totalBattles) || !Number.isFinite(seasonsPlayed) || overallWinRate == null) {
        return null;
    }

    return buildClanBattleHeaderState({
        totalBattles,
        seasonsPlayed,
        overallWinRate,
    });
};

const areEquivalentClanBattleHeaderStates = (
    current: PlayerClanBattleSummary | null,
    incoming: PlayerClanBattleSummary | null,
): boolean => {
    if (current === incoming) {
        return true;
    }

    if (current == null || incoming == null) {
        return current == null && incoming == null;
    }

    return (
        current.overallWinRate === incoming.overallWinRate
        && selectColorByWR(current.overallWinRate) === selectColorByWR(incoming.overallWinRate)
    );
};

const PlayerDetail: React.FC<PlayerDetailProps> = ({
    player,
    onBack,
    onSelectMember,
    onSelectClan,
    isLoading = false,
}) => {
    const [shareState, setShareState] = useState<'idle' | 'copied' | 'failed'>('idle');
    const pveBattles = Math.max(player.total_battles - player.pvp_battles, 0);
    const isPveEnjoyer = Boolean(player.is_pve_player);
    const isSleepyPlayer = player.days_since_last_battle > 365;
    const rankedBattleCount = Array.isArray(player.ranked_json)
        ? player.ranked_json.reduce((total, row) => total + Math.max(row?.total_battles || 0, 0), 0)
        : 0;
    const isRankedEnjoyer = rankedBattleCount > 100;
    const highestRankedLeague = player.highest_ranked_league ?? getHighestRankedLeagueName(player.ranked_json);
    const efficiencyRankTier = !player.is_hidden
        ? resolveEfficiencyRankTier(player.efficiency_rank_tier, player.has_efficiency_rank_icon)
        : null;
    const hasEfficiencyRankIcon = !player.is_hidden && efficiencyRankTier === 'E';
    const hasKnownRankedGames = Array.isArray(player.ranked_json)
        ? player.ranked_json.some((row) => (row?.total_battles || 0) > 0)
        : true;
    const [clanBattleSummary, setClanBattleSummary] = useState<PlayerClanBattleSummary | null>(() => getInitialClanBattleHeaderState(player));
    const [shouldLoadClanMembers, setShouldLoadClanMembers] = useState(false);
    const isClanBattleEnjoyer = clanBattleSummary !== null;
    const { members: clanMembers, loading: clanMembersLoading, error: clanMembersError } = useClanMembers(player.clan_id || null, shouldLoadClanMembers);

    usePlayerRouteDiagnostics(player.player_id, player.name);


    useEffect(() => {
        setClanBattleSummary(getInitialClanBattleHeaderState(player));
    }, [
        player.player_id,
        player.clan_battle_header_eligible,
        player.clan_battle_header_total_battles,
        player.clan_battle_header_seasons_played,
        player.clan_battle_header_overall_win_rate,
    ]);

    useEffect(() => {
        if (!player.clan_id) {
            setShouldLoadClanMembers(false);
            return;
        }

        setShouldLoadClanMembers(false);

        let timeoutId: number | null = null;
        let idleCallbackId: number | null = null;
        const activateClanMembers = () => setShouldLoadClanMembers(true);

        if (typeof window !== 'undefined' && typeof window.requestIdleCallback === 'function') {
            idleCallbackId = window.requestIdleCallback(activateClanMembers, { timeout: 1200 });
        } else if (typeof window !== 'undefined') {
            timeoutId = window.setTimeout(activateClanMembers, 250);
        }

        return () => {
            if (idleCallbackId != null && typeof window !== 'undefined' && typeof window.cancelIdleCallback === 'function') {
                window.cancelIdleCallback(idleCallbackId);
            }

            if (timeoutId != null && typeof window !== 'undefined') {
                window.clearTimeout(timeoutId);
            }
        };
    }, [player.clan_id, player.player_id]);

    useEffect(() => {
        if (shareState === 'idle') {
            return;
        }

        const timeoutId = window.setTimeout(() => {
            setShareState('idle');
        }, 1800);

        return () => window.clearTimeout(timeoutId);
    }, [shareState]);

    useEffect(() => {
        dispatchPlayerRouteSectionRendered('player-header', player.player_id, 'immediate');

        if (!player.is_hidden) {
            dispatchPlayerRouteSectionRendered('summary-cards', player.player_id, 'immediate');
        }

        if (player.clan_id) {
            dispatchPlayerRouteSectionRendered('clan-plot', player.player_id, 'immediate');
        }
    }, [player.clan_id, player.is_hidden, player.player_id]);

    const handleShare = async () => {
        try {
            await navigator.clipboard.writeText(window.location.href);
            setShareState('copied');
        } catch (error) {
            console.error('Failed to copy player URL:', error);
            setShareState('failed');
        }
    };

    const handleClanBattleSummaryChange = (nextSummary: PlayerClanBattleSummary | null) => {
        const nextHeaderState = buildClanBattleHeaderState(nextSummary);

        setClanBattleSummary((current) => {
            if (areEquivalentClanBattleHeaderStates(current, nextHeaderState)) {
                return current;
            }

            return nextHeaderState;
        });
    };

    return (
        <div className="relative overflow-hidden bg-white p-6">
            {isLoading ? (
                <div className="absolute inset-0 z-20 flex items-start justify-center bg-white/70 pt-6">
                    <div className="rounded-md border border-gray-200 bg-white px-3 py-1 text-sm font-medium text-gray-700 shadow-sm">
                        Loading player...
                    </div>
                </div>
            ) : null}
            <div className="grid grid-cols-[350px_1fr] gap-4">
                {/* First Column */}
                <div>
                    <div className="mb-4 pb-1">
                        {player.clan_id ? (
                            <button
                                type="button"
                                onClick={() => onSelectClan(player.clan_id, player.clan_name || "Clan")}
                                className="mt-1 text-xl font-semibold text-[#2171b5] underline-offset-4 hover:underline"
                                aria-label={`Open clan page for ${player.clan_name || "clan"}`}
                            >
                                {player.clan_tag ? `[${player.clan_tag}] ` : ''}{player.clan_name || 'Clan'}
                            </button>
                        ) : (
                            <h2 className="mt-1 text-xl font-semibold text-[#2171b5]">No Clan</h2>
                        )}
                    </div>
                    {player.clan_id ? (
                        <>
                            <div id="clan_plot_container" className="mb-5 min-h-[280px]" data-perf-section="clan-plot">
                                <ClanSVG
                                    clanId={player.clan_id}
                                    onSelectMember={onSelectMember}
                                    highlightedPlayerName={player.name}
                                    svgHeight={280}
                                    membersData={clanMembers}
                                />

                            </div>
                            <DeferredSection
                                className="pt-5"
                                minHeight={96}
                                placeholder={<LoadingPanel label="Preparing clan members..." minHeight={96} />}
                                playerId={player.player_id}
                                rootMargin="80px 0px"
                                sectionId="clan-members"
                            >
                                <div id="clan_members_container">
                                    <ClanMembers members={clanMembers} loading={clanMembersLoading} error={clanMembersError} onSelectMember={onSelectMember} layout="stacked" />
                                </div>
                            </DeferredSection>
                        </>
                    ) : (
                        <>
                            <p className="text-sm text-gray-500">No clan data available</p>
                        </>
                    )}
                </div>

                {/* Second Column */}
                <div className="min-w-0 text-left border-l border-[#c6dbef] pl-4">
                    <div className="mb-3 border-b border-[#c6dbef] pb-3" data-perf-section="player-header">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="flex min-w-0 flex-wrap items-center gap-2">
                                <h1 className="text-3xl font-semibold tracking-tight text-[#084594]">
                                    {player.name}
                                </h1>
                                {player.is_hidden ? <HiddenAccountIcon className="text-sm text-[#6baed6]" /> : null}
                                {player.is_clan_leader ? <HeaderLeaderCrown /> : null}
                                {isPveEnjoyer ? <HeaderPveRobot /> : null}
                                {isSleepyPlayer ? <HeaderSleepyBed /> : null}
                                {isRankedEnjoyer ? <HeaderRankedStar league={highestRankedLeague} /> : null}
                                {isClanBattleEnjoyer && clanBattleSummary ? <HeaderClanBattleShield winRate={clanBattleSummary.overallWinRate} /> : null}
                                {hasEfficiencyRankIcon && efficiencyRankTier ? <EfficiencyRankIcon tier={efficiencyRankTier} percentile={player.efficiency_rank_percentile} populationSize={player.efficiency_rank_population_size} size="header" /> : null}
                            </div>
                            <div className="flex items-center gap-2 self-start">
                                <button
                                    type="button"
                                    onClick={handleShare}
                                    className="rounded-md border border-[#c6dbef] px-3 py-1.5 text-sm font-medium text-[#2171b5] transition-colors hover:bg-[#eff3ff]"
                                    aria-label="Copy shareable player URL"
                                >
                                    Share
                                </button>
                                {shareState === 'copied' ? (
                                    <span className="text-xs font-medium text-[#2171b5]">Copied</span>
                                ) : null}
                                {shareState === 'failed' ? (
                                    <span className="text-xs font-medium text-[#b91c1c]">Copy failed</span>
                                ) : null}
                            </div>
                        </div>
                        <p className="mt-1 text-sm text-[#4292c6]">
                            Last played {player.days_since_last_battle} days ago
                        </p>
                    </div>

                    {player.is_hidden ? (
                        <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-3">
                            <p className="text-sm font-medium text-amber-800">
                                This player&apos;s stats are hidden.
                            </p>
                            <p className="mt-1 text-xs text-amber-700">
                                The player has set their profile to private. Detailed statistics and charts are not available.
                            </p>
                        </div>
                    ) : (
                        <>
                            <div className="mt-4 grid grid-cols-2 gap-3 xl:grid-cols-4" data-perf-section="summary-cards">
                                <div
                                    className="flex min-h-[108px] flex-col rounded-md bg-[#eff3ff] p-3"
                                    style={{ border: `1px solid ${selectColorByWR(player.pvp_ratio)}` }}
                                >
                                    <p className="text-xs uppercase tracking-wide text-[#4292c6]">Win Rate</p>
                                    <div className="flex flex-1 items-center justify-center">
                                        <p className="text-center text-2xl font-semibold text-[#084594]">{player.pvp_ratio}%</p>
                                    </div>
                                </div>
                                <div className="flex min-h-[108px] flex-col rounded-md bg-[#eff3ff] p-3">
                                    <p className="text-xs uppercase tracking-wide text-[#4292c6]">PvP Battles</p>
                                    <div className="flex flex-1 items-center justify-center">
                                        <p className="text-center text-2xl font-semibold text-[#084594]">{player.pvp_battles.toLocaleString()}</p>
                                    </div>
                                </div>
                                <div className="flex min-h-[108px] flex-col rounded-md bg-[#eff3ff] p-3">
                                    <p className="text-xs uppercase tracking-wide text-[#4292c6]">Survival</p>
                                    <div className="flex flex-1 items-center justify-center">
                                        <p className="text-center text-2xl font-semibold text-[#084594]">{player.pvp_survival_rate}%</p>
                                    </div>
                                </div>
                                <div className="flex min-h-[108px] flex-col rounded-md bg-[#eff3ff] p-3">
                                    <p className="text-xs uppercase tracking-wide text-[#4292c6]">KDR</p>
                                    <div className="flex flex-1 items-center justify-center">
                                        <p className="text-center text-2xl font-semibold text-[#084594]">{formatKillRatio(player.actual_kdr ?? null)}</p>
                                    </div>
                                </div>
                            </div>

                            <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-1 text-sm text-[#4292c6]">
                                <p>Total Battles: <span className="font-medium text-[#2171b5]">{player.total_battles.toLocaleString()}</span></p>
                                <p>PvP Wins: <span className="font-medium text-[#2171b5]">{player.pvp_wins.toLocaleString()}</span></p>
                                <p>Last Battle Date: <span className="font-medium text-[#2171b5]">{player.last_battle_date}</span></p>
                                <p>PvE Battles: <span className="font-medium text-[#2171b5]">{pveBattles.toLocaleString()}</span></p>
                            </div>

                            {SHOW_PLAYSTYLE_PANEL && player.verdict && (
                                <div className="mt-4 rounded-md border border-[#dbe9f6] bg-[#f7fbff] px-4 py-3">
                                    <p className="text-sm font-medium text-[#334155]">Playstyle: <span className="font-semibold text-[#084594]">{player.verdict}</span></p>
                                    {PLAYSTYLE_HELPER_TEXT[player.verdict] ? (
                                        <p className="mt-1 text-xs text-[#6baed6]">{PLAYSTYLE_HELPER_TEXT[player.verdict]}</p>
                                    ) : null}
                                </div>
                            )}
                            <PlayerDetailInsightsTabs
                                playerId={player.player_id}
                                pvpRatio={player.pvp_ratio}
                                pvpSurvivalRate={player.pvp_survival_rate}
                                pvpBattles={player.pvp_battles}
                                hasKnownRankedGames={hasKnownRankedGames}
                                hasClan={Boolean(player.clan_id)}
                                efficiencyRows={player.efficiency_json}
                                onClanBattleSummaryChange={handleClanBattleSummaryChange}
                                isLoading={isLoading}
                            />
                        </>
                    )}
                </div>
            </div>
            <div className="mt-8 pt-5">
                <button
                    type="button"
                    onClick={onBack}
                    className="inline-flex items-center rounded-md border border-[#2171b5] px-4 py-2 text-sm font-medium text-[#2171b5] transition-colors hover:bg-[#eff3ff]"
                    aria-label="Return to landing page"
                >
                    Back
                </button>
            </div>
        </div>
    );

};

export default PlayerDetail;
