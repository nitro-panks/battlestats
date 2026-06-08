import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import ShipRouteView from '../ShipRouteView';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
}));

jest.mock('../../context/RealmContext', () => ({
    useRealm: () => ({ realm: 'na' }),
}));

const trackEventMock = jest.fn();
jest.mock('../../lib/umami', () => ({
    trackEvent: (...args: unknown[]) => trackEventMock(...args),
}));

const mockFetch = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

const leaderboardFixture = {
    realm: 'na',
    window_days: 14,
    captured_on: '2026-06-06',
    ship: {
        ship_id: 12345,
        name: 'Shimakaze',
        tier: 10,
        ship_type: 'Destroyer',
        nation: 'japan',
        is_premium: false,
    },
    players: [
        { rank: 1, player_name: 'ChampDD', win_rate: 62.5, battles: 145, avg_damage: 48200, kills_per_battle: 1.23 },
        { rank: 2, player_name: 'RunnerUp', win_rate: 58.3, battles: 120, avg_damage: 44100, kills_per_battle: 1.15 },
    ],
};

describe('ShipRouteView', () => {
    let consoleErrorSpy: jest.SpyInstance;

    beforeEach(() => {
        trackEventMock.mockReset();
        mockFetch.mockReset();
        consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    });

    afterEach(() => {
        consoleErrorSpy.mockRestore();
    });

    it('fetches the realm-scoped leaderboard and renders the ship masthead + players', async () => {
        mockFetch.mockResolvedValue({ data: leaderboardFixture } as never);

        render(<ShipRouteView shipSlug="12345-shimakaze" />);

        // Masthead ship name appears once the payload resolves.
        expect(await screen.findByRole('heading', { name: 'Shimakaze' })).toBeInTheDocument();

        // Fetch hit the realm-scoped endpoint with the documented cache options.
        expect(mockFetch).toHaveBeenCalledWith(
            '/api/realm/na/ship/12345/leaderboard',
            expect.objectContaining({
                label: 'ShipLeaderboard:na:12345',
                cacheKey: 'ship-lb:na:12345',
            }),
        );

        // Both leaderboard rows render (desktop table + mobile cards both present,
        // so each name appears at least once).
        expect(screen.getAllByText('ChampDD').length).toBeGreaterThan(0);
        expect(screen.getAllByText('RunnerUp').length).toBeGreaterThan(0);

        // Page-view analytics fired with ship identity.
        expect(trackEventMock).toHaveBeenCalledWith('ship-page-view', {
            ship_id: 12345,
            ship_name: 'Shimakaze',
            realm: 'na',
        });
    });

    it('shows the empty-standings message when the ship has no ranked players', async () => {
        mockFetch.mockResolvedValue({ data: { ...leaderboardFixture, players: [] } } as never);

        render(<ShipRouteView shipSlug="12345-shimakaze" />);

        expect(await screen.findByText(/No ranked standings for this ship yet/i)).toBeInTheDocument();
    });

    it('renders the not-found state for an unparseable slug without fetching', async () => {
        render(<ShipRouteView shipSlug="not-a-ship" />);

        expect(await screen.findByText(/may not have ranked this ship yet/i)).toBeInTheDocument();
        expect(mockFetch).not.toHaveBeenCalled();
    });

    it('falls back to the error state when the fetch rejects', async () => {
        mockFetch.mockRejectedValue(new Error('boom'));

        render(<ShipRouteView shipSlug="12345-shimakaze" />);

        await waitFor(() => {
            expect(screen.getByText(/may not have ranked this ship yet/i)).toBeInTheDocument();
        });
    });
});
