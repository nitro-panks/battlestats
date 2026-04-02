import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import ClanRouteView from '../ClanRouteView';

const pushMock = jest.fn();
const trackEntityDetailViewMock = jest.fn();
const capturedProps: { current: null | Record<string, unknown> } = { current: null };

jest.mock('next/navigation', () => ({
    useRouter: () => ({
        push: pushMock,
    }),
}));

jest.mock('../../lib/visitAnalytics', () => ({
    trackEntityDetailView: (...args: unknown[]) => trackEntityDetailViewMock(...args),
}));

jest.mock('../ClanDetail', () => {
    return function MockClanDetail(props: { clan: { clan_id: number; name: string; tag: string; members_count: number } }) {
        capturedProps.current = props;
        const { clan } = props;
        return (
            <div data-testid="clan-detail">
                <span>{clan.clan_id}</span>
                <span>{clan.name}</span>
                <span>{clan.tag}</span>
                <span>{clan.members_count}</span>
            </div>
        );
    };
});

describe('ClanRouteView', () => {
    let consoleErrorSpy: jest.SpyInstance;

    beforeEach(() => {
        pushMock.mockReset();
        trackEntityDetailViewMock.mockReset();
        capturedProps.current = null;
        global.fetch = jest.fn();
        consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    });

    afterEach(() => {
        consoleErrorSpy.mockRestore();
    });

    it('loads clan details from the singular clan API route and wires callbacks', async () => {
        (global.fetch as jest.Mock).mockResolvedValue({
            ok: true,
            headers: {
                get: (headerName: string) => headerName === 'content-type' ? 'application/json' : null,
            },
            json: async () => ({
                clan_id: 1000067803,
                name: 'Test Clan',
                tag: 'TEST',
                members_count: 42,
            }),
        });

        render(<ClanRouteView clanSlug="1000067803-test-clan" />);

        await waitFor(() => {
            expect(global.fetch).toHaveBeenCalledWith('/api/clan/1000067803?realm=na');
        });

        expect(await screen.findByTestId('clan-detail')).toBeInTheDocument();
        expect(screen.getByText('Test Clan')).toBeInTheDocument();
        expect(screen.getByText('TEST')).toBeInTheDocument();
        expect(screen.getByText('42')).toBeInTheDocument();
        expect(trackEntityDetailViewMock).toHaveBeenCalledWith({
            entityType: 'clan',
            entityId: 1000067803,
            entityName: 'Test Clan',
            entitySlug: '1000067803-test-clan',
        });

        const props = capturedProps.current as {
            onBack: () => void;
            onSelectMember: (memberName: string) => void;
        };

        props.onBack();
        props.onSelectMember('Player One');

        expect(pushMock).toHaveBeenNthCalledWith(1, '/');
        expect(pushMock).toHaveBeenNthCalledWith(2, '/player/Player%20One?realm=na');
    });

    it('normalizes sparse clan payloads using route fallbacks', async () => {
        (global.fetch as jest.Mock).mockResolvedValue({
            ok: true,
            headers: {
                get: (headerName: string) => headerName === 'content-type' ? 'application/json' : null,
            },
            json: async () => ({
                clan_id: 1000067803,
            }),
        });

        render(<ClanRouteView clanSlug="1000067803-test-clan" />);

        expect(await screen.findByTestId('clan-detail')).toBeInTheDocument();
        expect(screen.getByText('Clan')).toBeInTheDocument();
        expect(screen.getByText('0')).toBeInTheDocument();
    });

    it('shows a not found state for an invalid clan slug without fetching', async () => {
        render(<ClanRouteView clanSlug="not-a-clan" />);

        expect(await screen.findByText('Clan not found.')).toBeInTheDocument();
        expect(global.fetch).not.toHaveBeenCalled();
        expect(trackEntityDetailViewMock).not.toHaveBeenCalled();
    });

    it('shows a not found state when the clan payload cannot be normalized', async () => {
        (global.fetch as jest.Mock).mockResolvedValue({
            ok: true,
            headers: {
                get: (headerName: string) => headerName === 'content-type' ? 'application/json' : null,
            },
            json: async () => ({
                clan_id: 'not-a-number',
            }),
        });

        render(<ClanRouteView clanSlug="1000067803-test-clan" />);

        expect(await screen.findByText('Clan not found.')).toBeInTheDocument();
        expect(trackEntityDetailViewMock).not.toHaveBeenCalled();
    });
});