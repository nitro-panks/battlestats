import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import PlayerRouteView from '../PlayerRouteView';

const pushMock = jest.fn();
const capturedProps: { current: null | Record<string, unknown> } = { current: null };
const trackEntityDetailViewMock = jest.fn();

jest.mock('next/navigation', () => ({
    useRouter: () => ({
        push: pushMock,
    }),
}));

jest.mock('../../lib/visitAnalytics', () => ({
    trackEntityDetailView: (...args: unknown[]) => trackEntityDetailViewMock(...args),
}));

jest.mock('../PlayerDetail', () => {
    return function MockPlayerDetail(props: Record<string, unknown>) {
        capturedProps.current = props;
        const player = props.player as { name: string; clan_name: string; player_id: number };
        return (
            <div data-testid="player-detail">
                <span>{player.name}</span>
                <span>{player.clan_name}</span>
                <span>{player.player_id}</span>
            </div>
        );
    };
});

// Count only the profile-load fetches (`/api/player/<name>`), not the parallel
// battle-history prefetch PlayerRouteView also fires — so retry-count assertions
// stay scoped to the load under test.
const profileFetchCount = (): number =>
    (global.fetch as jest.Mock).mock.calls.filter(
        ([url]) => typeof url === 'string'
            && url.startsWith('/api/player/')
            && !url.includes('/battle-history'),
    ).length;

describe('PlayerRouteView', () => {
    let consoleErrorSpy: jest.SpyInstance;

    beforeEach(() => {
        pushMock.mockReset();
        capturedProps.current = null;
        trackEntityDetailViewMock.mockReset();
        global.fetch = jest.fn();
        consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    });

    afterEach(() => {
        consoleErrorSpy.mockRestore();
    });

    it('loads player details from the routed player API and wires navigation callbacks', async () => {
        (global.fetch as jest.Mock).mockResolvedValue({
            ok: true,
            headers: {
                get: (headerName: string) => headerName === 'content-type' ? 'application/json' : null,
            },
            json: async () => ({
                id: 1,
                name: 'Player One',
                player_id: 77,
                kill_ratio: null,
                actual_kdr: null,
                player_score: null,
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
            }),
        });

        render(<PlayerRouteView playerName="Player One" />);

        await waitFor(() => {
            expect(global.fetch).toHaveBeenCalledWith('/api/player/Player%20One?realm=na', undefined);
        });

        expect(await screen.findByTestId('player-detail')).toBeInTheDocument();
        expect(screen.getByText('Player One')).toBeInTheDocument();
        expect(screen.getByText('Test Clan')).toBeInTheDocument();
        expect(trackEntityDetailViewMock).toHaveBeenCalledWith({
            entityType: 'player',
            entityId: 77,
            entityName: 'Player One',
            entitySlug: 'Player One',
        });

        const props = capturedProps.current as {
            onBack: () => void;
            onSelectMember: (name: string) => void;
        };

        props.onBack();
        props.onSelectMember('Other Player');

        expect(pushMock).toHaveBeenNthCalledWith(1, '/');
        expect(pushMock).toHaveBeenNthCalledWith(2, '/player/Other%20Player?realm=na');
    });

    it('shows a not found state on a 404, with NO retry', async () => {
        (global.fetch as jest.Mock).mockResolvedValue({
            ok: false,
            status: 404,
            headers: {
                get: () => 'text/html',
            },
            text: async () => '<html>missing</html>',
        });

        render(<PlayerRouteView playerName="Missing Player" />);

        expect(await screen.findByText('Player not found.')).toBeInTheDocument();
        expect(trackEntityDetailViewMock).not.toHaveBeenCalled();
        // 404 is terminal — the profile load is fetched exactly once (no retry).
        expect(profileFetchCount()).toBe(1);
    });

    it('retries a transient 5xx then renders the player', async () => {
        const okResponse = () => ({
            ok: true,
            status: 200,
            headers: {
                get: (headerName: string) => headerName === 'content-type' ? 'application/json' : null,
            },
            json: async () => ({
                id: 2,
                name: 'Flaky Player',
                player_id: 99,
                clan_name: 'Resilient Clan',
                clan_tag: 'RES',
                clan_id: 200,
            }),
            text: async () => '',
        });
        const serverError = () => ({ ok: false, status: 502, headers: { get: () => 'text/html' }, text: async () => 'bad gateway' });

        // Route by URL: the profile load fails ONCE then succeeds; the parallel
        // battle-history prefetch is irrelevant here (return a benign error).
        let profileCalls = 0;
        (global.fetch as jest.Mock).mockImplementation((url: string) => {
            if (typeof url === 'string' && url.startsWith('/api/player/') && !url.includes('/battle-history')) {
                profileCalls += 1;
                return Promise.resolve(profileCalls === 1 ? serverError() : okResponse());
            }
            return Promise.resolve(serverError());
        });

        render(<PlayerRouteView playerName="Flaky Player" />);

        expect(await screen.findByTestId('player-detail', {}, { timeout: 3000 })).toBeInTheDocument();
        expect(screen.getByText('Flaky Player')).toBeInTheDocument();
        // 1 failed + 1 successful retry.
        expect(profileFetchCount()).toBe(2);
    });

    it('shows a temporarily-unavailable state (NOT "not found") when 5xx retries exhaust', async () => {
        (global.fetch as jest.Mock).mockResolvedValue({
            ok: false,
            status: 503,
            headers: { get: () => 'text/html' },
            text: async () => '<html>down</html>',
        });

        render(<PlayerRouteView playerName="Stalled Player" />);

        expect(
            await screen.findByText(/temporarily unavailable/i, {}, { timeout: 3000 }),
        ).toBeInTheDocument();
        expect(screen.queryByText('Player not found.')).not.toBeInTheDocument();
        // 1 initial + 2 retries on the profile load.
        expect(profileFetchCount()).toBe(3);
        expect(trackEntityDetailViewMock).not.toHaveBeenCalled();
    });
});