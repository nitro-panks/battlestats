import React, { useCallback, useEffect, useState } from 'react';
import dynamic from 'next/dynamic';
import BattleHistoryCard, {
    BATTLE_HISTORY_FETCH_TTL_MS,
    battleHistoryCacheKey,
    battleHistoryFetchUrl,
    battleHistoryIndicatesActivity,
    type BattleHistoryPayload,
} from './BattleHistoryCard';
import PlayerEfficiencyBadges from './PlayerEfficiencyBadges';
import LoadingPanel from './LoadingPanel';
import SectionHeadingWithTooltip from './SectionHeadingWithTooltip';
import { resilientDynamicImport } from './resilientDynamicImport';
import type { PlayerClanBattleSummary } from './PlayerClanBattleSeasons';
import type { TierTypePayload } from './playerProfileChartData';
import { deriveTierRowsFromTierTypePayload, deriveTypeRowsFromTierTypePayload } from './playerProfileChartData';
import { dispatchPlayerRouteSectionRendered } from './usePlayerRouteDiagnostics';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { decrementChartFetches, fetchSharedJson, incrementChartFetches, isAbortError } from '../lib/sharedJsonFetch';
import { usePlayerRequestSignal } from '../context/PlayerRequestScopeContext';
import { useTheme } from '../context/ThemeContext';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';
import { trackEvent } from '../lib/umami';

type InsightsTabId = 'activity' | 'profile' | 'ships' | 'ranked' | 'career' | 'badges' | 'population';

