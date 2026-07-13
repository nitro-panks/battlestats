import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import PlayerDetailInsightsTabs from '../PlayerDetailInsightsTabs';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
    getChartFetchesInFlight: jest.fn(() => 0),
    incrementChartFetches: jest.fn(),
    decrementChartFetches: jest.fn(),
}));

const mockTrackEvent = jest.fn();
jest.mock('../../lib/umami', () => ({
    trackEvent: (...args: unknown[]) => mockTrackEvent(...args),
}));

let mockRankedHeatmapVisibility: boolean | undefined;
const mockFetchSharedJson = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

const readyTierTypePayload = {
    metric: 'tier_type' as const,
    label: 'Tier vs Ship Type',
    x_label: 'Ship Type',
    y_label: 'Tier',
    tracked_population: 739,
    x_labels: ['Destroyer', 'Cruiser', 'Battleship', 'Aircraft Carrier', 'Submarine'],
    y_values: [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
    tiles: [
        { x_index: 2, y_index: 1, count: 320000 },
        { x_index: 1, y_index: 1, count: 410000 },
    ],
    trend: [
        { x_index: 2, avg_tier: 9.3, count: 320000 },
        { x_index: 1, avg_tier: 9.1, count: 410000 },
    ],
    player_cells: [
        { ship_type: 'Battleship', ship_tier: 10, pvp_battles: 420, wins: 239, win_ratio: 0.569 },
        { ship_type: 'Cruiser', ship_tier: 10, pvp_battles: 320, wins: 176, win_ratio: 0.551 },
    ],
};

const pendingTierTypePayload = {
    ...readyTierTypePayload,
    player_cells: [],
};

jest.mock('next/dynamic', () => {
    return () => function MockDynamicComponent(props: {
        onVisibilityChange?: (isVisible: boolean) => void;
    }) {
        const React = require('react');
        const { onVisibilityChange } = props;

        React.useEffect(() => {
            if (typeof onVisibilityChange === 'function' && mockRankedHeatmapVisibility !== undefined) {
                onVisibilityChange(mockRankedHeatmapVisibility);
            }
        }, [onVisibilityChange]);

        return <div data-testid="dynamic-component" />;
    };
});

jest.mock('../SectionHeadingWithTooltip', () => {
    return function MockSectionHeadingWithTooltip({ title }: { title: string }) {
        return <div>{title}</div>;
    };
});

describe('PlayerDetailInsightsTabs', () => {
    beforeEach(() => {
        mockRankedHeatmapVisibility = undefined;
        mockTrackEvent.mockReset();
        mockFetchSharedJson.mockReset();
        mockFetchSharedJson.mockImplementation((url) => {
            if (url.includes('/api/fetch/player_correlation/tier_type/')) {
                return new Promise(() => { });
            }

            // Activity tab card fetch — return a payload with battles so the
            // Activity tab reports available and stays the default.
            if (url.includes('/battle-history/')) {
                return Promise.resolve({
                    data: {
                        as_of: '2026-06-06T00:00:00Z',
                        available_modes: ['random'],
                        totals: {
                            battles: 42, wins: 24, losses: 18, win_rate: 57.1,
                            damage: 4200000, avg_damage: 100000, frags: 60, xp: 0,
                            planes_killed: 0, survived_battles: 20, survival_rate: 47.6,
                        },
                        by_ship: [],
                        by_day: [],
                    },
                    headers: {},
                });
            }

            return Promise.resolve({ data: [], headers: {} });
        });
        jest.useFakeTimers();
    });

    afterEach(() => {
        act(() => {
            jest.runOnlyPendingTimers();
        });
        jest.useRealTimers();
    });

    it('defaults to the Activity tab (left-most) and keeps heavy chart lanes inactive', () => {
        render(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="TestCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
            />,
        );

        // Activity replaced the old "Insights" header and is the default tab;
        // Ships is the tab to its right and is NOT active on load.
        expect(screen.getByRole('tab', { name: 'Activity' })).toHaveAttribute('aria-selected', 'true');
        expect(screen.getByRole('tab', { name: 'Ships' })).toHaveAttribute('aria-selected', 'false');
        // The heavy chart lanes stay inactive until selected.
        expect(screen.queryByText('Top Ships (Random Battles)')).not.toBeInTheDocument();
        expect(screen.queryByText('Loading profile charts...')).not.toBeInTheDocument();
        expect(screen.queryByText('Ranked Seasons')).not.toBeInTheDocument();
        expect(screen.queryByText('Efficiency Badges')).not.toBeInTheDocument();
    });

    it('darks out the Activity tab and falls back to Ships when there is no activity', async () => {
        // Battle-history payload with zero battles + only random mode → no activity.
        mockFetchSharedJson.mockImplementation((url) => {
            if (url.includes('/api/fetch/player_correlation/tier_type/')) {
                return new Promise(() => { });
            }
            if (url.includes('/battle-history/')) {
                return Promise.resolve({
                    data: {
                        as_of: '2026-06-06T00:00:00Z',
                        available_modes: ['random'],
                        totals: {
                            battles: 0, wins: 0, losses: 0, win_rate: 0,
                            damage: 0, avg_damage: 0, frags: 0, xp: 0,
                            planes_killed: 0, survived_battles: 0, survival_rate: 0,
                        },
                        by_ship: [],
                        by_day: [],
                    },
                    headers: {},
                });
            }
            return Promise.resolve({ data: [], headers: {} });
        });

        render(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="QuietCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
            />,
        );

        await waitFor(() => {
            expect(screen.getByRole('tab', { name: 'Activity' })).toBeDisabled();
        });
        // Fell back to the Ships tab (to the right of Activity).
        expect(screen.getByRole('tab', { name: 'Ships' })).toHaveAttribute('aria-selected', 'true');
        expect(screen.getByRole('tab', { name: 'Activity' })).toHaveAttribute('aria-selected', 'false');
    });

    it('falls back to the Ranked tab (not Ships) when the player is ranked-only', async () => {
        // Zero random battles but ranked rows exist → Activity darks out and
        // focus lands on Ranked, where the ranked battle history lives now.
        mockFetchSharedJson.mockImplementation((url) => {
            if (url.includes('/api/fetch/player_correlation/tier_type/')) {
                return new Promise(() => { });
            }
            if (url.includes('/battle-history/')) {
                return Promise.resolve({
                    data: {
                        as_of: '2026-06-06T00:00:00Z',
                        available_modes: ['ranked'],
                        totals: {
                            battles: 0, wins: 0, losses: 0, win_rate: 0,
                            damage: 0, avg_damage: 0, frags: 0, xp: 0,
                            planes_killed: 0, survived_battles: 0, survival_rate: 0,
                        },
                        by_ship: [],
                        by_day: [],
                    },
                    headers: {},
                });
            }
            return Promise.resolve({ data: [], headers: {} });
        });

        render(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="RankedOnlyCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
            />,
        );

        await waitFor(() => {
            expect(screen.getByRole('tab', { name: 'Ranked' })).toHaveAttribute('aria-selected', 'true');
        });
        expect(screen.getByRole('tab', { name: 'Activity' })).toBeDisabled();
        expect(screen.getByRole('tab', { name: 'Ships' })).toHaveAttribute('aria-selected', 'false');
    });

    it('renders the Recent Ranked Battles history card on the Ranked tab', async () => {
        render(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="TestCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
            />,
        );

        fireEvent.click(screen.getByRole('tab', { name: 'Ranked' }));
        await waitFor(() => {
            expect(screen.getByText('Recent Ranked Battles')).toBeInTheDocument();
        });
        // The embedded battle-history card mounts inside the section (the
        // default beforeEach mock answers every battle-history URL with battles).
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
    });

    it('hides Recent Ranked Battles when the player has no ranked history', async () => {
        mockFetchSharedJson.mockImplementation((url) => {
            if (url.includes('/api/fetch/player_correlation/tier_type/')) {
                return new Promise(() => { });
            }
            if (url.includes('/battle-history/')) {
                const rankedRequest = url.includes('mode=ranked');
                return Promise.resolve({
                    data: {
                        as_of: '2026-06-06T00:00:00Z',
                        available_modes: ['random'],
                        totals: {
                            battles: rankedRequest ? 0 : 42,
                            wins: rankedRequest ? 0 : 24,
                            losses: rankedRequest ? 0 : 18,
                            win_rate: rankedRequest ? 0 : 57.1,
                            damage: 0, avg_damage: 0, frags: 0, xp: 0,
                            planes_killed: 0, survived_battles: 0,
                            survival_rate: 0,
                        },
                        by_ship: [],
                        by_day: [],
                    },
                    headers: {},
                });
            }
            return Promise.resolve({ data: [], headers: {} });
        });

        render(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="RandomsCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
            />,
        );

        fireEvent.click(screen.getByRole('tab', { name: 'Ranked' }));
        // The ranked card mounts, reports no ranked availability, and the
        // section unmounts; the rest of the Ranked tab stays.
        await waitFor(() => {
            expect(screen.queryByText('Recent Ranked Battles')).not.toBeInTheDocument();
        });
        expect(screen.getByText('Ranked Seasons')).toBeInTheDocument();
    });

    it('re-lights a dark Activity tab when a refresh backfills battle history, without stealing focus', async () => {
        // First load: zero battles → Activity darks out, focus falls to Ships.
        // A later visit-driven WG fetch (refreshNonce bump) backfills battles.
        let hasBattlesNow = false;
        const battleHistoryPayload = (battles: number) => ({
            data: {
                as_of: '2026-06-06T00:00:00Z',
                available_modes: ['random'],
                totals: {
                    battles, wins: battles, losses: 0, win_rate: battles ? 100 : 0,
                    damage: 0, avg_damage: 0, frags: 0, xp: 0,
                    planes_killed: 0, survived_battles: 0, survival_rate: 0,
                },
                by_ship: [],
                by_day: [],
            },
            headers: {},
        });
        mockFetchSharedJson.mockImplementation((url) => {
            if (url.includes('/api/fetch/player_correlation/tier_type/')) {
                return new Promise(() => { });
            }
            if (url.includes('/battle-history/')) {
                return Promise.resolve(battleHistoryPayload(hasBattlesNow ? 42 : 0));
            }
            return Promise.resolve({ data: [], headers: {} });
        });

        const props = {
            playerId: 101,
            playerName: 'LateBloomer',
            pvpRatio: 55,
            pvpSurvivalRate: 40,
            pvpBattles: 800,
            playerScore: null,
            hasKnownRankedGames: true,
            hasClan: true,
            efficiencyRows: [],
        };

        const { rerender } = render(
            <PlayerDetailInsightsTabs {...props} refreshNonce={0} />,
        );

        // Dark-out + focus fallback to Ships.
        await waitFor(() => {
            expect(screen.getByRole('tab', { name: 'Activity' })).toBeDisabled();
        });
        expect(screen.getByRole('tab', { name: 'Ships' })).toHaveAttribute('aria-selected', 'true');

        // The refresh lands with battle history present.
        hasBattlesNow = true;
        await act(async () => {
            rerender(<PlayerDetailInsightsTabs {...props} refreshNonce={1} />);
            await Promise.resolve();
        });

        // Activity lights back up...
        await waitFor(() => {
            expect(screen.getByRole('tab', { name: 'Activity' })).not.toBeDisabled();
        });
        // ...but focus stays where the user left it (Ships) — never yanked to Activity.
        expect(screen.getByRole('tab', { name: 'Ships' })).toHaveAttribute('aria-selected', 'true');
        expect(screen.getByRole('tab', { name: 'Activity' })).toHaveAttribute('aria-selected', 'false');
    });

    it('fires name-baked player-insights events per tab (readable label, not the internal id)', () => {
        render(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="TestCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                playerScore={1.8}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
            />,
        );

        // Ships is the default tab; switching to others fires distinct named events.
        fireEvent.click(screen.getByRole('tab', { name: 'Ranked' }));
        expect(mockTrackEvent).toHaveBeenCalledWith('player-insights-ranked', expect.objectContaining({ realm: expect.any(String) }));

        // 'Clan Battles' (id: career) and 'Efficiency' (id: badges) use the readable label slug.
        fireEvent.click(screen.getByRole('tab', { name: 'Clan Battles' }));
        expect(mockTrackEvent).toHaveBeenCalledWith('player-insights-clan-battles', expect.objectContaining({ realm: expect.any(String) }));

        fireEvent.click(screen.getByRole('tab', { name: 'Efficiency' }));
        expect(mockTrackEvent).toHaveBeenCalledWith('player-insights-efficiency', expect.objectContaining({ realm: expect.any(String) }));

        // Re-clicking the already-active tab does not re-fire.
        mockTrackEvent.mockReset();
        fireEvent.click(screen.getByRole('tab', { name: 'Efficiency' }));
        expect(mockTrackEvent).not.toHaveBeenCalled();
    });

    it('switches across the insights tabs one at a time', () => {
        render(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="TestCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
            />,
        );

        fireEvent.click(screen.getByRole('tab', { name: 'Ships' }));
        expect(screen.getByText('Top Ships (Random Battles)')).toBeInTheDocument();
        expect(screen.queryByText('Win Rate vs Survival')).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('tab', { name: 'Ranked' }));
        expect(screen.getByText('Ranked Games vs Win Rate')).toBeInTheDocument();
        expect(screen.getByText('Ranked Seasons')).toBeInTheDocument();
        expect(screen.queryByText('Top Ships (Random Battles)')).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('tab', { name: 'Profile' }));
        expect(screen.getByText('Loading profile charts...')).toBeInTheDocument();
        expect(screen.queryByText('Ranked Seasons')).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('tab', { name: 'Efficiency' }));
        expect(screen.getByText('Efficiency Badges')).toBeInTheDocument();
        expect(screen.queryByText('Performance by Tier')).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('tab', { name: 'Clan Battles' }));
        expect(screen.getByText('Clan Battle Seasons')).toBeInTheDocument();
        expect(screen.queryByText('Efficiency Badges')).not.toBeInTheDocument();
        expect(screen.queryByText('Performance by Tier')).not.toBeInTheDocument();
        expect(screen.queryByText('Tier vs Type Profile')).not.toBeInTheDocument();
    });

    it('shows the compact ranked empty state when the heatmap says the player has no ranked history', () => {
        mockRankedHeatmapVisibility = false;

        render(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="TestCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
            />,
        );

        fireEvent.click(screen.getByRole('tab', { name: 'Ranked' }));

        expect(screen.getByText('No ranked history is visible for this player yet.')).toBeInTheDocument();
        expect(screen.getByText('Ranked Seasons')).toBeInTheDocument();
    });

    it('keeps the selected tab while the same player route remains mounted', () => {
        const { rerender } = render(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="TestCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
            />,
        );

        fireEvent.click(screen.getByRole('tab', { name: 'Ships' }));

        rerender(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="TestCaptain"
                pvpRatio={56}
                pvpSurvivalRate={41}
                pvpBattles={820}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
            />,
        );

        expect(screen.getByRole('tab', { name: 'Ships' })).toHaveAttribute('aria-selected', 'true');
        expect(screen.getByText('Top Ships (Random Battles)')).toBeInTheDocument();
    });

    it('keeps the career tab empty for clanless players', () => {
        render(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="TestCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan={false}
                efficiencyRows={[]}
            />,
        );

        fireEvent.click(screen.getByRole('tab', { name: 'Clan Battles' }));

        expect(screen.queryByText('Clan Battle Seasons')).not.toBeInTheDocument();
        expect(screen.queryByText('Efficiency Badges')).not.toBeInTheDocument();
        expect(screen.queryByText('Performance by Tier')).not.toBeInTheDocument();
    });

    it('renders efficiency badges only inside the badges tab', () => {
        render(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="TestCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
            />,
        );

        expect(screen.queryByText('Efficiency Badges')).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('tab', { name: 'Efficiency' }));

        expect(screen.getByText('Efficiency Badges')).toBeInTheDocument();
        expect(screen.queryByText('Clan Battle Seasons')).not.toBeInTheDocument();
    });

    it('warms tab data only after the player shell finishes loading', async () => {
        const { rerender } = render(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="TestCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
                isLoading
            />,
        );

        await act(async () => {
            jest.advanceTimersByTime(250);
        });

        expect(mockFetchSharedJson).not.toHaveBeenCalled();

        await act(async () => {
            rerender(
                <PlayerDetailInsightsTabs
                    playerId={101}
                    playerName="TestCaptain"
                    pvpRatio={55}
                    pvpSurvivalRate={40}
                    pvpBattles={800}
                    hasKnownRankedGames
                    hasClan
                    efficiencyRows={[]}
                    isLoading={false}
                />,
            );
            await Promise.resolve();
        });

        await act(async () => {
            jest.advanceTimersByTime(250);
            await Promise.resolve();
        });

        await waitFor(() => {
            // 4 background warmup calls + 2 from the embedded Activity card (its
            // main window/mode fetch and the always-month sparkline fetch). The
            // profile chart still does NOT self-fetch tier_type until Profile is
            // selected; warmup pre-warms tier_type once.
            expect(mockFetchSharedJson).toHaveBeenCalledTimes(6);
        });

        expect(mockFetchSharedJson).toHaveBeenCalledWith('/api/fetch/player_correlation/ranked_wr_battles/101/?realm=na', expect.objectContaining({ ttlMs: 30000 }));
        expect(mockFetchSharedJson).toHaveBeenCalledWith('/api/fetch/ranked_data/101/?realm=na', expect.objectContaining({ ttlMs: 30000, cacheKey: 'ranked-data:101:0:0:0' }));
        expect(mockFetchSharedJson).toHaveBeenCalledWith('/api/fetch/player_correlation/tier_type/101/?realm=na', expect.objectContaining({ ttlMs: 30000 }));
        expect(mockFetchSharedJson).toHaveBeenCalledWith('/api/fetch/player_clan_battle_seasons/101/?realm=na', expect.objectContaining({ ttlMs: 30000 }));
        expect(mockFetchSharedJson).not.toHaveBeenCalledWith('/api/fetch/type_data/101/?realm=na', expect.anything());
        expect(mockFetchSharedJson).not.toHaveBeenCalledWith('/api/fetch/tier_data/101/?realm=na', expect.anything());
    });

    it('skips clan battle warmup for clanless players', async () => {
        render(
            <PlayerDetailInsightsTabs
                playerId={202}
                playerName="TestCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan={false}
                efficiencyRows={[]}
                isLoading={false}
            />,
        );

        await act(async () => {
            jest.advanceTimersByTime(250);
            await Promise.resolve();
        });

        await waitFor(() => {
            // 3 background warmup calls (no clan battle) + 2 from the embedded
            // Activity card (main fetch + month sparkline). The profile chart does
            // not self-fetch tier_type until selected.
            expect(mockFetchSharedJson).toHaveBeenCalledTimes(5);
        });

        expect(mockFetchSharedJson).not.toHaveBeenCalledWith(
            '/api/fetch/player_clan_battle_seasons/202/?realm=na',
            expect.anything(),
        );
        expect(mockFetchSharedJson).not.toHaveBeenCalledWith('/api/fetch/type_data/202/?realm=na', expect.anything());
        expect(mockFetchSharedJson).not.toHaveBeenCalledWith('/api/fetch/tier_data/202/?realm=na', expect.anything());
    });

    it('retries pending profile charts instead of freezing the empty placeholder payload', async () => {
        let tierTypeRequestCount = 0;

        mockFetchSharedJson.mockImplementation((url) => {
            if (url.includes('/api/fetch/player_correlation/tier_type/')) {
                tierTypeRequestCount += 1;

                if (tierTypeRequestCount < 3) {
                    return Promise.resolve({
                        data: pendingTierTypePayload,
                        headers: { 'X-Tier-Type-Pending': 'true' },
                    });
                }

                return Promise.resolve({
                    data: readyTierTypePayload,
                    headers: { 'X-Tier-Type-Pending': 'false' },
                });
            }

            return Promise.resolve({ data: [], headers: {} });
        });

        render(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="TestCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
                isLoading={false}
            />,
        );

        // Ships is the default tab now — activate Profile to exercise its charts.
        await act(async () => {
            fireEvent.click(screen.getByRole('tab', { name: 'Profile' }));
            await Promise.resolve();
        });

        expect(screen.getByText('Loading profile charts...')).toBeInTheDocument();

        await act(async () => {
            jest.advanceTimersByTime(1500);
            await Promise.resolve();
        });

        await act(async () => {
            jest.advanceTimersByTime(1500);
            await Promise.resolve();
        });

        await waitFor(() => {
            expect(screen.getByText('Tier vs Type Profile')).toBeInTheDocument();
        });

        expect(screen.getByText('Performance by Ship Type')).toBeInTheDocument();
        expect(screen.getByText('Performance by Tier')).toBeInTheDocument();
        expect(screen.queryByText('Profile charts are still warming. Try again in a moment.')).not.toBeInTheDocument();
        expect(tierTypeRequestCount).toBeGreaterThanOrEqual(3);
    });

    it('resets warming state to idle after 30s so profile charts can self-heal', async () => {
        // Serve pending responses until the warming message appears, then serve ready.
        let serveReady = false;

        mockFetchSharedJson.mockImplementation((url) => {
            if (url.includes('/api/fetch/player_correlation/tier_type/')) {
                if (serveReady) {
                    return Promise.resolve({
                        data: readyTierTypePayload,
                        headers: { 'X-Tier-Type-Pending': 'false' },
                    });
                }

                return Promise.resolve({
                    data: pendingTierTypePayload,
                    headers: { 'X-Tier-Type-Pending': 'true' },
                });
            }

            return Promise.resolve({ data: [], headers: {} });
        });

        render(
            <PlayerDetailInsightsTabs
                playerId={102}
                playerName="TestCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan={false}
                efficiencyRows={[]}
                isLoading={false}
            />,
        );

        // Ships is the default tab now — activate Profile to exercise its charts.
        await act(async () => {
            fireEvent.click(screen.getByRole('tab', { name: 'Profile' }));
            await Promise.resolve();
        });

        // Exhaust pending retries to reach warming state. PROFILE_PENDING_RETRY_LIMIT=5,
        // each retry fires after PROFILE_PENDING_RETRY_DELAY_MS=1500ms. Advance enough to
        // drain the full retry loop (extra advances are safe — no timers remain after warming).
        for (let i = 0; i < 8; i += 1) {
            await act(async () => {
                jest.advanceTimersByTime(1500);
                await Promise.resolve();
            });
        }

        await waitFor(() => {
            expect(screen.getByText('Profile charts are still warming. Try again in a moment.')).toBeInTheDocument();
        });

        // Switch the mock to return ready before the 30s recovery fires.
        serveReady = true;

        // Advance 30s — warming recovery timer resets state to idle and re-triggers the fetch.
        await act(async () => {
            jest.advanceTimersByTime(30_000);
            await Promise.resolve();
        });

        await waitFor(() => {
            expect(screen.getByText('Tier vs Type Profile')).toBeInTheDocument();
        });
    });
});