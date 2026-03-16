import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import ClanRouteView from '../ClanRouteView';

const pushMock = jest.fn();

jest.mock('next/navigation', () => ({
    useRouter: () => ({
        push: pushMock,
    }),
}));

jest.mock('../ClanDetail', () => {
    return function MockClanDetail({ clan }: { clan: { clan_id: number; name: string; tag: string; members_count: number } }) {
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
    beforeEach(() => {
        pushMock.mockReset();
        global.fetch = jest.fn();
    });

    it('loads clan details from the singular clan API route', async () => {
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
            expect(global.fetch).toHaveBeenCalledWith('http://localhost:8888/api/clan/1000067803/');
        });

        expect(await screen.findByTestId('clan-detail')).toBeInTheDocument();
        expect(screen.getByText('Test Clan')).toBeInTheDocument();
        expect(screen.getByText('TEST')).toBeInTheDocument();
        expect(screen.getByText('42')).toBeInTheDocument();
    });

    it('shows a not found state for an invalid clan slug without fetching', async () => {
        render(<ClanRouteView clanSlug="not-a-clan" />);

        expect(await screen.findByText('Clan not found.')).toBeInTheDocument();
        expect(global.fetch).not.toHaveBeenCalled();
    });
});