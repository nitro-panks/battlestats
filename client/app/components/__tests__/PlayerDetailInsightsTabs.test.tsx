import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import PlayerDetailInsightsTabs from '../PlayerDetailInsightsTabs';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
}));

let mockRankedHeatmapVisibility: boolean | undefined;
const mockFetchSharedJson = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

const readyTierTypePayload = {
    metric: 'tier_type' as const,
    label: 'Tier vs Ship Type',
    x_label: 'Ship Type',
    y_label: 'Tier',
    tracked_population: 739,
    tiles: [
        { ship_type: 'Battleship', ship_tier: 10, count: 320000 },
        { ship_type: 'Cruiser', ship_tier: 10, count: 410000 },
    ],
    trend: [
        { ship_type: 'Battleship', avg_tier: 9.3, count: 320000 },
        { ship_type: 'Cruiser', avg_tier: 9.1, count: 410000 },
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

        React.useEffect(() => {
            if (typeof props.onVisibilityChange === 'function' && mockRankedHeatmapVisibility !== undefined) {
                props.onVisibilityChange(mockRankedHeatmapVisibility);
            }
        }, [props.onVisibilityChange]);

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
        mockFetchSharedJson.mockReset();
        mockFetchSharedJson.mockImplementation((url) => {
            if (url.includes('/api/fetch/player_correlation/tier_type/')) {
                return new Promise(() => {});
            }

            return Promise.resolve({ data: [], headers: {} });
        });
        jest.useFakeTimers();
    });

    afterEach(() => {
        jest.runOnlyPendingTimers();
        jest.useRealTimers();
    });

    it('renders the profile lane by default and keeps other heavy panels inactive', () => {
        render(
            <PlayerDetailInsightsTabs
                playerId={101}
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
            />,
        );

        expect(screen.getByText('Loading profile charts...')).toBeInTheDocument();

        expect(screen.getByRole('tab', { name: 'Profile' })).toHaveAttribute('aria-selected', 'true');
        expect(screen.queryByText('Top Ships (Random Battles)')).not.toBeInTheDocument();
        expect(screen.queryByText('Ranked Seasons')).not.toBeInTheDocument();
        expect(screen.queryByText('Efficiency Badges')).not.toBeInTheDocument();
    });

    it('switches across the insights tabs one at a time', () => {
        render(
            <PlayerDetailInsightsTabs
                playerId={101}
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

        fireEvent.click(screen.getByRole('tab', { name: 'Badges' }));
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
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
            />,
        );

        expect(screen.queryByText('Efficiency Badges')).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('tab', { name: 'Badges' }));

        expect(screen.getByText('Efficiency Badges')).toBeInTheDocument();
        expect(screen.queryByText('Clan Battle Seasons')).not.toBeInTheDocument();
    });

    it('warms tab data only after the player shell finishes loading', async () => {
        const { rerender } = render(
            <PlayerDetailInsightsTabs
                playerId={101}
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
            expect(mockFetchSharedJson).toHaveBeenCalledTimes(6);
        });

        expect(mockFetchSharedJson).toHaveBeenCalledWith('/api/fetch/player_correlation/ranked_wr_battles/101/', expect.objectContaining({ ttlMs: 30000 }));
        expect(mockFetchSharedJson).toHaveBeenCalledWith('/api/fetch/ranked_data/101/', expect.objectContaining({ ttlMs: 30000, cacheKey: 'ranked-data:101:0:0' }));
        expect(mockFetchSharedJson).toHaveBeenCalledWith('/api/fetch/player_correlation/tier_type/101/', expect.objectContaining({ ttlMs: 30000 }));
        expect(mockFetchSharedJson).toHaveBeenCalledWith('/api/fetch/player_clan_battle_seasons/101/', expect.objectContaining({ ttlMs: 30000 }));
        expect(mockFetchSharedJson).not.toHaveBeenCalledWith('/api/fetch/type_data/101/', expect.anything());
        expect(mockFetchSharedJson).not.toHaveBeenCalledWith('/api/fetch/tier_data/101/', expect.anything());
    });

    it('skips clan battle warmup for clanless players', async () => {
        render(
            <PlayerDetailInsightsTabs
                playerId={202}
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
            expect(mockFetchSharedJson).toHaveBeenCalledTimes(5);
        });

        expect(mockFetchSharedJson).not.toHaveBeenCalledWith(
            '/api/fetch/player_clan_battle_seasons/202/',
            expect.anything(),
        );
        expect(mockFetchSharedJson).not.toHaveBeenCalledWith('/api/fetch/type_data/202/', expect.anything());
        expect(mockFetchSharedJson).not.toHaveBeenCalledWith('/api/fetch/tier_data/202/', expect.anything());
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
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
                isLoading={false}
            />,
        );

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
});