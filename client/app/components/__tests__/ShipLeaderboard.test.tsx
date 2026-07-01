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

// CarrierEasterEgg paints a <canvas> in a useEffect (getContext is a no-op stub
// under jsdom). Stub it to a container with the same aria-label so the
// wiring/branch assertions can find it without exercising canvas drawing.
jest.mock('../CarrierEasterEgg', () => ({
    __esModule: true,
    default: () => (
        <div aria-label="There are no Tier 9 aircraft carriers — but here is one rendered in binary." />
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
        // The component now persists tier/type/WR to localStorage; clear it so a
        // selection made in one test doesn't get restored into the next.
        localStorage.clear();
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
            // Default view is the top-50% WR cut — fetched with &wr_pct=50 and
            // bypassing the client settled cache (ttlMs:0).
            expect(mockFetch).toHaveBeenCalledWith(
                '/api/realm/na/ships?tier=10&type=Battleship&wr_pct=50',
                expect.objectContaining({ ttlMs: 0 }),
            );
        });
        // T10, BB and the 50% WR pill render pre-pressed; All is not.
        expect(screen.getByRole('button', { name: '10' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: 'BB' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: '50%' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'false');
    });

    it('switching type re-fetches and renders the list WR-desc', async () => {
        render(<ShipLeaderboard />);
        selectTierAndType('DD');

        await screen.findAllByText('Gearing');
        // List endpoint hit with the tier+type query params (the default 50% WR
        // filter persists across a type switch).
        expect(mockFetch).toHaveBeenCalledWith(
            '/api/realm/na/ships?tier=10&type=Destroyer&wr_pct=50',
            expect.objectContaining({ ttlMs: 0 }),
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
                '/api/realm/na/ships?tier=10&type=AirCarrier&wr_pct=50',
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
                    '/api/realm/na/ships?tier=10&type=Destroyer&wr_pct=50',
                    expect.objectContaining({ ttlMs: 0 }),
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

    describe('T9 carrier easter egg', () => {
        const T9_CV_LABEL =
            'There are no Tier 9 aircraft carriers — but here is one rendered in binary.';

        it('renders the easter egg for T9 + CV with NO fetch and no dead-end message', async () => {
            render(<ShipLeaderboard />);
            await screen.findAllByText('Gearing');
            mockFetch.mockClear();

            fireEvent.click(screen.getByRole('button', { name: '9' }));
            fireEvent.click(screen.getByRole('button', { name: 'CV' }));

            // (a) The easter-egg container renders (query by its aria-label).
            await screen.findByLabelText(T9_CV_LABEL);
            // (b) The dead-end "No ranked ships" message is absent.
            expect(screen.queryByText(/no ranked ships/i)).not.toBeInTheDocument();
            // (c) The short-circuit fired no T9+AirCarrier ships fetch.
            await waitFor(() => {
                expect(mockFetch).not.toHaveBeenCalledWith(
                    expect.stringContaining('tier=9&type=AirCarrier'),
                    expect.anything(),
                );
            });
        });

        it('tracks a t9-carrier umami event once each time it surfaces', async () => {
            render(<ShipLeaderboard />);
            await screen.findAllByText('Gearing');
            mockTrack.mockClear();

            fireEvent.click(screen.getByRole('button', { name: '9' }));
            fireEvent.click(screen.getByRole('button', { name: 'CV' }));
            await screen.findByLabelText(T9_CV_LABEL);

            const eggCalls = () =>
                mockTrack.mock.calls.filter((c) => c[0] === 'ship-leaderboard-easter-egg');
            expect(eggCalls()).toHaveLength(1);
            expect(mockTrack).toHaveBeenCalledWith('ship-leaderboard-easter-egg', {
                realm: 'na',
                egg: 't9-carrier',
            });

            // Leaving and re-entering the combo counts as a fresh activation.
            fireEvent.click(screen.getByRole('button', { name: 'DD' }));
            expect(screen.queryByLabelText(T9_CV_LABEL)).not.toBeInTheDocument();
            fireEvent.click(screen.getByRole('button', { name: 'CV' }));
            await screen.findByLabelText(T9_CV_LABEL);
            expect(eggCalls()).toHaveLength(2);
        });
    });

    describe('class/tier share %', () => {
        it('renders each ship\'s battles as a share of the bucket total_battles', async () => {
            // total_battles 40,000 → Gearing 11,044 = 27.6%, Shimakaze 20,310 = 50.8%.
            mockFetch.mockImplementation((url: string) => {
                if (url.includes('/ships?')) {
                    return Promise.resolve({ data: { ...listFixture, total_battles: 40000 } } as never);
                }
                return routeFetch(url);
            });
            render(<ShipLeaderboard />);
            selectTierAndType('DD');
            await screen.findAllByText('Gearing');

            // The share renders in its own parenthesised span (desktop + mobile),
            // so each percentage appears at least once.
            expect(screen.getAllByText('(27.6%)').length).toBeGreaterThan(0);
            expect(screen.getAllByText('(50.8%)').length).toBeGreaterThan(0);
        });

        it('omits the share when total_battles is absent (e.g. a pre-field payload)', async () => {
            mockFetch.mockImplementation((url: string) => {
                if (url.includes('/ships?')) {
                    // No total_battles key at all → battles-only, no NaN%.
                    return Promise.resolve({ data: listFixture } as never);
                }
                return routeFetch(url);
            });
            render(<ShipLeaderboard />);
            selectTierAndType('DD');
            await screen.findAllByText('Gearing');

            // No parenthesised share token anywhere (win rate uses no parens).
            expect(screen.queryByText(/\(\d+(\.\d+)?%\)/)).toBeNull();
            expect(screen.queryByText('<0.1%')).toBeNull();
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

    describe('ship-list sort persistence', () => {
        const STORAGE_KEY = 'battlestats:ship-list:sort';

        const rowOrder = () =>
            screen
                .getAllByRole('button', { name: /Gearing|Shimakaze/ })
                .map((b) => b.textContent);

        it('persists the chosen ship-list column sort to localStorage', async () => {
            render(<ShipLeaderboard />);
            selectTierAndType('DD');
            await screen.findAllByText('Gearing');

            // Battles desc reorders Shimakaze (20310) ahead of Gearing (11044) —
            // distinct from the payload's natural win-rate order (Gearing first).
            fireEvent.click(screen.getAllByRole('button', { name: /Battles/ })[0]);
            expect(rowOrder()[0]).toContain('Shimakaze');
            expect(JSON.parse(localStorage.getItem(STORAGE_KEY) as string)).toEqual({
                key: 'battles',
                dir: 'desc',
            });
        });

        it('restores the persisted sort on a fresh mount instead of the server order', async () => {
            localStorage.setItem(STORAGE_KEY, JSON.stringify({ key: 'battles', dir: 'desc' }));

            render(<ShipLeaderboard />);
            selectTierAndType('DD');
            await screen.findAllByText('Gearing');

            // Hydrated from storage: Shimakaze leads on battles, not the WR-desc
            // payload order (which would put Gearing first).
            await waitFor(() => expect(rowOrder()[0]).toContain('Shimakaze'));
        });
    });

    describe('drill-down top-3 medals', () => {
        // A four-player board so we can assert the medal is worn by ranks 1–3 and
        // not by rank 4 — mirroring the /ship page (ShipRouteView) podium.
        const medalBoardFixture = {
            realm: 'na',
            ship: { ship_id: 111, name: 'Shimakaze', tier: 10, ship_type: 'Destroyer', nation: 'japan', is_premium: false },
            players: [
                { rank: 1, player_name: 'GoldPlayer', win_rate: 65.3, battles: 95, avg_damage: 68227, kills_per_battle: 1.07 },
                { rank: 2, player_name: 'SilverPlayer', win_rate: 62.1, battles: 120, avg_damage: 61020, kills_per_battle: 0.98 },
                { rank: 3, player_name: 'BronzePlayer', win_rate: 60.4, battles: 210, avg_damage: 57110, kills_per_battle: 0.91 },
                { rank: 4, player_name: 'NoMedalPlayer', win_rate: 58.0, battles: 305, avg_damage: 54000, kills_per_battle: 0.83 },
            ],
        };
        const routeMedalBoard = (url: string) => {
            if (url.includes('/leaderboard')) return Promise.resolve({ data: medalBoardFixture } as never);
            return routeFetch(url);
        };

        it('renders a gold/silver/bronze medal beside the top-3 players, none for rank 4', async () => {
            mockFetch.mockImplementation((url: string) => routeMedalBoard(url));
            render(<ShipLeaderboard />);
            await clickShip('Shimakaze');
            await screen.findAllByText('GoldPlayer');

            // The medal is a TopShipIcon whose aria-label names the held rank. Names
            // render twice (desktop table + mobile cards), so each label appears 2×.
            expect(screen.getAllByLabelText(/Currently #1 Shimakaze/).length).toBeGreaterThan(0);
            expect(screen.getAllByLabelText(/Currently #2 Shimakaze/).length).toBeGreaterThan(0);
            expect(screen.getAllByLabelText(/Currently #3 Shimakaze/).length).toBeGreaterThan(0);
            // Rank 4 wears no medal.
            expect(screen.queryByLabelText(/Currently #4 Shimakaze/)).toBeNull();
        });
    });

    describe('WR-percentile filter', () => {
        // A distinct top-25% fixture so we can assert the list actually swaps to
        // the filtered numbers (Gearing's WR climbs, battle count drops).
        const top25Fixture = {
            ...listFixture,
            wr_pct: 25,
            ships: [
                { ship_id: 222, ship_name: 'Gearing', ship_type: 'Destroyer', tier: 10, nation: 'usa', is_premium: false, battles: 2700, win_rate: 71.4, avg_damage: 78900, kills_per_battle: 1.21 },
                { ship_id: 111, ship_name: 'Shimakaze', ship_type: 'Destroyer', tier: 10, nation: 'japan', is_premium: false, battles: 5100, win_rate: 64.8, avg_damage: 69800, kills_per_battle: 1.02 },
            ],
        };
        const routeWithPct = (url: string) => {
            if (url.includes('/ships?') && url.includes('wr_pct=25')) {
                return Promise.resolve({ data: top25Fixture } as never);
            }
            return routeFetch(url);
        };

        it('refetches with &wr_pct and a pct-tagged cacheKey when 25% is picked', async () => {
            mockFetch.mockImplementation((url: string) => routeWithPct(url));
            render(<ShipLeaderboard />);
            await screen.findAllByText('Gearing');

            fireEvent.click(screen.getByRole('button', { name: '25%' }));
            await waitFor(() => {
                expect(mockFetch).toHaveBeenCalledWith(
                    '/api/realm/na/ships?tier=10&type=Battleship&wr_pct=25',
                    // Percentile views bypass the client settled cache (ttlMs:0) so a
                    // `pending` stub never poisons it and polling always hits the server.
                    expect.objectContaining({ ttlMs: 0 }),
                );
            });
            // The 25% pill is pressed and the filtered numbers are shown.
            expect(screen.getByRole('button', { name: '25%' })).toHaveAttribute('aria-pressed', 'true');
            expect(screen.getAllByText('71.4%').length).toBeGreaterThan(0);
            expect(screen.getAllByText(/top 25%/i).length).toBeGreaterThan(0);
        });

        it('polls a pending bucket, shows a crunching message, then renders ships', async () => {
            let pctCalls = 0;
            mockFetch.mockImplementation((url: string) => {
                if (url.includes('/ships?') && url.includes('wr_pct=25')) {
                    pctCalls += 1;
                    // First response is the cold pending stub; the poll then lands.
                    return Promise.resolve({
                        data: pctCalls === 1
                            ? { ...listFixture, wr_pct: 25, ships: [], pending: true }
                            : top25Fixture,
                    } as never);
                }
                return routeFetch(url);
            });
            render(<ShipLeaderboard />);
            await screen.findAllByText('Gearing');

            fireEvent.click(screen.getByRole('button', { name: '25%' }));
            // Pending → the crunching message shows (not the stale all-list numbers).
            await screen.findByText(/crunching/i);
            // The poll (~3s) then resolves to the ready top-25% numbers.
            await screen.findAllByText('71.4%', undefined, { timeout: 6000 });
            expect(pctCalls).toBeGreaterThanOrEqual(2);
        });

        it('All issues no wr_pct param (default behavior) and tracks the filter', async () => {
            mockFetch.mockImplementation((url: string) => routeWithPct(url));
            render(<ShipLeaderboard />);
            await screen.findAllByText('Gearing');

            // Go to 25%, then back to All.
            fireEvent.click(screen.getByRole('button', { name: '25%' }));
            await waitFor(() =>
                expect(screen.getByRole('button', { name: '25%' })).toHaveAttribute('aria-pressed', 'true'),
            );
            fireEvent.click(screen.getByRole('button', { name: 'All' }));

            await waitFor(() => {
                expect(mockFetch).toHaveBeenCalledWith(
                    '/api/realm/na/ships?tier=10&type=Battleship',
                    expect.objectContaining({ cacheKey: 'ships-by:na:10:Battleship:all' }),
                );
            });
            expect(mockTrack).toHaveBeenCalledWith(
                'ship-leaderboard-wr-filter',
                expect.objectContaining({ realm: 'na', wr_pct: 25 }),
            );
        });

        it('hides the WR pills while a ship board is open (list-only filter)', async () => {
            render(<ShipLeaderboard />);
            await clickShip('Shimakaze');
            await screen.findAllByText('UsunU');
            // In board view the WR pills are gone…
            expect(screen.queryByRole('button', { name: '25%' })).not.toBeInTheDocument();
            // …and come back on Clear.
            fireEvent.click(screen.getByRole('button', { name: /clear/i }));
            await screen.findAllByText('Gearing');
            expect(screen.getByRole('button', { name: '25%' })).toBeInTheDocument();
        });
    });

    describe('onBucket emit (feeds the landing treemap)', () => {
        const lastBucket = (fn: jest.Mock) => fn.mock.calls.at(-1)?.[0];

        it('emits the resolved default bucket with its ships once the list lands', async () => {
            const onBucket = jest.fn();
            render(<ShipLeaderboard onBucket={onBucket} />);
            await screen.findAllByText('Gearing');
            await waitFor(() => expect(lastBucket(onBucket)?.loading).toBe(false));

            const b = lastBucket(onBucket);
            expect(b.tier).toBe(10);
            expect(b.type).toBe('Battleship');
            expect(b.wrPct).toBe(50);
            expect(b.pending).toBe(false);
            expect(b.empty).toBe(false);
            // The treemap is fed the same ships (WR-desc) the table shows.
            expect(b.ships.map((s: { ship_name: string }) => s.ship_name)).toEqual([
                'Gearing',
                'Shimakaze',
            ]);
        });

        it('re-emits with the new tier+type when the filter changes', async () => {
            const onBucket = jest.fn();
            render(<ShipLeaderboard onBucket={onBucket} />);
            await waitFor(() => expect(lastBucket(onBucket)?.loading).toBe(false));

            fireEvent.click(screen.getByRole('button', { name: 'DD' }));
            await waitFor(() => {
                const b = lastBucket(onBucket);
                expect(b.type).toBe('Destroyer');
                expect(b.loading).toBe(false);
            });
        });

        it('emits empty:true for the T9 submarine easter-egg bucket (no ships)', async () => {
            const onBucket = jest.fn();
            render(<ShipLeaderboard onBucket={onBucket} />);
            await screen.findAllByText('Gearing');

            fireEvent.click(screen.getByRole('button', { name: '9' }));
            fireEvent.click(screen.getByRole('button', { name: 'SS' }));
            await waitFor(() => {
                const b = lastBucket(onBucket);
                expect(b.tier).toBe(9);
                expect(b.type).toBe('Submarine');
                expect(b.empty).toBe(true);
                expect(b.ships).toEqual([]);
            });
        });
    });
});
