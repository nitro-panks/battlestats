import { act, render, screen, waitFor } from '@testing-library/react';
import RankedSeasons from '../RankedSeasons';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../../lib/playerRouteFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
    getChartFetchesInFlight: jest.fn(() => 0),
    incrementChartFetches: jest.fn(),
    decrementChartFetches: jest.fn(),
}));

const mockFetchSharedJson = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

describe('RankedSeasons', () => {
    afterEach(() => {
        jest.clearAllMocks();
        jest.useRealTimers();
    });

    it('keeps a refreshing state for pending empty ranked data and then renders seasons', async () => {
        jest.useFakeTimers();
        mockFetchSharedJson
            .mockResolvedValueOnce({
                data: [],
                headers: { 'X-Ranked-Pending': 'true' },
            })
            .mockResolvedValueOnce({
                data: [
                    {
                        season_id: 1001,
                        season_name: 'Pilot Season',
                        season_label: 'S1',
                        start_date: '2020-12-21',
                        end_date: '2021-02-02',
                        highest_league: 3,
                        highest_league_name: 'Gold',
                        total_battles: 10,
                        total_wins: 6,
                        win_rate: 0.6,
                        top_ship_name: 'Stalingrad',
                        best_sprint: null,
                        sprints: [],
                    },
                ],
                headers: { 'X-Ranked-Pending': null },
            });

        render(<RankedSeasons playerId={123} />);

        expect(await screen.findByText('Refreshing ranked seasons...')).toBeInTheDocument();
        expect(screen.queryByText('No ranked seasons found for this player.')).not.toBeInTheDocument();

        await act(async () => {
            jest.advanceTimersByTime(1500);
        });

        await waitFor(() => {
            expect(screen.getByText('S1')).toBeInTheDocument();
        });

        expect(mockFetchSharedJson).toHaveBeenNthCalledWith(1, '/api/fetch/ranked_data/123/', expect.objectContaining({
            ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS,
            cacheKey: 'ranked-data:123:0:0',
        }));
    });

    it('shows the final empty state when ranked data is empty and not pending', async () => {
        mockFetchSharedJson.mockResolvedValueOnce({
            data: [],
            headers: { 'X-Ranked-Pending': null },
        });

        render(<RankedSeasons playerId={456} />);

        expect(await screen.findByText('No ranked seasons found for this player.')).toBeInTheDocument();
    });
});
