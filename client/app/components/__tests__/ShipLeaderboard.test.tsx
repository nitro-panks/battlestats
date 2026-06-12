import React from 'react';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import ShipLeaderboard, { type ShipLeaderboardHandle } from '../ShipLeaderboard';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';
import { trackEvent } from '../../lib/umami';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
}));

// The real SubmarineEasterEgg runs a D3 animation effect that calls
// window.matchMedia (unimplemented in jsdom) and schedules transition rAF
// loops — neither is meaningful under Jest. Stub it to a container that
// exposes the same aria-label so the wiring/branch assertions can find it.
jest.mock('../SubmarineEasterEgg', () => ({
    __esModule: true,
    default: () => (
        <div aria-label="There are no Tier 9 submarines — but here is one anyway." />
    ),
}));

jest.mock('../../context/RealmContext', () => ({
    useRealm: () => ({ realm: 'na' }),
}));

jest.mock('../../lib/umami', () => ({
    trackEvent: jest.fn(),
}));

const mockFetch = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;
const mockTrack = trackEvent as jest.MockedFunction<typeof trackEvent>;

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
        mockTrack.mockClear();
        // jsdom has no scrollIntoView; the imperative handle calls it.
        Element.prototype.scrollIntoView = jest.fn();
    });

    const selectTierAndType = (typeAbbr = 'DD') => {
        fireEvent.click(screen.getByRole('button', { name: '10' }));
        fireEvent.click(screen.getByRole('button', { name: typeAbbr }));
    };

    // Ship/player names render twice (desktop table + mobile cards), so always
    // resolve the first match.
    const clickShip = async (name: string) =>
        fireEvent.click((await screen.findAllByRole('button', { name }))[0]);

    it('defaults to T10 Battleships and fetches that list on mount', async () => {
        render(<ShipLeaderboard />);
        // No "pick a tier/type" prompt — both filters are pre-selected.
        expect(screen.queryByText(/pick a tier and a type/i)).not.toBeInTheDocument();
        await waitFor(() => {
            expect(mockFetch).toHaveBeenCalledWith(
                '/api/realm/na/ships?tier=10&type=Battleship',
                expect.objectContaining({ cacheKey: 'ships-by:na:10:Battleship' }),
            );
        });
        // T10 and BB pills render pre-pressed.
        expect(screen.getByRole('button', { name: '10' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: 'BB' })).toHaveAttribute('aria-pressed', 'true');
    });

    it('switching type re-fetches and renders the list WR-desc', async () => {
        render(<ShipLeaderboard />);
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

    describe('treemap handoff (selectShip handle)', () => {
        it('drills straight to a ship board, scrolls, and sets the underlying filters', async () => {
            const ref = React.createRef<ShipLeaderboardHandle>();
            render(<ShipLeaderboard ref={ref} />);
            // Let the default T10/BB list settle, then clear the call log so the
            // assertions below only see fetches caused by the handle.
            await screen.findAllByText('Gearing');
            mockFetch.mockClear();

            act(() => {
                ref.current!.selectShip({ id: 111, name: 'Shimakaze', tier: 10, type: 'Destroyer' });
            });

            // (a) Board endpoint fetched and the player board renders...
            await screen.findAllByText('UsunU');
            expect(mockFetch).toHaveBeenCalledWith(
                '/api/realm/na/ship/111/leaderboard',
                expect.objectContaining({ cacheKey: 'ship-lb:na:111' }),
            );
            // (b) ...without needing the ship-list endpoint to reach the board.
            expect(mockFetch).not.toHaveBeenCalledWith(
                expect.stringContaining('/ships?'),
                expect.anything(),
            );
            // (c) The section was scrolled into view.
            expect(Element.prototype.scrollIntoView).toHaveBeenCalled();

            // (d) Clear returns to the T10 Destroyer list — proves tier+type were set.
            fireEvent.click(screen.getByRole('button', { name: /clear/i }));
            await waitFor(() => {
                expect(mockFetch).toHaveBeenCalledWith(
                    '/api/realm/na/ships?tier=10&type=Destroyer',
                    expect.objectContaining({ cacheKey: 'ships-by:na:10:Destroyer' }),
                );
            });
            expect(screen.getByRole('button', { name: '10' })).toHaveAttribute('aria-pressed', 'true');
            expect(screen.getByRole('button', { name: 'DD' })).toHaveAttribute('aria-pressed', 'true');
        });

        it('tags the drill-down event with source=treemap', async () => {
            const ref = React.createRef<ShipLeaderboardHandle>();
            render(<ShipLeaderboard ref={ref} />);
            await screen.findAllByText('Gearing');

            act(() => {
                ref.current!.selectShip({ id: 111, name: 'Shimakaze', tier: 10, type: 'Destroyer' });
            });
            expect(mockTrack).toHaveBeenCalledWith(
                'ship-leaderboard-drilldown',
                expect.objectContaining({ realm: 'na', ship_id: 111, source: 'treemap' }),
            );
        });
    });

    describe('T9 submarine easter egg', () => {
        const T9_SUB_LABEL = 'There are no Tier 9 submarines — but here is one anyway.';

        it('renders the easter egg for T9 + Submarine with NO fetch and no dead-end message', async () => {
            render(<ShipLeaderboard />);
            // Let the default T10/BB list settle, then clear the call log so the
            // assertions below only see fetches caused by selecting T9 + SS.
            await screen.findAllByText('Gearing');
            mockFetch.mockClear();

            fireEvent.click(screen.getByRole('button', { name: '9' }));
            fireEvent.click(screen.getByRole('button', { name: 'SS' }));

            // (a) The easter-egg container renders (query by its aria-label).
            await screen.findByLabelText(T9_SUB_LABEL);
            // (b) The dead-end "No ranked ships" message is absent.
            expect(screen.queryByText(/no ranked ships/i)).not.toBeInTheDocument();
            // (c) The short-circuit fired no T9+Submarine ships fetch.
            await waitFor(() => {
                expect(mockFetch).not.toHaveBeenCalledWith(
                    expect.stringContaining('tier=9&type=Submarine'),
                    expect.anything(),
                );
            });
        });

        it('tracks a umami event once each time the animation surfaces', async () => {
            render(<ShipLeaderboard />);
            await screen.findAllByText('Gearing');
            mockTrack.mockClear();

            fireEvent.click(screen.getByRole('button', { name: '9' }));
            fireEvent.click(screen.getByRole('button', { name: 'SS' }));
            await screen.findByLabelText(T9_SUB_LABEL);

            const eggCalls = () =>
                mockTrack.mock.calls.filter((c) => c[0] === 'ship-leaderboard-easter-egg');
            expect(eggCalls()).toHaveLength(1);
            expect(mockTrack).toHaveBeenCalledWith('ship-leaderboard-easter-egg', {
                realm: 'na',
                egg: 't9-submarine',
            });

            // Leaving and re-entering the combo counts as a fresh activation.
            fireEvent.click(screen.getByRole('button', { name: 'DD' }));
            expect(screen.queryByLabelText(T9_SUB_LABEL)).not.toBeInTheDocument();
            fireEvent.click(screen.getByRole('button', { name: 'SS' }));
            await screen.findByLabelText(T9_SUB_LABEL);
            expect(eggCalls()).toHaveLength(2);
        });
    });

    describe('umami tracking', () => {
        it('tracks tier and type filter clicks with a clear control field', async () => {
            render(<ShipLeaderboard />);
            await screen.findAllByText('Gearing');

            fireEvent.click(screen.getByRole('button', { name: 'DD' }));
            expect(mockTrack).toHaveBeenCalledWith(
                'ship-leaderboard-filter',
                expect.objectContaining({ realm: 'na', control: 'type', type: 'Destroyer' }),
            );

            fireEvent.click(screen.getByRole('button', { name: '8' }));
            expect(mockTrack).toHaveBeenCalledWith(
                'ship-leaderboard-filter',
                expect.objectContaining({ realm: 'na', control: 'tier', tier: 8 }),
            );
        });

        it('tracks ship-list column sorts with scope, column and direction', async () => {
            render(<ShipLeaderboard />);
            await screen.findAllByText('Gearing');

            fireEvent.click(screen.getAllByRole('button', { name: /Battles/ })[0]);
            expect(mockTrack).toHaveBeenCalledWith(
                'ship-leaderboard-sort',
                { realm: 'na', scope: 'ships', column: 'battles', dir: 'desc' },
            );
        });

        it('tracks the drill-down and player click-through with clear event names', async () => {
            render(<ShipLeaderboard />);
            await clickShip('Shimakaze');
            expect(mockTrack).toHaveBeenCalledWith(
                'ship-leaderboard-drilldown',
                expect.objectContaining({ realm: 'na', ship_id: 111 }),
            );

            await screen.findAllByText('UsunU');
            fireEvent.click(screen.getAllByRole('link', { name: 'UsunU' })[0]);
            expect(mockTrack).toHaveBeenCalledWith(
                'ship-leaderboard-player-click',
                { realm: 'na', ship_id: 111, rank: 1 },
            );
        });

        it('tracks a player-board column sort under the players scope', async () => {
            render(<ShipLeaderboard />);
            await clickShip('Shimakaze');
            await screen.findAllByText('UsunU');

            fireEvent.click(screen.getAllByRole('button', { name: /Win rate/ })[0]);
            expect(mockTrack).toHaveBeenCalledWith(
                'ship-leaderboard-sort',
                { realm: 'na', scope: 'players', column: 'win_rate', dir: 'desc' },
            );
        });
    });
});
