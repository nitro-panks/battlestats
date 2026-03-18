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
            expect(global.fetch).toHaveBeenCalledWith('http://localhost:8888/api/player/Player%20One/');
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
            onSelectClan: (id: number, name: string) => void;
        };

        props.onBack();
        props.onSelectMember('Other Player');
        props.onSelectClan(1000067803, 'The Best Clan');

        expect(pushMock).toHaveBeenNthCalledWith(1, '/');
        expect(pushMock).toHaveBeenNthCalledWith(2, '/player/Other%20Player');
        expect(pushMock).toHaveBeenNthCalledWith(3, '/clan/1000067803-the-best-clan');
    });

    it('shows a not found state when the player API request fails', async () => {
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
    });
});