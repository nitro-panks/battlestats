import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import PlayerSearch from '../PlayerSearch';

const pushMock = jest.fn();
const capturedPlayerDetailProps: { current: null | Record<string, unknown> } = { current: null };

const buildJsonResponse = (payload: unknown) => ({
    ok: true,
    status: 200,
    headers: {
        get: (name: string) => name.toLowerCase() === 'content-type' ? 'application/json' : null,
    },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
});

const buildErrorResponse = (status: number, body = 'missing') => ({
    ok: false,
    status,
    headers: {
        get: (name: string) => name.toLowerCase() === 'content-type' ? 'text/html' : null,
    },
    text: async () => body,
});

const buildPlayerPayload = (overrides: Record<string, unknown> = {}) => ({
    id: 1,
    name: 'Player One',
    player_id: 77,
    kill_ratio: 1.5,
    actual_kdr: 1.8,
    player_score: 980,
    total_battles: 100,
    pvp_battles: 80,
    pvp_wins: 44,
    pvp_losses: 36,
    pvp_ratio: 55,
    pvp_survival_rate: 30,
    wins_survival_rate: null,
    creation_date: '2024-01-01',
    days_since_last_battle: 2,
    last_battle_date: '2026-03-01',
    recent_games: {},
    is_hidden: false,
    stats_updated_at: '2026-03-01T00:00:00Z',
    last_fetch: '2026-03-01T00:00:00Z',
    last_lookup: '2026-03-01T00:00:00Z',
    clan: 100,
    clan_name: 'Test Clan',
    clan_tag: 'TEST',
    clan_id: 100,
    verdict: null,
    ...overrides,
});

// The landing now renders only the search funnel + the treemap/ship-leaderboard
// children (each covered by its own test). The player-search behaviours that
// live in PlayerSearch are: q-param load + back, nav-search error, and the clan
// hydration poll — all keyed off the /api/player/ fetch.
const installFetchMock = ({
    playerResponses = {},
}: {
    playerResponses?: Record<string, Array<ReturnType<typeof buildJsonResponse> | ReturnType<typeof buildErrorResponse>>>;
} = {}) => {
    const responseQueues = new Map(
        Object.entries(playerResponses).map(([playerName, queue]) => [playerName, [...queue]]),
    );

    global.fetch = jest.fn((input: RequestInfo | URL) => {
        const url = input.toString();

        if (url.startsWith('/api/player/')) {
            const strippedPath = url.replace(/\?.*$/, '');
            const playerName = decodeURIComponent(strippedPath.replace('/api/player/', '').replace(/\/$/, ''));
            const queue = responseQueues.get(playerName);
            if (!queue || queue.length === 0) {
                return Promise.reject(new Error(`Unexpected player fetch: ${playerName}`));
            }

            return Promise.resolve(queue.shift() as ReturnType<typeof buildJsonResponse>);
        }

        return Promise.reject(new Error(`Unexpected fetch: ${url}`));
    }) as jest.Mock;
};

jest.mock('next/navigation', () => ({
    useRouter: () => ({
        push: pushMock,
    }),
}));

jest.mock('../PlayerDetail', () => {
    return function MockPlayerDetail(props: Record<string, unknown>) {
        capturedPlayerDetailProps.current = props;
        const player = props.player as { name?: string; clan_name?: string | null };
        return (
            <div data-testid="player-detail">
                <span>{player?.name}</span>
                <span>{player?.clan_name || 'No clan yet'}</span>
            </div>
        );
    };
});

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

describe('PlayerSearch', () => {
    let consoleErrorSpy: jest.SpyInstance;

    beforeEach(() => {
        pushMock.mockReset();
        capturedPlayerDetailProps.current = null;
        window.history.replaceState({}, '', '/');
        jest.useRealTimers();
        installFetchMock();
        consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    });

    afterEach(() => {
        consoleErrorSpy.mockRestore();
        jest.useRealTimers();
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
    });

    it('loads player detail from the q parameter and returns to landing on back', async () => {
        window.history.replaceState({}, '', '/?q=Player%20One');
        installFetchMock({
            playerResponses: {
                'Player One': [buildJsonResponse(buildPlayerPayload())],
            },
        });

        render(<PlayerSearch />);

        expect(await screen.findByTestId('player-detail')).toBeInTheDocument();
        expect(screen.getByText('Player One')).toBeInTheDocument();

        const props = capturedPlayerDetailProps.current as { onBack: () => void };
        act(() => {
            props.onBack();
        });

        await waitFor(() => {
            expect(screen.queryByTestId('player-detail')).not.toBeInTheDocument();
        });
        expect(screen.getByTestId('ship-leaderboard')).toBeInTheDocument();
    });

    it('executes nav search events and shows an error when player lookup fails', async () => {
        installFetchMock({
            playerResponses: {
                'Missing Player': [buildErrorResponse(404)],
            },
        });

        render(<PlayerSearch />);

        await screen.findByTestId('ship-leaderboard');

        act(() => {
            window.dispatchEvent(new CustomEvent('navSearch', {
                detail: { query: 'Missing Player' },
            }));
        });

        expect(await screen.findByText('Player not found')).toBeInTheDocument();
    });

    it('polls hydrated clan data until a clan name appears', async () => {
        jest.useFakeTimers();
        installFetchMock({
            playerResponses: {
                'Hydrated Player': [
                    buildJsonResponse(buildPlayerPayload({
                        name: 'Hydrated Player',
                        clan_id: 100,
                        clan_name: null,
                        clan_tag: null,
                    })),
                    buildJsonResponse(buildPlayerPayload({
                        name: 'Hydrated Player',
                        clan_id: 100,
                        clan_name: 'Hydrated Clan',
                        clan_tag: 'HYD',
                    })),
                ],
            },
        });

        render(<PlayerSearch />);

        await screen.findByTestId('ship-leaderboard');

        act(() => {
            window.dispatchEvent(new CustomEvent('navSearch', {
                detail: { query: 'Hydrated Player' },
            }));
        });

        expect(await screen.findByText('No clan yet')).toBeInTheDocument();

        await act(async () => {
            jest.advanceTimersByTime(2500);
        });

        await waitFor(() => {
            expect(screen.getByText('Hydrated Clan')).toBeInTheDocument();
        });
        expect((global.fetch as jest.Mock).mock.calls.some(
            ([url]) => url === '/api/player/Hydrated%20Player?realm=na',
        )).toBe(true);
        expect((global.fetch as jest.Mock).mock.calls.filter(([url]) => url === '/api/player/Hydrated%20Player?realm=na')).toHaveLength(2);
    });
});
