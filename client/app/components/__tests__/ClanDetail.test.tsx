import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import ClanDetail from '../ClanDetail';

const mockUseClanMembers = jest.fn();
const mockUseClanMemberTiers = jest.fn();
const mockClipboardWriteText = jest.fn();
const trackEventMock = jest.fn();
const onSelectMemberSpy = jest.fn();

jest.mock('next/dynamic', () => {
    return () => function MockDynamicComponent(props: {
        clanId?: number;
        memberCount?: number;
        members?: unknown[];
        svgWidth?: number;
        onSelectMember?: (memberName: string) => void;
    }) {
        if (typeof props.memberCount === 'number') {
            return <div data-testid="clan-battle-seasons" />;
        }

        if (typeof props.svgWidth === 'number') {
            // The clan chart — expose a member-select control so the test can
            // assert the dot-click → onSelectMember wiring.
            return (
                <div data-testid="clan-svg">
                    <button type="button" onClick={() => props.onSelectMember?.('DeckBoss')}>Select member</button>
                </div>
            );
        }

        return null;
    };
});

jest.mock('../DeferredSection', () => {
    return function MockDeferredSection({ children }: { children: React.ReactNode }) {
        return <>{children}</>;
    };
});

jest.mock('../useClanMembers', () => ({
    useClanMembers: (...args: unknown[]) => mockUseClanMembers(...args),
}));

jest.mock('../useClanMemberTiers', () => ({
    useClanMemberTiers: (...args: unknown[]) => mockUseClanMemberTiers(...args),
}));

jest.mock('../../lib/umami', () => ({
    trackEvent: (...args: unknown[]) => trackEventMock(...args),
}));

describe('ClanDetail clan roster hydration wiring', () => {
    let consoleErrorSpy: jest.SpyInstance;

    beforeEach(() => {
        mockUseClanMembers.mockReturnValue({ members: [], loading: false, error: '' });
        mockUseClanMemberTiers.mockReturnValue({ data: [], loading: false });
        mockClipboardWriteText.mockReset();
        trackEventMock.mockReset();
        onSelectMemberSpy.mockReset();
        Object.defineProperty(navigator, 'clipboard', {
            configurable: true,
            value: {
                writeText: mockClipboardWriteText,
            },
        });
        consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => undefined);
        jest.useRealTimers();
    });

    afterEach(() => {
        mockUseClanMembers.mockClear();
        consoleErrorSpy.mockRestore();
        jest.useRealTimers();
    });

    it('loads clan members through the shared hook using the clan id', () => {
        render(
            <ClanDetail
                clan={{
                    clan_id: 5555,
                    name: 'Fixture Clan',
                    tag: 'FX',
                    members_count: 12,
                }}
                onSelectMember={() => undefined}
            />,
        );

        expect(mockUseClanMembers).toHaveBeenCalledWith(5555);
    });

    it('keeps the icon+label phase headers on the clan-page roster (headers mode)', () => {
        // The player page's clan section drops these headers (icon-lead mode);
        // the clan page must keep them — guard the default.
        mockUseClanMembers.mockReturnValue({
            members: [{
                name: 'Alpha',
                is_hidden: false,
                pvp_ratio: 52,
                days_since_last_battle: 3,
                is_leader: false,
                is_pve_player: false,
                is_sleepy_player: false,
                is_ranked_player: false,
                is_clan_battle_player: false,
                clan_battle_win_rate: null,
                highest_ranked_league: null,
                ranked_hydration_pending: false,
                ranked_updated_at: null,
                activity_bucket: 'active_7d',
            }],
            loading: false,
            error: '',
        });
        render(
            <ClanDetail
                clan={{
                    clan_id: 5555,
                    name: 'Fixture Clan',
                    tag: 'FX',
                    members_count: 12,
                }}
                onSelectMember={onSelectMemberSpy}
            />,
        );

        expect(screen.getByTestId('clan-phase-active_7d')).toHaveTextContent('Active now (1)');
    });

    it('renders the clan members list before the clan battle seasons section', () => {
        render(
            <ClanDetail
                clan={{
                    clan_id: 5555,
                    name: 'Fixture Clan',
                    tag: 'FX',
                    members_count: 12,
                }}
                onSelectMember={onSelectMemberSpy}
            />,
        );

        const clanMembers = screen.getByTestId('clan-activity-roster');
        const clanBattleSeasons = screen.getByTestId('clan-battle-seasons');

        expect(clanMembers.compareDocumentPosition(clanBattleSeasons) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it('renders the clan heading and member count', () => {
        render(
            <ClanDetail
                clan={{
                    clan_id: 5555,
                    name: 'Fixture Clan',
                    tag: 'FX',
                    members_count: 12,
                }}
                onSelectMember={() => undefined}
            />,
        );

        expect(screen.getByText('[FX] Fixture Clan')).toBeInTheDocument();
        expect(screen.getByText('12 members')).toBeInTheDocument();
    });

    it('wires member selection controls', () => {
        render(
            <ClanDetail
                clan={{
                    clan_id: 5555,
                    name: 'Fixture Clan',
                    tag: 'FX',
                    members_count: 12,
                }}
                onSelectMember={onSelectMemberSpy}
            />,
        );

        fireEvent.click(screen.getByRole('button', { name: 'Select member' }));

        expect(onSelectMemberSpy).toHaveBeenCalledWith('DeckBoss');
    });

    it('fires name-encoded clan-chart-3d / clan-chart-2d events for the dimension toggle', () => {
        // >=50% KDR coverage makes the 3D toggle available.
        mockUseClanMemberTiers.mockReturnValue({
            data: [{ kdr: 1.2 }, { kdr: 0.9 }],
            loading: false,
        });

        render(
            <ClanDetail
                clan={{ clan_id: 5555, name: 'Fixture Clan', tag: 'FX', members_count: 12 }}
                onSelectMember={() => undefined}
            />,
        );

        // Default is 2D, so switching to 3D fires clan-chart-3d (not clan-chart-2d).
        fireEvent.click(screen.getByRole('button', { name: '3D' }));
        expect(trackEventMock).toHaveBeenCalledWith('clan-chart-3d', expect.objectContaining({ realm: expect.any(String) }));

        // Switching back fires clan-chart-2d — the two states are distinct event names.
        fireEvent.click(screen.getByRole('button', { name: '2D' }));
        expect(trackEventMock).toHaveBeenCalledWith('clan-chart-2d', expect.objectContaining({ realm: expect.any(String) }));
    });
});