interface PlayerDetailInsightsTabsProps {
    playerId: number;
    // Battle-history (Activity tab) is keyed by player name + realm, not id.
    playerName: string;
    pvpRatio: number;
    pvpSurvivalRate: number;
    pvpBattles: number;
    playerScore: number | null;
    hasKnownRankedGames: boolean;
    hasClan: boolean;
    efficiencyRows?: Array<{
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
    onClanBattleSummaryChange?: (summary: PlayerClanBattleSummary | null) => void;
    onWarmupSettled?: () => void;
    isLoading?: boolean;
    // Bumped by the live-update poll when fresh stats land; folded into the
    // chart cacheKeys + fetch deps so the tabs re-fetch instead of serving the
    // settled (pre-refresh) cache. 0 = inert (no live refresh).
    refreshNonce?: number;
}

const RandomsSVG = dynamic(() => resilientDynamicImport(() => import('./RandomsSVG'), 'PlayerDetailInsightsTabs-RandomsSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading top ships..." minHeight={500} />,
});

const RankedSeasons = dynamic(() => resilientDynamicImport(() => import('./RankedSeasons'), 'PlayerDetailInsightsTabs-RankedSeasons'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading ranked seasons..." minHeight={220} />,
});

const RankedWRBattlesHeatmapSVG = dynamic(() => resilientDynamicImport(() => import('./RankedWRBattlesHeatmapSVG'), 'PlayerDetailInsightsTabs-RankedWRBattlesHeatmapSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading ranked heatmap..." minHeight={280} />,
});

const PlayerClanBattleSeasons = dynamic(() => resilientDynamicImport(() => import('./PlayerClanBattleSeasons'), 'PlayerDetailInsightsTabs-PlayerClanBattleSeasons'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading clan battle seasons..." minHeight={220} />,
});

const TierSVG = dynamic(() => resilientDynamicImport(() => import('./TierSVG'), 'PlayerDetailInsightsTabs-TierSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading tier chart..." minHeight={300} />,
});

const TypeSVG = dynamic(() => resilientDynamicImport(() => import('./TypeSVG'), 'PlayerDetailInsightsTabs-TypeSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading ship type chart..." minHeight={192} />,
});

const TierTypeHeatmapSVG = dynamic(() => resilientDynamicImport(() => import('./TierTypeHeatmapSVG'), 'PlayerDetailInsightsTabs-TierTypeHeatmapSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading tier vs type heatmap..." minHeight={332} />,
});

const WRDistributionSVG = dynamic(() => resilientDynamicImport(() => import('./WRDistributionSVG'), 'PlayerDetailInsightsTabs-WRDistributionSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading win rate distribution..." minHeight={348} />,
});

const BattlesDistributionSVG = dynamic(() => resilientDynamicImport(() => import('./BattlesDistributionSVG'), 'PlayerDetailInsightsTabs-BattlesDistributionSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading battles distribution..." minHeight={284} />,
});

const PlayerScoreDistributionSVG = dynamic(() => resilientDynamicImport(() => import('./PlayerScoreDistributionSVG'), 'PlayerDetailInsightsTabs-PlayerScoreDistributionSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading score distribution..." minHeight={284} />,
});

const TAB_CONFIG: Array<{ id: InsightsTabId; label: string; panelLabel: string; minHeight: number; }> = [
    // Order reflects measured Umami tab-click demand (90d, 2026-07-08): Activity
    // stays first as the default landing tab; the remaining tabs are ranked by
    // click volume — Ships > Profile > Population > Efficiency > Ranked > Clan Battles.
    { id: 'activity', label: 'Activity', panelLabel: 'Recent battle activity', minHeight: 420 },
    { id: 'ships', label: 'Ships', panelLabel: 'Ship insights', minHeight: 560 },
    { id: 'profile', label: 'Profile', panelLabel: 'Profile insights', minHeight: 920 },
    { id: 'population', label: 'Population', panelLabel: 'Population insights', minHeight: 720 },
    { id: 'badges', label: 'Efficiency', panelLabel: 'Efficiency insights', minHeight: 360 },
    { id: 'ranked', label: 'Ranked', panelLabel: 'Ranked insights', minHeight: 900 },
    { id: 'career', label: 'Clan Battles', panelLabel: 'Clan battles insights', minHeight: 280 },
];

const panelSectionIdByTab: Record<InsightsTabId, string> = {
    activity: 'insights-activity',
    population: 'insights-population',
    ships: 'insights-ships',
    ranked: 'insights-ranked',
    profile: 'insights-profile',
    badges: 'insights-badges',
    career: 'insights-career',
};

// Umami event name per tab — value baked into the name (readable label, not the
// internal id) so each tab reads as a distinct row in the realtime feed.
const insightsTabEventByTab: Record<InsightsTabId, string> = {
    activity: 'player-insights-activity',
    ships: 'player-insights-ships',
    profile: 'player-insights-profile',
    ranked: 'player-insights-ranked',
    career: 'player-insights-clan-battles',
    badges: 'player-insights-efficiency',
    population: 'player-insights-population',
};

const TAB_DATA_WARMUP_DELAY_MS = 250;
const TAB_DATA_WARMUP_IDLE_TIMEOUT_MS = 1500;
const PROFILE_FETCH_RETRY_DELAY_MS = 350;
const PROFILE_PENDING_RETRY_DELAY_MS = 1500;
const PROFILE_PENDING_RETRY_LIMIT = 5;
const PROFILE_WARMING_RETRY_DELAY_MS = 30_000;

const delay = (timeoutMs: number): Promise<void> => new Promise((resolve) => {
    window.setTimeout(resolve, timeoutMs);
});

const PlayerDetailInsightsTabs: React.FC<PlayerDetailInsightsTabsProps> = ({
    playerId,
    playerName,
    pvpRatio,
    pvpSurvivalRate,
    pvpBattles,
    playerScore,
    hasKnownRankedGames,
    hasClan,
    efficiencyRows = null,
    onClanBattleSummaryChange,
    onWarmupSettled,
    isLoading = false,
    refreshNonce = 0,
}) => {
    const { theme } = useTheme();
    const { realm } = useRealm();
    const requestSignal = usePlayerRequestSignal();
    const [activeTab, setActiveTab] = useState<InsightsTabId>('activity');
    // null = unknown (still resolving); true/false once the Activity card's first
    // payload lands. Drives the default-tab choice and the dark Activity tab.
    const [activityAvailable, setActivityAvailable] = useState<boolean | null>(null);
    // null = unknown; set by the Ranked tab's battle-history card. false hides
    // the "Recent Ranked Battles" section for players with no ranked history.
    const [rankedHistoryAvailable, setRankedHistoryAvailable] = useState<boolean | null>(null);
    const [showRankedHeatmap, setShowRankedHeatmap] = useState(hasKnownRankedGames);
    const [profileChartPayload, setProfileChartPayload] = useState<TierTypePayload | null>(null);
    const [profileChartState, setProfileChartState] = useState<'idle' | 'loading' | 'ready' | 'warming' | 'error'>('idle');
    // Gates the one-time tab-strip attention glow (see `.tab-attention-glow--armed`
    // in globals.css). We withhold it until the Activity sparkline's D3 entrance
    // finishes so the two animations don't compete on load; when there's no
    // sparkline to wait for it arms immediately. Re-armed per player below.
    const [glowArmed, setGlowArmed] = useState(false);

    // Reset on player change: keeping a stale `activityAvailable` would let the
    // previous player's empty verdict bounce the new player off the Activity tab
    // before their card refetches; re-arm the glow so it replays for the new page.
    useEffect(() => {
        setActiveTab('activity');
        setActivityAvailable(null);
        setRankedHistoryAvailable(null);
        setGlowArmed(false);
    }, [playerId]);

    // Arm the tab-strip glow. If there's no sparkline to wait for — the user isn't
    // on the Activity tab, or Activity has no data (unavailable) — proceed on load.
    // Otherwise wait for the card's `onSparklineAnimationEnd`, with a safety
    // fallback anchored to data-landed (`activityAvailable === true`) so a missed
    // event can never leave the glow permanently suppressed.
    useEffect(() => {
        if (glowArmed) return;
        if (activeTab !== 'activity' || activityAvailable === false) {
            setGlowArmed(true);
            return;
        }
        if (activityAvailable === true) {
            const fallback = setTimeout(() => setGlowArmed(true), 6000);
            return () => clearTimeout(fallback);
        }
        // activityAvailable === null → still resolving; keep waiting.
    }, [glowArmed, activeTab, activityAvailable]);

    const handleActivityAvailability = useCallback((
        available: boolean,
        // Mode list still arrives from the card (and the re-probe effect) but no
        // longer steers the fallback — kept for signature stability.
        _availableModes: ReadonlyArray<'random' | 'ranked'> = [],
    ) => {
        setActivityAvailable(available);
        if (!available) {
            // Nothing random to show — dark out Activity and land on Ships (the
            // tab to the right). Always Ships, even for a ranked-only player:
            // the v3.2.0–3.2.7 ranked fallback surprised more than it helped.
            setActiveTab((current) => (current === 'activity' ? 'ships' : current));
        }
    }, []);

    // Re-light a dark Activity tab when a visit-driven WG fetch backfills battle
    // history. Once the card reports empty, the parent switches focus to Ships
    // and the card UNMOUNTS (it only renders while Activity is active), so it can
    // never re-report on its own. Instead, while Activity is dark, re-probe the
    // battle-history endpoint on each live-refresh (`refreshNonce` bump from
    // usePlayerLiveRefresh) and, if data has now landed, light the tab back up.
    // Crucially we route through handleActivityAvailability(true), which sets
    // availability WITHOUT touching activeTab — the user stays on whatever tab
    // they're reading; the Activity button just un-darkens so they can click in.
    // Bounded, not a poll: gated to `activityAvailable === false` (rare — only
    // players who loaded with no battle history) and fires at most once per
    // refresh cycle, deduping onto the card's cache via the shared cacheKey.
    useEffect(() => {
        if (activityAvailable !== false) return;
        if (isLoading) return;
        // refreshNonce 0 is the initial mount, where the card itself is still
        // doing its own first availability report — nothing to re-probe yet.
        if (refreshNonce === 0) return;
        let cancelled = false;
        fetchSharedJson<BattleHistoryPayload>(
            battleHistoryFetchUrl(playerName, realm),
            {
                label: `Activity re-probe ${playerName}`,
                ttlMs: BATTLE_HISTORY_FETCH_TTL_MS,
                cacheKey: battleHistoryCacheKey(playerName, realm, 'month', 'random', 0, refreshNonce),
                signal: requestSignal,
            },
        )
            .then(({ data }) => {
                if (cancelled) return;
                if (battleHistoryIndicatesActivity(data, 'random')) {
                    // Light up only — never switches focus to Activity.
                    handleActivityAvailability(true, data.available_modes ?? ['random']);
                }
            })
            .catch(() => { /* leave the tab dark on error; next cycle retries */ });
        return () => { cancelled = true; };
    }, [activityAvailable, refreshNonce, isLoading, playerName, realm, requestSignal, handleActivityAvailability]);

    useEffect(() => {
        setProfileChartPayload(null);
        setProfileChartState('idle');
    }, [playerId, refreshNonce]);

    useEffect(() => {
        setShowRankedHeatmap(hasKnownRankedGames);
    }, [hasKnownRankedGames, playerId]);

    useEffect(() => {
        dispatchPlayerRouteSectionRendered(panelSectionIdByTab[activeTab], playerId, 'immediate');
    }, [activeTab, playerId]);

    useEffect(() => {
        if (isLoading) {
            return;
        }

        let timeoutId: number | null = null;
        let idleCallbackId: number | null = null;

        const warmTabData = () => {
            // Background prefetch of NON-visible tabs (Profile/Ranked/Career). Low
            // priority so it never delays the visible content (detail, clan rail,
            // the default Activity tab's battle history).
            const requests: Array<Promise<unknown>> = [
                fetchSharedJson<unknown>(withRealm(`/api/fetch/player_correlation/tier_type/${playerId}/`, realm), {
                    label: `Tier type correlation ${playerId}`,
                    ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                    priority: 'low',
                    signal: requestSignal,
                    cacheKey: `tier-type:${playerId}:0:0:${refreshNonce}`,
                    responseHeaders: ['X-Tier-Type-Pending'],
                }),
                fetchSharedJson<unknown>(withRealm(`/api/fetch/player_correlation/ranked_wr_battles/${playerId}/`, realm), {
                    label: `Ranked correlation ${playerId}`,
                    ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                    priority: 'low',
                    signal: requestSignal,
                    cacheKey: `ranked-corr:${playerId}:${refreshNonce}`,
                }),
                fetchSharedJson<unknown>(withRealm(`/api/fetch/ranked_data/${playerId}/`, realm), {
                    label: `Ranked data ${playerId}`,
                    ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                    priority: 'low',
                    signal: requestSignal,
                    cacheKey: `ranked-data:${playerId}:0:0:${refreshNonce}`,
                    responseHeaders: ['X-Ranked-Pending'],
                }),
            ];

            if (hasClan) {
                requests.push(
                    fetchSharedJson<unknown>(withRealm(`/api/fetch/player_clan_battle_seasons/${playerId}/`, realm), {
                        label: `Player clan battle seasons ${playerId}`,
                        ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                        priority: 'low',
                        signal: requestSignal,
                        cacheKey: `clan-cb-seasons:${playerId}:${refreshNonce}`,
                    }),
                );
            }

            incrementChartFetches();
            Promise.allSettled(requests.map((request) => request.catch(() => undefined)))
                .then(() => {
                    decrementChartFetches();
                    onWarmupSettled?.();
                });
        };

        if (typeof window !== 'undefined' && typeof window.requestIdleCallback === 'function') {
            idleCallbackId = window.requestIdleCallback(warmTabData, { timeout: TAB_DATA_WARMUP_IDLE_TIMEOUT_MS });
        } else if (typeof window !== 'undefined') {
            timeoutId = window.setTimeout(warmTabData, TAB_DATA_WARMUP_DELAY_MS);
        }

        return () => {
            if (idleCallbackId != null && typeof window !== 'undefined' && typeof window.cancelIdleCallback === 'function') {
                window.cancelIdleCallback(idleCallbackId);
            }

            if (timeoutId != null && typeof window !== 'undefined') {
                window.clearTimeout(timeoutId);
            }
        };
    }, [hasClan, isLoading, onWarmupSettled, playerId, realm, requestSignal, refreshNonce]);

    useEffect(() => {
        if (isLoading || activeTab !== 'profile' || profileChartPayload || profileChartState === 'error' || profileChartState === 'warming') {
            return;
        }

        let cancelled = false;
        let timeoutId: ReturnType<typeof setTimeout> | null = null;
        let pendingAttempts = 0;

        const requestProfileData = async (): Promise<{ data: TierTypePayload; pending: boolean } | null> => {
            for (let attempt = 0; attempt < 2; attempt += 1) {
                try {
                    const payload = await fetchSharedJson<TierTypePayload>(withRealm(`/api/fetch/player_correlation/tier_type/${playerId}/`, realm), {
                        label: `Tier type correlation ${playerId}`,
                        ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                        signal: requestSignal,
                        cacheKey: `tier-type:${playerId}:${pendingAttempts}:${attempt}:${refreshNonce}`,
                        responseHeaders: ['X-Tier-Type-Pending'],
                    });

                    return {
                        data: payload.data,
                        pending: payload.headers['X-Tier-Type-Pending'] === 'true',
                    };
                } catch (err) {
                    // Navigated away / realm switch — stop, don't retry or error.
                    if (isAbortError(err)) {
                        return null;
                    }
                    if (attempt === 0) {
                        await delay(PROFILE_FETCH_RETRY_DELAY_MS);
                        if (cancelled) {
                            return null;
                        }
                        continue;
                    }
                }
            }

            return null;
        };

        const loadProfileCharts = async () => {
            timeoutId = null;
            setProfileChartState('loading');

            const result = await requestProfileData();
            if (cancelled) {
                return;
            }

            if (result === null) {
                setProfileChartState('error');
                return;
            }

            if (result.pending && result.data.player_cells.length === 0) {
                if (pendingAttempts < PROFILE_PENDING_RETRY_LIMIT) {
                    pendingAttempts += 1;
                    timeoutId = setTimeout(() => {
                        void loadProfileCharts();
                    }, PROFILE_PENDING_RETRY_DELAY_MS);
                    return;
                }

                setProfileChartPayload(null);
                setProfileChartState('warming');
                return;
            }

            setProfileChartPayload(result.data);
            setProfileChartState('ready');
        };

        void loadProfileCharts();

        return () => {
            cancelled = true;
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
        };
    }, [activeTab, isLoading, playerId, profileChartPayload, profileChartState, realm, requestSignal, refreshNonce]);

    useEffect(() => {
        if (profileChartState !== 'warming') {
            return;
        }

        const timeoutId = setTimeout(() => {
            setProfileChartState('idle');
        }, PROFILE_WARMING_RETRY_DELAY_MS);

        return () => clearTimeout(timeoutId);
    }, [profileChartState]);

    const derivedTypeRows = profileChartPayload ? deriveTypeRowsFromTierTypePayload(profileChartPayload) : [];
    const derivedTierRows = profileChartPayload ? deriveTierRowsFromTierTypePayload(profileChartPayload) : [];

    const activeConfig = TAB_CONFIG.find((tab) => tab.id === activeTab) ?? TAB_CONFIG[0];

    return (
        <section className="mt-5 rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-4" data-perf-section="insights-tabs-shell">
            {/* The tab strip is the section header now — the standalone "Insights"
                title is gone and Activity sits in its place (left-most). Scrolls
                horizontally on narrow viewports instead of stacking into rows. */}
            <div className="mb-4 border-b border-[var(--border)] pb-3">
                <div
                    role="tablist"
                    aria-label="Player insight tabs"
                    className="-mx-1 flex flex-nowrap gap-2 overflow-x-auto px-1 pb-1 sm:flex-wrap sm:overflow-visible sm:pb-0"
                >
                    {TAB_CONFIG.map((tab) => {
                        const isActive = tab.id === activeTab;
                        // Activity dark-outs (disabled) once we know the player has
                        // no battle activity to show.
                        const isDisabled = tab.id === 'activity' && activityAvailable === false;
                        const base = 'inline-flex min-h-[44px] shrink-0 items-center justify-center whitespace-nowrap rounded-full border px-3 py-1.5 text-sm font-medium transition-colors';
                        const stateClass = isDisabled
                            ? 'cursor-not-allowed border-[var(--border)] bg-[var(--bg-surface)] text-[var(--text-muted)] opacity-40'
                            : isActive
                                ? 'border-[var(--accent-mid)] bg-[var(--accent-faint)] text-[var(--accent-mid)]'
                                : 'border-[var(--border)] bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:border-[var(--accent-light)] hover:text-[var(--accent-mid)]';
                        return (
                            <button
                                key={tab.id}
                                id={`player-insights-tab-${tab.id}`}
                                role="tab"
                                type="button"
                                aria-selected={isActive}
                                aria-controls={`player-insights-panel-${tab.id}`}
                                aria-disabled={isDisabled}
                                disabled={isDisabled}
                                tabIndex={isActive ? 0 : -1}
                                onClick={() => {
                                    if (isDisabled) return;
                                    if (tab.id !== activeTab) {
                                        trackEvent(insightsTabEventByTab[tab.id], { realm });
                                    }
                                    setActiveTab(tab.id);
                                }}
                                className={`${base} ${stateClass}${isDisabled ? '' : ` tab-attention-glow${glowArmed ? ' tab-attention-glow--armed' : ''}`}`}
                            >
                                {tab.label}
                            </button>
                        );
                    })}
                </div>
            </div>

            <div
                id={`player-insights-panel-${activeConfig.id}`}
                role="tabpanel"
                aria-labelledby={`player-insights-tab-${activeConfig.id}`}
                // Activity (the dense battle-history table) takes the full panel
                // width; the chart lanes are capped at 1200px so they don't stretch
                // and thin out on wide viewports.
                className={activeTab === 'activity' ? 'min-w-0' : 'min-w-0 max-w-[1200px]'}
                data-perf-section={panelSectionIdByTab[activeTab]}
                style={{ minHeight: activeConfig.minHeight, contain: 'layout style' }}
            >
                {activeTab === 'activity' ? (
                    isLoading ? (
                        <LoadingPanel label="Loading activity..." minHeight={360} />
                    ) : (
                        <BattleHistoryCard
                            embedded
                            mode="random"
                            playerName={playerName}
                            realm={realm}
                            refreshNonce={refreshNonce}
                            onAvailabilityChange={handleActivityAvailability}
                            onSparklineAnimationEnd={() => setGlowArmed(true)}
                        />
                    )
                ) : null}

                {activeTab === 'population' ? (
                    <div>
                        <SectionHeadingWithTooltip
                            title="Win Rate vs Survival"
                            description="This scatter plot shows how this player's win rate and survival rate compare to the broader tracked player base. Each dot represents a player, positioned by PvP win rate on the x-axis and PvP survival rate on the y-axis. Darker areas indicate denser player clusters, and the outlined marker shows where this player sits in that field."
                            className="mb-2"
                        />
                        <WRDistributionSVG playerWR={pvpRatio} playerSurvivalRate={pvpSurvivalRate} svgHeight={348} theme={theme} />

                        {pvpBattles >= 150 ? (
                            <div className="mt-6">
                                <SectionHeadingWithTooltip
                                    title="Battles Played Distribution"
                                    description="This distribution shows where the player's total PvP battle count falls relative to the broader tracked player population. It is a population-position view, not a quality score."
                                    className="mb-2"
                                />
                                <BattlesDistributionSVG playerBattles={pvpBattles} svgHeight={284} theme={theme} />
                            </div>
                        ) : null}

                        {playerScore != null && playerScore >= 2.0 ? (
                            <div className="mt-6">
                                <SectionHeadingWithTooltip
                                    title="Player Score Distribution"
                                    description="Player score blends win rate, kill ratio, survival, and battle volume into a 0–10 composite. This distribution shows where the player falls relative to the tracked population."
                                    className="mb-2"
                                />
                                <PlayerScoreDistributionSVG playerScore={playerScore} svgHeight={284} theme={theme} />
                            </div>
                        ) : null}
                    </div>
                ) : null}

                {activeTab === 'ships' ? (
                    <div>
                        <SectionHeadingWithTooltip
                            title="Top Ships (Random Battles)"
                            description="This chart highlights the player's most-played random-battle ships, pairing battle volume with wins so you can see which ships dominate their recent visible mix."
                            className="mb-2"
                        />
                        <RandomsSVG playerId={playerId} playerName={playerName} isLoading={isLoading} theme={theme} />
                    </div>
                ) : null}

                {activeTab === 'ranked' ? (
                    <div>
                        {/* Ranked battle history relocated here from the Activity
                            tab's mode pill (2026-07-13) — the same card, fixed to
                            mode="ranked", sparkline included. Hidden once the card
                            reports the player has no ranked history. */}
                        {rankedHistoryAvailable !== false ? (
                            <div className="mb-6">
                                <SectionHeadingWithTooltip
                                    title="Recent Ranked Battles"
                                    description="Battle history scoped to Ranked — daily activity over the last 30 days, per-ship results, and totals for the player's current ranked season. The same view the Activity tab gives for Random battles."
                                    className="mb-2"
                                />
                                <BattleHistoryCard
                                    embedded
                                    mode="ranked"
                                    playerName={playerName}
                                    realm={realm}
                                    refreshNonce={refreshNonce}
                                    onAvailabilityChange={setRankedHistoryAvailable}
                                />
                            </div>
                        ) : null}
                        {!showRankedHeatmap ? (
                            <p className="mb-3 rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-2 text-sm text-[var(--accent-mid)]">
                                No ranked history is visible for this player yet.
                            </p>
                        ) : (
                            <>
                                <SectionHeadingWithTooltip
                                    title="Ranked Games vs Win Rate"
                                    description="Each tile represents a pocket of ranked players grouped by total ranked games and overall ranked win rate. The outlined marker shows where this player lands inside that broader field."
                                    className="mb-3"
                                />
                                <RankedWRBattlesHeatmapSVG
                                    playerId={playerId}
                                    isLoading={isLoading}
                                    onVisibilityChange={setShowRankedHeatmap}
                                    theme={theme}
                                />
                            </>
                        )}

                        <div className="mt-4">
                            <SectionHeadingWithTooltip
                                title="Ranked Seasons"
                                description="This table summarizes the player's historical ranked-season results, including total battles, win rate, and the best league finish reached in each season."
                                className="mb-3"
                            />
                            <RankedSeasons playerId={playerId} isLoading={isLoading} />
                        </div>
                    </div>
                ) : null}

                {activeTab === 'profile' ? (
                    <div>
                        {profileChartState === 'error' ? (
                            <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-400">
                                Unable to load profile charts right now.
                            </p>
                        ) : profileChartState === 'warming' ? (
                            <p className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-2 text-sm text-[var(--accent-mid)]">
                                Profile charts are still warming. Try again in a moment.
                            </p>
                        ) : profileChartPayload ? (
                            <>
                                <SectionHeadingWithTooltip
                                    title="Tier vs Type Profile"
                                    description="This heatmap shows where the tracked player base clusters by ship tier and type. The player markers show where this captain spends most of their battles, so you can compare their ship mix with the broader population trend."
                                    className="mb-2"
                                />
                                <TierTypeHeatmapSVG playerId={playerId} data={profileChartPayload} theme={theme} />

                                <div className="mt-4">
                                    <SectionHeadingWithTooltip
                                        title="Performance by Ship Type"
                                        description="This chart groups the player's battle volume and win rate by ship class, showing where destroyers, cruisers, battleships, carriers, or submarines contribute most."
                                        className="mb-2"
                                    />
                                    <TypeSVG playerId={playerId} data={derivedTypeRows} svgHeight={192} theme={theme} />
                                </div>

                                <div className="mt-5">
                                    <SectionHeadingWithTooltip
                                        title="Performance by Tier"
                                        description="This chart groups the player's battle volume and win rate by ship tier, making it easier to see whether performance clusters in lower, mid, or high tiers."
                                        className="mb-2"
                                    />
                                    <TierSVG playerId={playerId} data={derivedTierRows} svgHeight={300} theme={theme} />
                                </div>
                            </>
                        ) : (
                            <LoadingPanel label="Loading profile charts..." minHeight={560} />
                        )}
                    </div>
                ) : null}

                {activeTab === 'badges' ? (
                    <div>
                        <PlayerEfficiencyBadges efficiencyRows={efficiencyRows} />
                    </div>
                ) : null}

                {activeTab === 'career' ? (
                    <div>
                        {hasClan ? (
                            <div>
                                <SectionHeadingWithTooltip
                                    title="Clan Battle Seasons"
                                    description="Player-specific clan battle participation by season, including battles played, ship tier bracket, and season win rate."
                                    className="mb-2"
                                />
                                <PlayerClanBattleSeasons playerId={playerId} onSummaryChange={onClanBattleSummaryChange} />
                            </div>
                        ) : null}
                    </div>
                ) : null}
            </div>
        </section>
    );
};

export default PlayerDetailInsightsTabs;