import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import PlayerRailLayout from '../PlayerRailLayout';

// The active player comes from the child route segment. We drive it from a
// module-level value + rerender() — which models the real architecture: the
// layout stays mounted (same component instance) while only the segment
// changes. The Next-routing guarantee that the parent layout is preserved
// across the [playerName] segment change was settled empirically by the spike
// (see runbook-player-rail-soft-nav-2026-06-23.md); this unit test covers the
// component contract that rides on it.
const mockSegment = { value: 'Player%20A' };
const mockPush = jest.fn();
const mockClanSvg = { mounts: 0 };
const mockUseClanMembers = jest.fn();

// player name -> clan payload. A & B share clan 100 (same-clan swap); C is in
// clan 200 (cross-clan swap).
const mockClans: Record<string, { clan_id: number; clan_name: string; clan_tag: string; player_id: number }> = {
    'Player A': { clan_id: 100, clan_name: 'Alpha', clan_tag: 'AL', player_id: 1 },
    'Player B': { clan_id: 100, clan_name: 'Alpha', clan_tag: 'AL', player_id: 2 },
    'Player C': { clan_id: 200, clan_name: 'Bravo', clan_tag: 'BR', player_id: 3 },
};

jest.mock('next/navigation', () => ({
    useRouter: () => ({ push: mockPush }),
    useSelectedLayoutSegment: () => mockSegment.value,
}));

jest.mock('../../context/RealmContext', () => ({
    useRealm: () => ({ realm: 'na', setRealm: jest.fn() }),
}));

jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ theme: 'light', setTheme: jest.fn() }),
}));

jest.mock('../useClanMembers', () => ({
    useClanMembers: (...args: unknown[]) => mockUseClanMembers(...args),
}));

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn((url: string) => {
        const path = String(url).split('?')[0];
        const name = decodeURIComponent(path.replace('/api/player/', '').replace(/\/$/, ''));
        const clan = mockClans[name];
        if (!clan) {
            return Promise.reject(new Error(`Unexpected player fetch: ${name}`));
        }
        return Promise.resolve({ data: { ...clan, name }, headers: {} });
    }),
    isAbortError: () => false,
}));

// ClanSVG records a module-scoped mount count so we can assert the rail never
// remounts across a swap; it surfaces clanId + the marker as data-attributes.
jest.mock('../ClanSVG', () => ({
    __esModule: true,
    default: function MockClanSvg(props: { clanId: number; highlightedPlayerName?: string }) {
        const ReactLocal = require('react');
        ReactLocal.useEffect(() => {
            mockClanSvg.mounts += 1;
        }, []);
        return (
            <div
                data-testid="clan-svg"
                data-clan-id={String(props.clanId)}
                data-highlight={props.highlightedPlayerName ?? ''}
            />
        );
    },
}));

jest.mock('../DeferredSection', () => {
    return function MockDeferredSection({ children }: { children: React.ReactNode }) {
        return <>{children}</>;
    };
});

// ClanMembers is code-split via next/dynamic; mock the loader to a component
// that surfaces the marker prop and a clickable member row.
jest.mock('next/dynamic', () => () => function MockClanMembers(props: {
    highlightedPlayerName?: string;
    onSelectMember?: (name: string) => void;
}) {
    return (
        <div data-testid="clan-members" data-highlight={props.highlightedPlayerName ?? ''}>
            <button type="button" onClick={() => props.onSelectMember?.('Player B')}>
                pick Player B
            </button>
        </div>
    );
});

describe('PlayerRailLayout', () => {
    let consoleErrorSpy: jest.SpyInstance;

    beforeEach(() => {
        mockSegment.value = 'Player%20A';
        mockPush.mockReset();
        mockClanSvg.mounts = 0;
        mockUseClanMembers.mockReset();
        mockUseClanMembers.mockReturnValue({ members: [], loading: false, error: '' });
        consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    });

    afterEach(() => {
        consoleErrorSpy.mockRestore();
    });

    const renderRail = () =>
        render(
            <PlayerRailLayout>
                <div data-testid="well">well</div>
            </PlayerRailLayout>,
        );

    it('does not remount the rail on a same-clan member swap; only the marker moves', async () => {
        const { rerender } = renderRail();

        // Initial player resolves to clan 100, rail mounts once.
        const initial = await screen.findByTestId('clan-svg');
        expect(initial).toHaveAttribute('data-clan-id', '100');
        expect(initial).toHaveAttribute('data-highlight', 'Player A');
        expect(mockClanSvg.mounts).toBe(1);

        // Click another member of the SAME clan: segment changes, layout stays.
        mockSegment.value = 'Player%20B';
        rerender(
            <PlayerRailLayout>
                <div data-testid="well">well</div>
            </PlayerRailLayout>,
        );

        // Marker follows the segment synchronously...
        await waitFor(() => {
            expect(screen.getByTestId('clan-svg')).toHaveAttribute('data-highlight', 'Player B');
        });
        // ...clan is unchanged and the rail never remounted.
        expect(screen.getByTestId('clan-svg')).toHaveAttribute('data-clan-id', '100');
        expect(screen.getByTestId('clan-members')).toHaveAttribute('data-highlight', 'Player B');
        expect(mockClanSvg.mounts).toBe(1);
    });

    it('redraws the rail (new clanId) on a cross-clan swap without remounting', async () => {
        const { rerender } = renderRail();

        await waitFor(() => {
            expect(screen.getByTestId('clan-svg')).toHaveAttribute('data-clan-id', '100');
        });

        // Swap to a player in a DIFFERENT clan.
        mockSegment.value = 'Player%20C';
        rerender(
            <PlayerRailLayout>
                <div data-testid="well">well</div>
            </PlayerRailLayout>,
        );

        await waitFor(() => {
            expect(screen.getByTestId('clan-svg')).toHaveAttribute('data-clan-id', '200');
        });
        expect(screen.getByTestId('clan-svg')).toHaveAttribute('data-highlight', 'Player C');
        // The rail re-rendered with a new clanId but did not remount.
        expect(mockClanSvg.mounts).toBe(1);
        // The clanId-keyed roster hook saw the new clan.
        expect(mockUseClanMembers).toHaveBeenCalledWith(200);
    });

    it('renders the well (children) alongside the rail', async () => {
        renderRail();
        expect(await screen.findByTestId('well')).toHaveTextContent('well');
    });

    it('navigates to the canonical player route on a member click', async () => {
        renderRail();
        await screen.findByTestId('clan-svg');
        expect(mockPush).not.toHaveBeenCalled();

        screen.getByRole('button', { name: 'pick Player B' }).click();

        expect(mockPush).toHaveBeenCalledWith('/player/Player%20B?realm=na');
    });
});
