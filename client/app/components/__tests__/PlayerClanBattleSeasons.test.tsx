import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import PlayerClanBattleSeasons from '../PlayerClanBattleSeasons';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
    getChartFetchesInFlight: jest.fn(() => 0),
    incrementChartFetches: jest.fn(),
    decrementChartFetches: jest.fn(),
}));

const mockFetchSharedJson = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

describe('PlayerClanBattleSeasons', () => {
    afterEach(() => {
        jest.clearAllMocks();
    });

    it('renders the final empty state when there are no clan battle seasons', async () => {
        mockFetchSharedJson.mockResolvedValueOnce({
            data: [],
            headers: {},
        });

        render(<PlayerClanBattleSeasons playerId={101} />);

        expect(await screen.findByText('No clan battle season data available for this player.')).toBeInTheDocument();
    });

    it('renders summary cards and reports aggregate summary changes', async () => {
        const onSummaryChange = jest.fn();

        mockFetchSharedJson.mockResolvedValueOnce({
            data: [
                {
                    season_id: 32,
                    season_name: 'Typhoon Rising',
                    season_label: 'S32',
                    start_date: '2026-02-01',
                    end_date: '2026-03-01',
                    ship_tier_min: 8,
                    ship_tier_max: 10,
                    battles: 12,
                    wins: 7,
                    losses: 5,
                    win_rate: 58.3,
                },
                {
                    season_id: 31,
                    season_name: 'Steel Clash',
                    season_label: 'S31',
                    start_date: '2025-12-01',
                    end_date: '2026-01-01',
                    ship_tier_min: 8,
                    ship_tier_max: 10,
                    battles: 8,
                    wins: 4,
                    losses: 4,
                    win_rate: 50,
                },
            ],
            headers: {},
        });

        render(<PlayerClanBattleSeasons playerId={202} onSummaryChange={onSummaryChange} />);

        expect(await screen.findByText('S32')).toBeInTheDocument();
        expect(screen.getByText('S31')).toBeInTheDocument();
        expect(screen.getByText('20')).toBeInTheDocument();

        await waitFor(() => {
            expect(onSummaryChange).toHaveBeenCalled();
        });

        expect(onSummaryChange).toHaveBeenLastCalledWith(expect.objectContaining({
            seasonsPlayed: 2,
            totalBattles: 20,
            // No row flagged is_current → sitting the current season out.
            currentSeasonBattles: 0,
            currentSeasonWinRate: null,
        }));

        expect(onSummaryChange.mock.calls.at(-1)?.[0]?.overallWinRate).toBeCloseTo(55, 5);
    });

    it('reports the current-season slice from the server-flagged is_current row', async () => {
        const onSummaryChange = jest.fn();

        mockFetchSharedJson.mockResolvedValueOnce({
            data: [
                {
                    season_id: 34,
                    season_name: 'Hammerhead',
                    season_label: 'S34',
                    start_date: '2026-06-22',
                    end_date: '2026-08-10',
                    ship_tier_min: 10,
                    ship_tier_max: 10,
                    battles: 8,
                    wins: 5,
                    losses: 3,
                    win_rate: 62.5,
                    is_current: true,
                },
                {
                    season_id: 33,
                    season_name: 'Blue Marlin',
                    season_label: 'S33',
                    start_date: '2026-03-16',
                    end_date: '2026-05-18',
                    ship_tier_min: 10,
                    ship_tier_max: 10,
                    battles: 40,
                    wins: 18,
                    losses: 22,
                    win_rate: 45.0,
                    is_current: false,
                },
            ],
            headers: {},
        });

        render(<PlayerClanBattleSeasons playerId={303} onSummaryChange={onSummaryChange} />);

        expect(await screen.findByText('S34')).toBeInTheDocument();

        await waitFor(() => {
            expect(onSummaryChange).toHaveBeenCalled();
        });

        expect(onSummaryChange).toHaveBeenLastCalledWith(expect.objectContaining({
            seasonsPlayed: 2,
            totalBattles: 48,
            currentSeasonBattles: 8,
            currentSeasonWinRate: 62.5,
        }));
    });
});