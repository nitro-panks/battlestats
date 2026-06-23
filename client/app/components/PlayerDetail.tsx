import React, { useEffect, useState } from 'react';
import { getHighestRankedLeagueName, type RankedLeagueName } from './rankedLeague';
import PlayerDetailInsightsTabs from './PlayerDetailInsightsTabs';
import HiddenAccountIcon from './HiddenAccountIcon';
import EfficiencyRankIcon, { resolveEfficiencyRankTier } from './EfficiencyRankIcon';
import LeaderCrownIcon from './LeaderCrownIcon';
import TwitchStreamerIcon from './TwitchStreamerIcon';
import PveEnjoyerIcon from './PveEnjoyerIcon';
import ActivityIcon from './ActivityIcon';
import RankedPlayerIcon from './RankedPlayerIcon';
import ClanBattleShieldIcon from './ClanBattleShieldIcon';
import ShipTopPlayerBanner, { ShipBadge } from './ShipTopPlayerBanner';
import TopShipBadges from './TopShipBadges';
import type { PlayerClanBattleSummary } from './PlayerClanBattleSeasons';
import { dispatchPlayerRouteSectionRendered, usePlayerRouteDiagnostics } from './usePlayerRouteDiagnostics';
import { useRealm } from '../context/RealmContext';
import { trackEvent } from '../lib/umami';
import wrColor from '../lib/wrColor';

interface PlayerDetailProps {
    player: {
        id: number;
        name: string;
        player_id: number;
        realm?: string;
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
        is_streamer?: boolean;
        twitch_handle?: string | null;
        twitch_url?: string | null;
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
        // Weekly top-3 finishes in a Tier-10 ship (gold/silver/bronze badges).
        ship_badges?: ShipBadge[];
        // Durable per-ship career record (append-only award ledger).
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
    isLoading?: boolean;
    // Visit-based live-update status (see usePlayerLiveRefresh). When omitted the
    // page renders exactly as before — the badge and chart re-fetch are inert.
    refreshStatus?: { phase: 'loading' | 'cooldown'; secondsRemaining: number };
    refreshNonce?: number;
}

const formatKillRatio = (killRatio: number | null): string => {
    if (killRatio == null) {
        return '—';
    }

    return killRatio.toFixed(2);
};


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
        && wrColor(current.overallWinRate) === wrColor(incoming.overallWinRate)
    );
};

