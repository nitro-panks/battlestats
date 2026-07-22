import React, { useCallback, useEffect, useState } from 'react';
import dynamic from 'next/dynamic';
import BattleHistoryCard, {
    BATTLE_HISTORY_FETCH_TTL_MS,
    battleHistoryCacheKey,
    battleHistoryFetchUrl,
    battleHistoryIndicatesActivity,
    type BattleHistoryPayload,
} from './BattleHistoryCard';
import PlayerEfficiencyBadges, { hasEfficiencyBadges } from './PlayerEfficiencyBadges';
import LoadingPanel from './LoadingPanel';
import SectionHeadingWithTooltip from './SectionHeadingWithTooltip';
import RankedLeagueLegend from './RankedLeagueLegend';
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

type InsightsTabId = 'activity' | 'profile' | 'ships' | 'ranked' | 'career' | 'badges';

interface PlayerDetailInsightsTabsProps {
    playerId: number;
    // Battle-history (Activity tab) is keyed by player name + realm, not id.
    playerName: string;
    pvpRatio: number;
    pvpSurvivalRate: number;
    pvpBattles: number;
    hasKnownRankedGames: boolean;
    hasClan: boolean;
    hasClanBattleData: boolean;
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

const RankedSeasonScatterSVG = dynamic(() => resilientDynamicImport(() => import('./RankedSeasonScatterSVG'), 'PlayerDetailInsightsTabs-RankedSeasonScatterSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading season scatter..." minHeight={240} />,
});

const ClanBattleWRBattlesHeatmapSVG = dynamic(() => resilientDynamicImport(() => import('./ClanBattleWRBattlesHeatmapSVG'), 'PlayerDetailInsightsTabs-ClanBattleWRBattlesHeatmapSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading clan battle heatmap..." minHeight={280} />,
});

const ClanBattleSeasonScatterSVG = dynamic(() => resilientDynamicImport(() => import('./ClanBattleSeasonScatterSVG'), 'PlayerDetailInsightsTabs-ClanBattleSeasonScatterSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading season scatter..." minHeight={240} />,
});

const ClanBattleSeasonTimelineSVG = dynamic(() => resilientDynamicImport(() => import('./ClanBattleSeasonTimelineSVG'), 'PlayerDetailInsightsTabs-ClanBattleSeasonTimelineSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading season timeline..." minHeight={128} />,
});

const RankedSeasonTimelineSVG = dynamic(() => resilientDynamicImport(() => import('./RankedSeasonTimelineSVG'), 'PlayerDetailInsightsTabs-RankedSeasonTimelineSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading season timeline..." minHeight={128} />,
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
    loading: () => <LoadingPanel label="Loading tier vs type heatmap..." minHeight={286} />,
});

const WRDistributionSVG = dynamic(() => resilientDynamicImport(() => import('./WRDistributionSVG'), 'PlayerDetailInsightsTabs-WRDistributionSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading win rate distribution..." minHeight={348} />,
});

const BattlesDistributionSVG = dynamic(() => resilientDynamicImport(() => import('./BattlesDistributionSVG'), 'PlayerDetailInsightsTabs-BattlesDistributionSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading battles distribution..." minHeight={284} />,
});

const TAB_CONFIG: Array<{ id: InsightsTabId; label: string; panelLabel: string; minHeight: number; }> = [
    // Order reflects measured Umami tab-click demand (90d, 2026-07-08): Activity
    // stays first as the default landing tab; the remaining tabs are ranked by
    // click volume — Ships > Profile > Efficiency > Ranked > Clan Battles. The
    // former Population tab was folded into the bottom of Profile (2026-07-20).
    // minHeight is only a loading-stability floor (roughly the tab's
    // LoadingPanel stack) — panels size to content since 2026-07-15, so a
    // large floor just recreates the dead space the content-sizing removed.
    { id: 'activity', label: 'Activity', panelLabel: 'Recent battle activity', minHeight: 420 },
    { id: 'ships', label: 'Ships', panelLabel: 'Ship insights', minHeight: 560 },
    { id: 'profile', label: 'Profile', panelLabel: 'Profile insights', minHeight: 360 },
    { id: 'badges', label: 'Efficiency', panelLabel: 'Efficiency insights', minHeight: 360 },
    { id: 'ranked', label: 'Ranked', panelLabel: 'Ranked insights', minHeight: 280 },
    { id: 'career', label: 'Clan Battles', panelLabel: 'Clan battles insights', minHeight: 280 },
];

// Height CAP (px) for the battle-table insight views — the Activity tab and
// Ranked's activity sub-view take it as maxHeight: their table flex-shrinks
// and scrolls inside the clamp when dense, and the panel collapses toward the
// per-tab minHeight when a player has little data (panels stopped being one
// shared locked height on 2026-07-15). Derived from the Ships tab's natural
// height with the 825px chart scroll viewport (RANDOMS_CHART_MAX_VIEWPORT_PX)
// at the desktop insights column: ≈ 1057px.
const LOCKED_PANEL_HEIGHT_PX = 1057;

// Per-row step (px) of the Profile "Performance by Ship Type" horizontal bar
// chart. Fixed (formerly derived from the stacked tier chart so the two
// charts' bar thicknesses matched — moot now that the tier chart is vertical);
// preserves the historical ~28px step so type bars keep their thickness.
const PROFILE_TYPE_CHART_ROW_STEP = 28;

// Shared SVG height (px) for the Profile tab's population row — the WR-vs-Survival
// heatmap and the two distribution histograms sit side by side on lg+, so one
// common height keeps the row's bottom edge flush. Each chart measures its own
// ~1/3 column width; below 480px the heatmap drops into its compact layout.
const POPULATION_CHART_HEIGHT = 210;

// The Ranked tab's activity/history sub-view toggle chip. Sized to sit inline
// beside the mode caption ("Ranked"): same text size + padding, bordered (vs the
// caption's fill) so it reads as an action rather than a label.
const RANKED_TOGGLE_CHIP_CLASS = 'inline-flex shrink-0 items-center rounded border border-[var(--border)] px-2 py-0.5 text-xs font-semibold text-[var(--accent-mid)] transition-colors hover:border-[var(--accent-light)] hover:text-[var(--accent-dark)]';
// Mirrors BattleHistoryCard's mode-caption chip so the history sub-view carries
// the same "Ranked" label the activity card shows in its header.
const RANKED_MODE_CAPTION_CLASS = 'rounded bg-[var(--accent-faint)] px-2 py-0.5 text-xs font-semibold text-[var(--accent-dark)]';

const panelSectionIdByTab: Record<InsightsTabId, string> = {
    activity: 'insights-activity',
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
    hasKnownRankedGames,
    hasClan,
    hasClanBattleData,
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
    // null = unknown; set by the Ranked tab's battle-history card. false means
    // the player has no recent ranked battle activity.
    const [rankedHistoryAvailable, setRankedHistoryAvailable] = useState<boolean | null>(null);
    // The Ranked tab has two sub-views: 'activity' (a ranked copy of the Activity
    // page — the battle-history card) and 'history' (the ranked heatmap + seasons,
    // i.e. the view shown when there's no activity). Defaults to 'activity'; the
    // auto-flip effect below drops it to 'history' once the card reports no ranked
    // activity. No manual-pin flag is needed: the effect only fires on
    // `=== false`, and the "Activity" toggle is hidden in that state, so a user can
    // never be stranded on an empty activity view.
    const [rankedView, setRankedView] = useState<'activity' | 'history'>('activity');
    const [showRankedHeatmap, setShowRankedHeatmap] = useState(hasKnownRankedGames);
    // Gates ONLY the clan-battle population heatmap (hides itself when the
    // player has no stored CB summary point); the scatter/timeline/table on the
    // CB tab render independently. Defaults true so it shows while loading.
    const [showClanBattleHeatmap, setShowClanBattleHeatmap] = useState(true);
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
        setRankedView('activity');
        setGlowArmed(false);
    }, [playerId]);

    // Ranked tab defaults to its activity sub-view; fall back to the history view
    // (heatmap + seasons) once the ranked battle-history card reports the player
    // has no recent ranked activity. `rankedView` is intentionally NOT a dep — the
    // effect only reacts to the availability verdict, so it can't loop or fight a
    // user who manually toggled back to history.
    useEffect(() => {
        if (rankedHistoryAvailable === false) {
            setRankedView('history');
        }
    }, [rankedHistoryAvailable]);

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
        // Re-show the CB heatmap on player switch; the component hides it again
        // if the new player has no stored CB summary point.
        setShowClanBattleHeatmap(true);
    }, [playerId]);

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
    // TierSVG renders vertical columns, so its x-axis order is the row order:
    // the derive helper emits 11→1 (the old top-to-bottom row order); reverse
    // so tiers ascend left→right.
    const derivedTierRows = profileChartPayload ? deriveTierRowsFromTierTypePayload(profileChartPayload).reverse() : [];
    // TypeSVG (Performance by Ship Type) has few, data-dependent rows; size its
    // height from a fixed per-row step (shipBarPlot y padding 0.18, non-compact
    // top=8/bottom=48) so bars keep a constant thickness instead of stretching
    // to fill a fixed panel height. The tier column chart beside it reuses the
    // same height so the side-by-side pair shares one bottom edge.
    const typeChartHeight = Math.round(PROFILE_TYPE_CHART_ROW_STEP * (Math.max(derivedTypeRows.length, 1) + 0.18)) + 8 + 48;

    const activeConfig = TAB_CONFIG.find((tab) => tab.id === activeTab) ?? TAB_CONFIG[0];
    // Computed once (not per-tab inside the strip map): whether the player has any
    // plottable efficiency badge, gating the Efficiency tab's enabled state.
    const hasBadges = hasEfficiencyBadges(efficiencyRows);

    // Profile-tab population row (WR-vs-Survival heatmap + the conditional
    // Battles-Played histogram, side by side on lg+). The histogram is conditional,
    // so the grid drops to a single full-width column when the player has too few
    // battles to plot — never a dead column. Both cards share one height so the
    // row's bottom edge stays flush; each SVG measures its own column width.
    const showBattlesDistribution = pvpBattles >= 150;
    // Match the Type/Tier profile row above, which goes side-by-side at md
    // (768px). Keying this on lg (1024px) instead made the population charts
    // stack full-width across the 768–1024px range while the row above stayed
    // 2-up — a lone reflow at ~950px with nothing else moving.
    const populationGridColsClass = showBattlesDistribution ? 'md:grid-cols-2' : '';

    return (
        <section className="mt-5 rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-4" data-perf-section="insights-tabs-shell">
            {/* The tab strip is the section header now — the standalone "Insights"
                title is gone and Activity sits in its place (left-most). Scrolls
                horizontally on narrow viewports instead of stacking into rows. */}
            {/* Compact rectangular tab strip flush to the section's top/left/right
                edges (negative margins cancel the section's p-4). A 1px rule under
                the strip runs edge-to-edge across the bounding box; the tablist's
                -mb-px overlaps the buttons' 2px bottom border onto it, so the
                active tab's accent indicator cuts through the rule. On desktop the
                tabs stretch to fill the width, on narrow viewports they scroll
                horizontally. */}
            <div className="-mx-4 -mt-4 mb-4 rounded-t-lg border-b border-[var(--border)]">
                <div
                    role="tablist"
                    aria-label="Player insight tabs"
                    className="-mb-px flex flex-nowrap gap-0 overflow-x-auto sm:overflow-visible"
                >
                {TAB_CONFIG.map((tab) => {
                    const isActive = tab.id === activeTab;
                    // Activity dark-outs (disabled) once we know the player has
                    // no battle activity to show; Ranked dark-outs when the player
                    // has no known ranked games (ranked_json resolved with zero
                    // battles — `hasKnownRankedGames` defaults true while pending,
                    // so the tab only disables once we're sure it's empty); Clan
                    // Battles dark-outs when the player has no clan-battle data
                    // (`hasClanBattleData` is the same server-resolved flag that
                    // gates the header CB shield, so shield-shown ⟺ tab-enabled);
                    // Efficiency dark-outs when the player has no plottable badge
                    // (shares the panel's own normalizeBadgeDots predicate).
                    const isDisabled = (tab.id === 'activity' && activityAvailable === false)
                        || (tab.id === 'ranked' && !hasKnownRankedGames)
                        || (tab.id === 'career' && !hasClanBattleData)
                        || (tab.id === 'badges' && !hasBadges);
                    const base = 'inline-flex shrink-0 items-center justify-center whitespace-nowrap border-b-2 px-4 py-2.5 text-sm font-medium transition-colors sm:flex-1';
                    const stateClass = isDisabled
                        ? 'cursor-not-allowed border-transparent text-[var(--text-muted)] opacity-40'
                        : isActive
                            ? 'border-[var(--accent-mid)] text-[var(--accent-mid)]'
                            : 'border-transparent text-[var(--text-secondary)] hover:border-[var(--accent-light)] hover:text-[var(--accent-mid)]';
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
                // The dense battle-history table takes the full panel width — that's
                // the Activity tab and the Ranked tab's activity sub-view (a ranked
                // copy of it).
                className={activeTab === 'activity' || (activeTab === 'ranked' && rankedView === 'activity') ? 'flex min-h-0 min-w-0 flex-col' : 'min-w-0'}
                data-perf-section={panelSectionIdByTab[activeTab]}
                style={{
                    // Panels size to their content instead of a shared locked
                    // height, so sparse players don't get a tall empty box. The
                    // old lock survives as a CAP on the two battle-table views
                    // (Activity and Ranked's activity sub-view): their table
                    // flex-shrinks and scrolls inside the clamp when dense,
                    // while short content lets the panel collapse toward the
                    // per-tab minHeight floor (kept so tab switches don't flash
                    // a zero-height shell while data loads). Every other tab
                    // grows naturally — Ships' filters can wrap, and a dense
                    // badge plot or Clan Battles seasons table can run taller
                    // than the cap and must not clip.
                    ...(activeTab === 'activity' || (activeTab === 'ranked' && rankedView === 'activity')
                        ? { maxHeight: LOCKED_PANEL_HEIGHT_PX }
                        : {}),
                    minHeight: activeConfig.minHeight,
                    contain: 'layout style',
                }}
            >
                {activeTab === 'activity' ? (
                    isLoading ? (
                        <LoadingPanel label="Loading activity..." minHeight={360} />
                    ) : (
                        <BattleHistoryCard
                            embedded
                            fillHeight
                            mode="random"
                            playerName={playerName}
                            realm={realm}
                            refreshNonce={refreshNonce}
                            onAvailabilityChange={handleActivityAvailability}
                            onSparklineAnimationEnd={() => setGlowArmed(true)}
                        />
                    )
                ) : null}

                {activeTab === 'ships' ? (
                    <RandomsSVG playerId={playerId} playerName={playerName} isLoading={isLoading} theme={theme} />
                ) : null}

                {activeTab === 'ranked' ? (
                    rankedView === 'activity' ? (
                        // Activity sub-view: a ranked copy of the Activity page (the
                        // battle-history card, mode="ranked", filling the locked
                        // panel). The card is the availability oracle — it reports
                        // via onAvailabilityChange, and the effect above drops us to
                        // the history view when the player has no ranked activity.
                        // The "History" toggle rides in the card header, inline to
                        // the left of its "Ranked" caption (no separate button row).
                        <BattleHistoryCard
                            embedded
                            fillHeight
                            mode="ranked"
                            playerName={playerName}
                            realm={realm}
                            refreshNonce={refreshNonce}
                            onAvailabilityChange={setRankedHistoryAvailable}
                            captionLeading={(
                                <button
                                    type="button"
                                    onClick={() => {
                                        setRankedView('history');
                                        trackEvent('player-insights-ranked-view', { realm, view: 'history' });
                                    }}
                                    className={RANKED_TOGGLE_CHIP_CLASS}
                                >
                                    History
                                </button>
                            )}
                        />
                    ) : (
                        // History sub-view: the ranked heatmap + seasons — the view
                        // shown when there's no activity. The "Activity" toggle +
                        // matching "Ranked" caption ride on the same line as the
                        // "Ranked Games vs Win Rate" heading (right side). The toggle
                        // only appears when the player actually has ranked activity
                        // (rankedHistoryAvailable !== false), so an empty-activity
                        // player is never offered a dead round-trip.
                        <div>
                            {/* pt-2.5/pl-[15px] is the shared tab-top header spot across
                                the insight tabs; the view toggle + mode caption ride in
                                the same flex row. items-start so the taller chips can't
                                push the label below the shared y. */}
                            <div className={`mb-3 flex items-start gap-3 pt-2.5 pl-[15px] ${showRankedHeatmap ? 'justify-between' : 'justify-end'}`}>
                                {showRankedHeatmap ? (
                                    <SectionHeadingWithTooltip
                                        title="Ranked Games vs Win Rate"
                                        description="Each tile represents a pocket of ranked players grouped by total ranked games and overall ranked win rate. The outlined marker shows where this player lands inside that broader field."
                                    />
                                ) : null}
                                <div className="flex shrink-0 items-center gap-1.5">
                                    {rankedHistoryAvailable !== false ? (
                                        <button
                                            type="button"
                                            onClick={() => {
                                                setRankedView('activity');
                                                trackEvent('player-insights-ranked-view', { realm, view: 'activity' });
                                            }}
                                            className={RANKED_TOGGLE_CHIP_CLASS}
                                        >
                                            Activity
                                        </button>
                                    ) : null}
                                    <span className={RANKED_MODE_CAPTION_CLASS} title="Ranked battles only (sums across active seasons)">
                                        Ranked
                                    </span>
                                </div>
                            </div>
                            {!showRankedHeatmap ? (
                                <p className="mb-3 rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-2 text-sm text-[var(--accent-mid)]">
                                    No ranked history is visible for this player yet.
                                </p>
                            ) : (
                                <RankedWRBattlesHeatmapSVG
                                    playerId={playerId}
                                    isLoading={isLoading}
                                    onVisibilityChange={setShowRankedHeatmap}
                                    theme={theme}
                                />
                            )}

                            {/* Per-season scatter: ranked battles (x) vs win rate
                                (y), one dot per season colored by WR — like the
                                clan chart. Rendered full-width (no pl-[15px]) with
                                the same margin.left as the heatmap so its y-axis
                                lines up with the heatmap's left edge. Gated on the
                                same ranked-history signal as the heatmap. */}
                            {showRankedHeatmap ? (
                                <div className="mt-4">
                                    <RankedSeasonScatterSVG playerId={playerId} isLoading={isLoading} theme={theme} />
                                </div>
                            ) : null}

                            {/* Season activity timeline (year markers) below the
                                scatter — shows where the player's ranked seasons
                                cluster in time. */}
                            {showRankedHeatmap ? (
                                <div className="mt-2">
                                    <SectionHeadingWithTooltip
                                        title="Ranked Season Timeline"
                                        description="Where the player's ranked seasons fall in time. Each marker is one season, positioned by year; its shape shows the highest league reached that season, its size the battles played (relative to the player's own range), and its color the season win rate."
                                        className="mb-2 pl-[15px]"
                                    />
                                    <RankedSeasonTimelineSVG playerId={playerId} isLoading={isLoading} theme={theme} />
                                </div>
                            ) : null}

                            {/* One-line shape key for the scatter's league glyphs,
                                sitting just above the seasons table. */}
                            {showRankedHeatmap ? (
                                <div className="mt-3 pl-[15px]">
                                    <RankedLeagueLegend theme={theme} />
                                </div>
                            ) : null}

                            {/* 30px gap between the season-glyph legend and the
                                Ranked Seasons header (requested spacing). */}
                            <div className="mt-[30px]">
                                {/* Label + table share the tab's 15px left inset (same
                                    x as the Ranked Games vs Win Rate header above); the
                                    table also pulls in 20px on the right. */}
                                <SectionHeadingWithTooltip
                                    title="Ranked Seasons"
                                    description="This table summarizes the player's historical ranked-season results, including total battles, win rate, and the best league finish reached in each season."
                                    className="mb-3 pl-[15px]"
                                />
                                <div className="pl-[15px] pr-[20px]">
                                    <RankedSeasons playerId={playerId} isLoading={isLoading} />
                                </div>
                            </div>
                        </div>
                    )
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
                                    title="Tier vs Type Profile (Random Battles)"
                                    description="This heatmap shows where the tracked player base clusters by ship tier and type. The player markers show where this captain spends most of their battles, so you can compare their ship mix with the broader population trend."
                                    className="mb-2 pt-2.5 pl-[15px]"
                                />
                                <TierTypeHeatmapSVG playerId={playerId} data={profileChartPayload} theme={theme} />

                                {/* The two performance breakdowns share a row on desktop
                                    (Type's horizontal bars left, Tier's vertical columns
                                    right) and stack on narrow viewports. min-w-0 lets each
                                    chart container measure the halved column instead of
                                    forcing overflow. */}
                                <div className="mt-4 flex flex-col gap-4 md:flex-row">
                                    <div className="min-w-0 md:w-1/2">
                                        <SectionHeadingWithTooltip
                                            title="Performance by Ship Type"
                                            description="This chart groups the player's battle volume and win rate by ship class, showing where destroyers, cruisers, battleships, carriers, or submarines contribute most."
                                            className="mb-2 pl-[15px]"
                                        />
                                        <TypeSVG playerId={playerId} data={derivedTypeRows} svgHeight={typeChartHeight} theme={theme} />
                                    </div>

                                    <div className="min-w-0 md:w-1/2">
                                        <SectionHeadingWithTooltip
                                            title="Performance by Tier"
                                            description="This chart groups the player's battle volume and win rate by ship tier, making it easier to see whether performance clusters in lower, mid, or high tiers."
                                            className="mb-2 pl-[15px]"
                                        />
                                        <TierSVG playerId={playerId} data={derivedTierRows} svgHeight={typeChartHeight} theme={theme} />
                                    </div>
                                </div>
                            </>
                        ) : (
                            <LoadingPanel label="Loading profile charts..." minHeight={560} />
                        )}

                        {/* Population comparison — formerly the standalone Population
                            tab, folded into the bottom of Profile (2026-07-20). It
                            depends only on the header props (win rate, survival,
                            battles), not the tier/type payload above, so it renders
                            regardless of that section's load state. A top margin
                            separates the player's own profile from where they sit
                            against the tracked population. The heatmap and the
                            conditional histogram share one row on md+ (matching the
                            Type/Tier row above); the grid drops to a single full-width
                            column when the histogram is gated out, so a sparse player
                            never leaves a dead column. Each
                            column carries the pl-[15px] inset the profile section
                            labels above use, so the two rows line up. */}
                        <div className="mt-8">
                            <div className={`grid grid-cols-1 gap-6 ${populationGridColsClass}`}>
                                <div className="min-w-0 pl-[15px]">
                                    <SectionHeadingWithTooltip
                                        title="Win Rate vs Survival"
                                        description="This scatter plot shows how this player's win rate and survival rate compare to the broader tracked player base. Each dot represents a player, positioned by PvP win rate on the x-axis and PvP survival rate on the y-axis. Darker areas indicate denser player clusters, and the outlined marker shows where this player sits in that field."
                                        className="mb-2"
                                    />
                                    <WRDistributionSVG playerWR={pvpRatio} playerSurvivalRate={pvpSurvivalRate} svgHeight={POPULATION_CHART_HEIGHT} theme={theme} />
                                </div>

                                {showBattlesDistribution ? (
                                    <div className="min-w-0 pl-[15px]">
                                        <SectionHeadingWithTooltip
                                            title="Battles Played Distribution"
                                            description="This distribution shows where the player's total PvP battle count falls relative to the broader tracked player population. It is a population-position view, not a quality score."
                                            className="mb-2"
                                        />
                                        <BattlesDistributionSVG playerBattles={pvpBattles} svgHeight={POPULATION_CHART_HEIGHT} theme={theme} />
                                    </div>
                                ) : null}
                            </div>
                        </div>
                    </div>
                ) : null}

                {activeTab === 'badges' ? (
                    <div>
                        <PlayerEfficiencyBadges efficiencyRows={efficiencyRows} maxTableHeightPx={LOCKED_PANEL_HEIGHT_PX} />
                    </div>
                ) : null}

                {activeTab === 'career' ? (
                    <div>
                        {hasClan ? (
                            <div>
                                <SectionHeadingWithTooltip
                                    title="Clan Battles vs Win Rate"
                                    description="Where this player sits in the tracked population by total clan battles and overall win rate (the heatmap: each tile is a pocket of players, the outlined marker is this player), followed by their own per-season battles and win rate below."
                                    className="mb-[18px] pt-2.5 pl-[15px]"
                                />
                                {/* Population heatmap at the top — mirrors the ranked
                                    tab. Hides only itself (setShowClanBattleHeatmap)
                                    when the player has no stored CB summary point;
                                    the scatter/timeline/table below stay. */}
                                {showClanBattleHeatmap ? (
                                    <div className="mb-4">
                                        <ClanBattleWRBattlesHeatmapSVG
                                            playerId={playerId}
                                            onVisibilityChange={setShowClanBattleHeatmap}
                                            theme={theme}
                                        />
                                    </div>
                                ) : null}
                                {/* Per-season scatter above the table — clan battles
                                    (x) vs win rate (y), one dot per season colored by
                                    WR (circles; no CB league bracket is in the
                                    payload). Mirrors the ranked-history scatter. */}
                                <div className="mb-2">
                                    <ClanBattleSeasonScatterSVG playerId={playerId} theme={theme} />
                                </div>
                                {/* Season activity timeline (year markers) below the
                                    scatter — shows where the player's clan-battle
                                    seasons cluster in time. */}
                                <div className="mb-4">
                                    <SectionHeadingWithTooltip
                                        title="Clan Season Timeline"
                                        description="Where the player's clan battle seasons fall in time. Each marker is one season, positioned by year, sized by battles played (relative to the player's own range), and colored by season win rate."
                                        className="mb-2 pl-[15px]"
                                    />
                                    <ClanBattleSeasonTimelineSVG playerId={playerId} theme={theme} />
                                </div>
                                {/* Content shares the header's 15px inset, mirrored
                                    on the right so the table stays centered. */}
                                <div className="px-[15px]">
                                    <PlayerClanBattleSeasons playerId={playerId} onSummaryChange={onClanBattleSummaryChange} />
                                </div>
                            </div>
                        ) : null}
                    </div>
                ) : null}
            </div>
        </section>
    );
};

export default PlayerDetailInsightsTabs;