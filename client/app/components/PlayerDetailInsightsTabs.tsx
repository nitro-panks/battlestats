import React, { useEffect, useState } from 'react';
import dynamic from 'next/dynamic';
import PlayerEfficiencyBadges from './PlayerEfficiencyBadges';
import SectionHeadingWithTooltip from './SectionHeadingWithTooltip';
import { resilientDynamicImport } from './resilientDynamicImport';
import type { PlayerClanBattleSummary } from './PlayerClanBattleSeasons';
import type { TierTypePayload } from './playerProfileChartData';
import { deriveTierRowsFromTierTypePayload, deriveTypeRowsFromTierTypePayload } from './playerProfileChartData';
import { dispatchPlayerRouteSectionRendered } from './usePlayerRouteDiagnostics';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { useTheme } from '../context/ThemeContext';

type InsightsTabId = 'population' | 'ships' | 'ranked' | 'profile' | 'badges' | 'career';

interface PlayerDetailInsightsTabsProps {
    playerId: number;
    pvpRatio: number;
    pvpSurvivalRate: number;
    pvpBattles: number;
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
    isLoading?: boolean;
}

const LoadingPanel: React.FC<{ label: string; minHeight?: number }> = ({ label, minHeight = 220 }) => (
    <div
        className="flex animate-pulse items-center justify-center rounded-md border border-[var(--border)] bg-[var(--bg-surface)] text-sm text-[var(--accent-light)]"
        style={{ minHeight }}
    >
        {label}
    </div>
);

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

const TAB_CONFIG: Array<{ id: InsightsTabId; label: string; panelLabel: string; minHeight: number; }> = [
    { id: 'profile', label: 'Profile', panelLabel: 'Profile insights', minHeight: 920 },
    { id: 'population', label: 'Population', panelLabel: 'Population insights', minHeight: 720 },
    { id: 'ships', label: 'Ships', panelLabel: 'Ship insights', minHeight: 560 },
    { id: 'ranked', label: 'Ranked', panelLabel: 'Ranked insights', minHeight: 620 },
    { id: 'badges', label: 'Badges', panelLabel: 'Badge insights', minHeight: 360 },
    { id: 'career', label: 'Clan Battles', panelLabel: 'Clan battles insights', minHeight: 280 },
];

const panelSectionIdByTab: Record<InsightsTabId, string> = {
    population: 'insights-population',
    ships: 'insights-ships',
    ranked: 'insights-ranked',
    profile: 'insights-profile',
    badges: 'insights-badges',
    career: 'insights-career',
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
    pvpRatio,
    pvpSurvivalRate,
    pvpBattles,
    hasKnownRankedGames,
    hasClan,
    efficiencyRows = null,
    onClanBattleSummaryChange,
    isLoading = false,
}) => {
    const { theme } = useTheme();
    const [activeTab, setActiveTab] = useState<InsightsTabId>('profile');
    const [showRankedHeatmap, setShowRankedHeatmap] = useState(hasKnownRankedGames);
    const [profileChartPayload, setProfileChartPayload] = useState<TierTypePayload | null>(null);
    const [profileChartState, setProfileChartState] = useState<'idle' | 'loading' | 'ready' | 'warming' | 'error'>('idle');

    useEffect(() => {
        setActiveTab('profile');
    }, [playerId]);

    useEffect(() => {
        setProfileChartPayload(null);
        setProfileChartState('idle');
    }, [playerId]);

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
            const requests: Array<Promise<unknown>> = [
                fetchSharedJson<unknown>(`/api/fetch/player_correlation/tier_type/${playerId}/`, {
                    label: `Tier type correlation ${playerId}`,
                    ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                    cacheKey: `tier-type:${playerId}:0:0`,
                    responseHeaders: ['X-Tier-Type-Pending'],
                }),
                fetchSharedJson<unknown>(`/api/fetch/player_correlation/ranked_wr_battles/${playerId}/`, {
                    label: `Ranked correlation ${playerId}`,
                    ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                }),
                fetchSharedJson<unknown>(`/api/fetch/ranked_data/${playerId}/`, {
                    label: `Ranked data ${playerId}`,
                    ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                    cacheKey: `ranked-data:${playerId}:0:0`,
                    responseHeaders: ['X-Ranked-Pending'],
                }),
            ];

            if (hasClan) {
                requests.push(
                    fetchSharedJson<unknown>(`/api/fetch/player_clan_battle_seasons/${playerId}/`, {
                        label: `Player clan battle seasons ${playerId}`,
                        ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                    }),
                );
            }

            void Promise.allSettled(requests.map((request) => request.catch(() => undefined)));
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
    }, [hasClan, isLoading, playerId]);

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
                    const payload = await fetchSharedJson<TierTypePayload>(`/api/fetch/player_correlation/tier_type/${playerId}/`, {
                        label: `Tier type correlation ${playerId}`,
                        ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
                        cacheKey: `tier-type:${playerId}:${pendingAttempts}:${attempt}`,
                        responseHeaders: ['X-Tier-Type-Pending'],
                    });

                    return {
                        data: payload.data,
                        pending: payload.headers['X-Tier-Type-Pending'] === 'true',
                    };
                } catch {
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
    }, [activeTab, isLoading, playerId, profileChartPayload, profileChartState]);

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
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border)] pb-3">
                <div>
                    <h2 className="text-lg font-semibold text-[var(--accent-dark)]">Insights</h2>
                </div>
                <div role="tablist" aria-label="Player insight tabs" className="flex flex-wrap gap-2">
                    {TAB_CONFIG.map((tab) => {
                        const isActive = tab.id === activeTab;
                        return (
                            <button
                                key={tab.id}
                                id={`player-insights-tab-${tab.id}`}
                                role="tab"
                                type="button"
                                aria-selected={isActive}
                                aria-controls={`player-insights-panel-${tab.id}`}
                                tabIndex={isActive ? 0 : -1}
                                onClick={() => setActiveTab(tab.id)}
                                className={isActive
                                    ? 'rounded-full border border-[var(--accent-mid)] bg-[var(--accent-faint)] px-3 py-1.5 text-sm font-medium text-[var(--accent-mid)]'
                                    : 'rounded-full border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-1.5 text-sm font-medium text-[var(--text-secondary)] transition-colors hover:border-[var(--accent-light)] hover:text-[var(--accent-mid)]'}
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
                className="min-w-0"
                data-perf-section={panelSectionIdByTab[activeTab]}
                style={{ minHeight: activeConfig.minHeight, contain: 'layout style' }}
            >
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
                    </div>
                ) : null}

                {activeTab === 'ships' ? (
                    <div>
                        <SectionHeadingWithTooltip
                            title="Top Ships (Random Battles)"
                            description="This chart highlights the player's most-played random-battle ships, pairing battle volume with wins so you can see which ships dominate their recent visible mix."
                            className="mb-2"
                        />
                        <RandomsSVG playerId={playerId} isLoading={isLoading} theme={theme} />
                    </div>
                ) : null}

                {activeTab === 'ranked' ? (
                    <div>
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