const PlayerDetail: React.FC<PlayerDetailProps> = ({
    player,
    isLoading = false,
    refreshStatus,
    refreshNonce = 0,
}) => {
    const { realm } = useRealm();
    const [shareState, setShareState] = useState<'idle' | 'copied' | 'failed'>('idle');
    const pveBattles = Math.max(player.total_battles - player.pvp_battles, 0);
    const isPveEnjoyer = Boolean(player.is_pve_player);
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
    const isClanBattleEnjoyer = clanBattleSummary !== null;

    usePlayerRouteDiagnostics(player.player_id, player.name);

    useEffect(() => {
        setClanBattleSummary(getInitialClanBattleHeaderState(player));
    }, [
        player,
        player.player_id,
        player.clan_battle_header_eligible,
        player.clan_battle_header_total_battles,
        player.clan_battle_header_seasons_played,
        player.clan_battle_header_overall_win_rate,
    ]);

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
    }, [player.is_hidden, player.player_id]);

    const handleShare = async () => {
        trackEvent('player-share', { realm });
        try {
            const url = new URL(window.location.href);
            if (!url.searchParams.has('realm')) {
                url.searchParams.set('realm', realm);
            }
            await navigator.clipboard.writeText(url.toString());
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
        <>
            <div className="mb-6 border-b border-[var(--border)] pb-3" data-perf-section="player-header">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="flex min-w-0 flex-wrap items-center gap-2">
                                <h1 className="text-3xl font-semibold tracking-tight text-[var(--accent-dark)]">
                                    {player.name}
                                </h1>
                                <ActivityIcon daysSinceLastBattle={player.days_since_last_battle} size="header" />
                                {player.is_hidden ? <HiddenAccountIcon className="text-sm text-[var(--accent-light)]" /> : null}
                                {player.is_clan_leader ? <LeaderCrownIcon size="header" /> : null}
                                {isPveEnjoyer ? <PveEnjoyerIcon size="header" /> : null}
                                {isRankedEnjoyer ? <RankedPlayerIcon league={highestRankedLeague} size="header" /> : null}
                                {isClanBattleEnjoyer && clanBattleSummary ? <ClanBattleShieldIcon winRate={clanBattleSummary.overallWinRate} size="header" /> : null}
                                {hasEfficiencyRankIcon && efficiencyRankTier ? <EfficiencyRankIcon tier={efficiencyRankTier} percentile={player.efficiency_rank_percentile} populationSize={player.efficiency_rank_population_size} size="header" /> : null}
                                {!player.is_hidden && <TopShipBadges badges={player.ship_badges} realm={player.realm} size="header" />}
                            </div>
                            <div className="flex items-center gap-2 self-start">
                                <button
                                    type="button"
                                    onClick={handleShare}
                                    className="rounded-md border border-[var(--border)] px-3 py-1.5 text-sm font-medium text-[var(--accent-mid)] transition-colors hover:bg-[var(--accent-faint)]"
                                    aria-label="Copy shareable player URL"
                                >
                                    Share
                                </button>
                                {shareState === 'copied' ? (
                                    <span className="text-xs font-medium text-[var(--accent-mid)]">Copied</span>
                                ) : null}
                                {shareState === 'failed' ? (
                                    <span className="text-xs font-medium text-red-500">Copy failed</span>
                                ) : null}
                            </div>
                        </div>
                        <div className="mt-1 flex items-center justify-between gap-3">
                            <p className="text-sm text-[var(--accent-light)]">
                                Last played {player.days_since_last_battle} days ago
                            </p>
                            {/* Right group: refresh status floats left of the streamer link
                                (which stays right-most); refresh alone is right-justified. */}
                            <div className="flex items-center gap-3">
                                {refreshStatus && !player.is_hidden ? (
                                    refreshStatus.phase === 'loading' ? (
                                        <span
                                            className="rainbow-text text-xs font-semibold"
                                            aria-live="polite"
                                            data-testid="live-refresh-status"
                                        >
                                            {/* "Updating…" not "Loading…": the page already
                                                shows full content (profile + cached battles);
                                                this pill marks a background freshness top-up of
                                                the whole profile. "Loading" read as a hang/timeout
                                                to users when the refresh took tens of seconds. */}
                                            Updating…
                                        </span>
                                    ) : refreshStatus.secondsRemaining > 0 ? (
                                        <span
                                            className="text-xs font-medium text-[var(--accent-light)]"
                                            aria-live="polite"
                                            data-testid="live-refresh-status"
                                        >
                                            {`Next update: ${Math.ceil(refreshStatus.secondsRemaining / 60)} min`}
                                        </span>
                                    ) : null
                                ) : null}
                                {player.is_streamer && player.twitch_handle && player.twitch_url ? (
                                    <a
                                        href={player.twitch_url}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="inline-flex items-center gap-1.5 text-sm font-medium text-[var(--accent-mid)] hover:text-[var(--accent-dark)] hover:underline"
                                        aria-label={`Twitch channel for ${player.twitch_handle}`}
                                    >
                                        <span>{player.twitch_handle}</span>
                                        <TwitchStreamerIcon size="header" titleText={`Watch ${player.twitch_handle} on Twitch`} ariaLabel="Twitch" />
                                    </a>
                                ) : null}
                            </div>
                        </div>
                    </div>

                    {player.is_hidden ? (
                        <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-900 dark:bg-amber-950/40">
                            <p className="text-sm font-medium text-amber-800 dark:text-amber-300">
                                This player&apos;s stats are hidden.
                            </p>
                            <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                                The player has set their profile to private. Detailed statistics and charts are not available.
                            </p>
                        </div>
                    ) : (
                        <>
                            <div className="mt-4 grid grid-cols-2 gap-3 xl:grid-cols-4" data-perf-section="summary-cards">
                                <div
                                    className="flex min-h-[108px] flex-col rounded-md bg-[var(--accent-faint)] p-3"
                                    style={{ border: `1px solid ${wrColor(player.pvp_ratio)}` }}
                                >
                                    <p className="text-xs uppercase tracking-wide text-[var(--accent-light)]">Win Rate</p>
                                    <div className="flex flex-1 items-center justify-center">
                                        <p className="text-center text-2xl font-semibold text-[var(--accent-dark)]">{player.pvp_ratio}%</p>
                                    </div>
                                </div>
                                <div className="flex min-h-[108px] flex-col rounded-md bg-[var(--accent-faint)] p-3">
                                    <p className="text-xs uppercase tracking-wide text-[var(--accent-light)]">PvP Battles</p>
                                    <div className="flex flex-1 items-center justify-center">
                                        <p className="text-center text-2xl font-semibold text-[var(--accent-dark)]">{player.pvp_battles.toLocaleString()}</p>
                                    </div>
                                </div>
                                <div className="flex min-h-[108px] flex-col rounded-md bg-[var(--accent-faint)] p-3">
                                    <p className="text-xs uppercase tracking-wide text-[var(--accent-light)]">Survival</p>
                                    <div className="flex flex-1 items-center justify-center">
                                        <p className="text-center text-2xl font-semibold text-[var(--accent-dark)]">{player.pvp_survival_rate}%</p>
                                    </div>
                                </div>
                                <div className="flex min-h-[108px] flex-col rounded-md bg-[var(--accent-faint)] p-3">
                                    <p className="text-xs uppercase tracking-wide text-[var(--accent-light)]">KDR</p>
                                    <div className="flex flex-1 items-center justify-center">
                                        <p className="text-center text-2xl font-semibold text-[var(--accent-dark)]">{formatKillRatio(player.actual_kdr ?? null)}</p>
                                    </div>
                                </div>
                            </div>

                            <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-1 text-sm text-[var(--accent-light)]">
                                <p>Total Battles: <span className="font-medium text-[var(--accent-mid)]">{player.total_battles.toLocaleString()}</span></p>
                                <p>PvP Wins: <span className="font-medium text-[var(--accent-mid)]">{player.pvp_wins.toLocaleString()}</span></p>
                                <p>Last Battle Date: <span className="font-medium text-[var(--accent-mid)]">{player.last_battle_date}</span></p>
                                <p>PvE Battles: <span className="font-medium text-[var(--accent-mid)]">{pveBattles.toLocaleString()}</span></p>
                            </div>

                            {!player.is_hidden ? (
                                <ShipTopPlayerBanner badges={player.ship_badges ?? []} realm={player.realm} />
                            ) : null}
                            <div className="mt-6" />
                            <PlayerDetailInsightsTabs
                                playerId={player.player_id}
                                playerName={player.name}
                                refreshNonce={refreshNonce}
                                pvpRatio={player.pvp_ratio}
                                pvpSurvivalRate={player.pvp_survival_rate}
                                pvpBattles={player.pvp_battles}
                                playerScore={player.player_score}
                                hasKnownRankedGames={hasKnownRankedGames}
                                hasClan={Boolean(player.clan_id)}
                                efficiencyRows={player.efficiency_json}
                                onClanBattleSummaryChange={handleClanBattleSummaryChange}
                                isLoading={isLoading}
                            />
                        </>
                    )}
        </>
    );

};

export default PlayerDetail;
