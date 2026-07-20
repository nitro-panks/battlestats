import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import PlayerRouteView from '../PlayerRouteView';

// Spies for the realm context so we can assert the auto-switch without a real
// provider. `realm` is fixed at 'na' (matches the default context the other
// PlayerRouteView tests rely on); the header drives the switch.
const setRealmMock = jest.fn();
const notifyRealmAutoSwitchMock = jest.fn();
const trackEventMock = jest.fn();

jest.mock('../../context/RealmContext', () => ({
    useRealm: () => ({
        realm: 'na',
        setRealm: setRealmMock,
        notifyRealmAutoSwitch: notifyRealmAutoSwitchMock,
        autoSwitchSignal: 0,
    }),
}));

jest.mock('next/navigation', () => ({
    useRouter: () => ({ push: jest.fn() }),
}));

jest.mock('../../lib/visitAnalytics', () => ({
    trackEntityDetailView: jest.fn(),
}));

jest.mock('../../lib/umami', () => ({
    trackEvent: (...args: unknown[]) => trackEventMock(...args),
}));

jest.mock('../PlayerDetail', () => {
    return function MockPlayerDetail(props: Record<string, unknown>) {
        const player = props.player as { name: string; player_id: number };
        return <div data-testid="player-detail">{player.name}</div>;
    };
});

const asiaPlayerResponse = () => ({
    ok: true,
    status: 200,
    headers: {
        get: (headerName: string) => {
            if (headerName === 'content-type') return 'application/json';
            if (headerName === 'X-Resolved-Realm') return 'asia';
            return null;
        },
    },
    json: async () => ({
        id: 1,
        name: 'Ayanami_332',
        player_id: 888,
        kill_ratio: null,
        actual_kdr: null,
        pvp_battles: 80,
        pvp_ratio: 55,
        is_hidden: false,
        clan_name: 'Asia Clan',
        clan_tag: 'ASIA',
        clan_id: 200,
    }),
});

describe('PlayerRouteView cross-realm fallback', () => {
    let consoleErrorSpy: jest.SpyInstance;

    beforeEach(() => {
        setRealmMock.mockReset();
        notifyRealmAutoSwitchMock.mockReset();
        trackEventMock.mockReset();
        global.fetch = jest.fn().mockResolvedValue(asiaPlayerResponse());
        window.history.replaceState({}, '', 'http://localhost/player/Ayanami_332?realm=na');
        consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    });

    afterEach(() => {
        consoleErrorSpy.mockRestore();
    });

    it('switches realm, flashes, tracks, and rewrites the URL when the player resolves elsewhere', async () => {
        render(<PlayerRouteView playerName="Ayanami_332" />);

        expect(await screen.findByTestId('player-detail')).toBeInTheDocument();

        await waitFor(() => expect(setRealmMock).toHaveBeenCalledWith('asia'));
        expect(notifyRealmAutoSwitchMock).toHaveBeenCalledTimes(1);
        expect(trackEventMock).toHaveBeenCalledWith('realm-fallback', { from: 'na', to: 'asia' });

        // ?realm= rewritten so a reload/share lands directly on ASIA.
        expect(new URL(window.location.href).searchParams.get('realm')).toBe('asia');

        // The bring-along notice is shown.
        expect(screen.getByRole('status')).toHaveTextContent(/isn't on NA — showing ASIA/i);
    });

    it('does NOT switch realm when the resolved realm matches the requested one', async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ...asiaPlayerResponse(),
            headers: {
                get: (headerName: string) => {
                    if (headerName === 'content-type') return 'application/json';
                    if (headerName === 'X-Resolved-Realm') return 'na';
                    return null;
                },
            },
        });

        render(<PlayerRouteView playerName="Ayanami_332" />);

        expect(await screen.findByTestId('player-detail')).toBeInTheDocument();
        expect(setRealmMock).not.toHaveBeenCalled();
        expect(screen.queryByRole('status')).not.toBeInTheDocument();
    });
});
