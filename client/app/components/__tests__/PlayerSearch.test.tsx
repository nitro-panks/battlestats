import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import PlayerSearch from '../PlayerSearch';

const pushMock = jest.fn();
const replaceMock = jest.fn();

jest.mock('next/navigation', () => ({
    useRouter: () => ({ push: pushMock, replace: replaceMock }),
}));

// Treemap + ship leaderboard own their data fetching and have dedicated tests;
// stub them so the landing renders without their network traffic.
jest.mock('../RealmTopShipsTreemapSVG', () => {
    return function MockRealmTopShipsTreemapSVG() {
        return <div data-testid="realm-top-ships-treemap" />;
    };
});

jest.mock('../ShipLeaderboard', () => {
    return function MockShipLeaderboard() {
        return <div data-testid="ship-leaderboard" />;
    };
});

// The landing is now search-funnel + treemap + ship leaderboard only. The
// player view lives solely at /player/<name> (with its clan rail in the route
// layout); PlayerSearch no longer renders PlayerDetail inline. A ?q= deep-link
// (the SEO SearchAction target) redirects to the canonical route.
describe('PlayerSearch', () => {
    let consoleErrorSpy: jest.SpyInstance;

    beforeEach(() => {
        pushMock.mockReset();
        replaceMock.mockReset();
        window.history.replaceState({}, '', '/');
        global.fetch = jest.fn(() => Promise.reject(new Error('no fetch expected'))) as jest.Mock;
        consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    });

    afterEach(() => {
        consoleErrorSpy.mockRestore();
    });

    it('renders the landing funnel (treemap + ship leaderboard) with no featured boards', async () => {
        render(<PlayerSearch />);

        expect(await screen.findByTestId('realm-top-ships-treemap')).toBeInTheDocument();
        expect(screen.getByTestId('ship-leaderboard')).toBeInTheDocument();
        // The decommissioned featured boards are gone.
        expect(screen.queryByRole('heading', { name: 'Players' })).not.toBeInTheDocument();
        expect(screen.queryByRole('heading', { name: 'Active Clans' })).not.toBeInTheDocument();
        // No landing-best traffic anymore.
        expect((global.fetch as jest.Mock).mock.calls.some(
            ([url]) => typeof url === 'string' && url.includes('/api/landing/'),
        )).toBe(false);
        // Bare landing does not redirect.
        expect(replaceMock).not.toHaveBeenCalled();
    });

    it('redirects a ?q= deep-link to the canonical player route (preserving realm)', async () => {
        window.history.replaceState({}, '', '/?q=Player%20One');

        render(<PlayerSearch />);

        await waitFor(() => {
            expect(replaceMock).toHaveBeenCalledWith('/player/Player%20One?realm=na');
        });
        // The player view is no longer rendered inline on the landing.
        expect(screen.queryByTestId('player-detail')).not.toBeInTheDocument();
    });

    it('does not redirect when there is no q parameter', async () => {
        render(<PlayerSearch />);

        await screen.findByTestId('ship-leaderboard');
        expect(replaceMock).not.toHaveBeenCalled();
    });
});
