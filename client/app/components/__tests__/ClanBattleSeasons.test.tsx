import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import ClanBattleSeasons from '../ClanBattleSeasons';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
    getChartFetchesInFlight: jest.fn(() => 0),
    isAbortError: jest.fn(() => false),
}));

// The SVG draws with D3 and is irrelevant to the cold-cache poll behavior.
jest.mock('../ClanBattleSeasonsSVG', () => function MockClanBattleSeasonsSVG() {
    return <div data-testid="cb-svg" />;
});

const mockFetchSharedJson = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

const SEASON = {
    season_id: 30,
    season_name: 'Winter Clash',
    season_label: 'S30',
    start_date: '2026-01-01',
    end_date: '2026-02-01',
    ship_tier_min: 10,
    ship_tier_max: 10,
    participants: 18,
    roster_battles: 120,
    roster_wins: 70,
    roster_losses: 50,
    roster_win_rate: 58.3,
    clan_battles: 40,
    clan_wins: 24,
};

describe('ClanBattleSeasons cold-cache poll', () => {
    afterEach(() => {
        jest.clearAllMocks();
        jest.useRealTimers();
    });

    it('renders seasons when a non-pending payload lands', async () => {
        mockFetchSharedJson.mockResolvedValue({ data: [SEASON], headers: {} } as never);

        render(<ClanBattleSeasons clanId={555} memberCount={30} />);

        expect(await screen.findByText('Winter Clash')).toBeInTheDocument();
        expect(screen.queryByText('No clan battles season data available.')).not.toBeInTheDocument();
    });

    it('shows the definitive empty state only when the empty payload is NOT pending', async () => {
        mockFetchSharedJson.mockResolvedValue({ data: [], headers: {} } as never);

        render(<ClanBattleSeasons clanId={555} memberCount={30} />);

        expect(await screen.findByText('No clan battles season data available.')).toBeInTheDocument();
    });

    it('does not claim "No data" while a cold warm is still pending — keeps the loading state', async () => {
        // Empty + pending header = the cold-cache warm is still in flight. The
        // bug was showing the definitive empty state here (then real data on reload).
        mockFetchSharedJson.mockResolvedValue({
            data: [],
            headers: { 'X-Clan-Battles-Pending': 'true' },
        } as never);

        render(<ClanBattleSeasons clanId={555} memberCount={30} />);

        // After the first pending response it schedules a retry, so it stays in the
        // loading state and must NOT show the definitive empty message.
        expect(await screen.findByText('Loading clan battles seasons...')).toBeInTheDocument();
        expect(screen.queryByText('No clan battles season data available.')).not.toBeInTheDocument();
    });

    it('surfaces a "still loading" hint (not "No data") after the pending retries are exhausted', async () => {
        jest.useFakeTimers();
        mockFetchSharedJson.mockResolvedValue({
            data: [],
            headers: { 'X-Clan-Battles-Pending': 'true' },
        } as never);

        render(<ClanBattleSeasons clanId={555} memberCount={30} />);

        // Run out the bounded retry budget (12 × 1500ms) plus headroom.
        await act(async () => {
            await jest.advanceTimersByTimeAsync(12 * 1500 + 100);
        });

        await waitFor(() => {
            expect(screen.getByText('Clan battle data is still loading — refresh in a moment.')).toBeInTheDocument();
        });
        expect(screen.queryByText('No clan battles season data available.')).not.toBeInTheDocument();
    });
});
