import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import ShipLeaderboard from '../ShipLeaderboard';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
}));

jest.mock('../../context/RealmContext', () => ({
    useRealm: () => ({ realm: 'na' }),
}));

jest.mock('../../lib/umami', () => ({
    trackEvent: jest.fn(),
}));

const mockFetch = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

const listFixture = {
    realm: 'na',
    tier: 10,
    ship_type: 'Destroyer',
    ships: [
        { ship_id: 222, ship_name: 'Gearing', ship_type: 'Destroyer', tier: 10, nation: 'usa', is_premium: false, battles: 11044, win_rate: 60.0, avg_damage: 61880, kills_per_battle: 0.88 },
        { ship_id: 111, ship_name: 'Shimakaze', ship_type: 'Destroyer', tier: 10, nation: 'japan', is_premium: false, battles: 20310, win_rate: 53.2, avg_damage: 54210, kills_per_battle: 0.71 },
    ],
};

const boardFixture = {
    realm: 'na',
    ship: { ship_id: 111, name: 'Shimakaze', tier: 10, ship_type: 'Destroyer', nation: 'japan', is_premium: false },
    players: [
        { rank: 1, player_name: 'UsunU', win_rate: 65.3, battles: 95, avg_damage: 68227, kills_per_battle: 1.07 },
    ],
};

// Route the mock by URL so list and drill-down resolve from one mock.
const routeFetch = (url: string) => {
    if (url.includes('/ships?')) return Promise.resolve({ data: listFixture } as never);
    if (url.includes('/leaderboard')) return Promise.resolve({ data: boardFixture } as never);
    return Promise.reject(new Error(`unexpected url ${url}`));
};

describe('ShipLeaderboard', () => {
    beforeEach(() => {
        mockFetch.mockReset();
        mockFetch.mockImplementation((url: string) => routeFetch(url));
    });

    const selectTierAndType = (typeAbbr = 'DD') => {
        fireEvent.click(screen.getByRole('button', { name: '10' }));
        fireEvent.click(screen.getByRole('button', { name: typeAbbr }));
    };

    // Ship/player names render twice (desktop table + mobile cards), so always
    // resolve the first match.
    const clickShip = async (name: string) =>
        fireEvent.click((await screen.findAllByRole('button', { name }))[0]);

    it('shows a prompt until both filters are chosen, then fetches the ship list', async () => {
        render(<ShipLeaderboard />);
        expect(screen.getByText(/pick a tier and a type/i)).toBeInTheDocument();
        expect(mockFetch).not.toHaveBeenCalled();

        selectTierAndType('DD');

        await screen.findAllByText('Gearing');
        // List endpoint hit with the tier+type query params.
        expect(mockFetch).toHaveBeenCalledWith(
            '/api/realm/na/ships?tier=10&type=Destroyer',
            expect.objectContaining({ cacheKey: 'ships-by:na:10:Destroyer' }),
        );
        // Ordered as the payload returned (WR desc): Gearing before Shimakaze.
        const names = screen.getAllByRole('button', { name: /Gearing|Shimakaze/ }).map((b) => b.textContent);
        expect(names[0]).toContain('Gearing');
    });

    it('sends the raw "AirCarrier" type value (no space) when CV is selected', async () => {
        render(<ShipLeaderboard />);
        selectTierAndType('CV');
        await waitFor(() => {
            expect(mockFetch).toHaveBeenCalledWith(
                '/api/realm/na/ships?tier=10&type=AirCarrier',
                expect.anything(),
            );
        });
    });

    it('swaps the table in place for the ship board on click, with no navigation', async () => {
        render(<ShipLeaderboard />);
        selectTierAndType('DD');
        await clickShip('Shimakaze');

        // Drill-down board renders the player; list column header is gone.
        await screen.findAllByText('UsunU');
        expect(mockFetch).toHaveBeenCalledWith(
            '/api/realm/na/ship/111/leaderboard',
            expect.objectContaining({ cacheKey: 'ship-lb:na:111' }),
        );
        // Clear control present in the board view.
        expect(screen.getByRole('button', { name: /clear/i })).toBeInTheDocument();
        // Player names link through to the player profile (realm-scoped).
        const playerLinks = screen.getAllByRole('link', { name: 'UsunU' });
        expect(playerLinks.length).toBeGreaterThan(0);
        expect(playerLinks[0]).toHaveAttribute('href', expect.stringContaining('/player/UsunU'));
        expect(playerLinks[0]).toHaveAttribute('href', expect.stringContaining('realm=na'));
    });

    it('Clear returns to the same tier/type ship list', async () => {
        render(<ShipLeaderboard />);
        selectTierAndType('DD');
        await clickShip('Shimakaze');
        await screen.findAllByText('UsunU');

        fireEvent.click(screen.getByRole('button', { name: /clear/i }));

        // Back to the list (tier 10 + Destroyer still active) — Gearing reappears.
        await screen.findAllByText('Gearing');
        expect(screen.getByRole('button', { name: '10' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: 'DD' })).toHaveAttribute('aria-pressed', 'true');
    });

    it('changing the type while in a board resets back to the list view', async () => {
        render(<ShipLeaderboard />);
        selectTierAndType('DD');
        await clickShip('Shimakaze');
        await screen.findAllByText('UsunU');

        // Switch type — the open board must drop and the list comes back.
        fireEvent.click(screen.getByRole('button', { name: 'BB' }));
        await screen.findAllByText('Gearing');
        expect(screen.queryByText('UsunU')).not.toBeInTheDocument();
    });
});
