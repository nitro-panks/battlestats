import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ClanDetail from '../ClanDetail';

const mockUseClanMembers = jest.fn();
const mockClipboardWriteText = jest.fn();
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

        if (Array.isArray(props.members)) {
            return (
                <div data-testid="clan-members">
                    <button type="button" onClick={() => props.onSelectMember?.('DeckBoss')}>Select member</button>
                </div>
            );
        }

        if (typeof props.svgWidth === 'number') {
            return <div data-testid="clan-svg" />;
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

describe('ClanDetail clan roster hydration wiring', () => {
    let consoleErrorSpy: jest.SpyInstance;

    beforeEach(() => {
        mockUseClanMembers.mockReturnValue({ members: [], loading: false, error: '' });
        mockClipboardWriteText.mockReset();
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
                onBack={() => undefined}
                onSelectMember={() => undefined}
            />,
        );

        expect(mockUseClanMembers).toHaveBeenCalledWith(5555);
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
                onBack={() => undefined}
                onSelectMember={onSelectMemberSpy}
            />,
        );

        const clanMembers = screen.getByTestId('clan-members');
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
                onBack={() => undefined}
                onSelectMember={() => undefined}
            />,
        );

        expect(screen.getByText('[FX] Fixture Clan')).toBeInTheDocument();
        expect(screen.getByText('12 members')).toBeInTheDocument();
    });

    it('wires member selection and back navigation controls', () => {
        const onBack = jest.fn();

        render(
            <ClanDetail
                clan={{
                    clan_id: 5555,
                    name: 'Fixture Clan',
                    tag: 'FX',
                    members_count: 12,
                }}
                onBack={onBack}
                onSelectMember={onSelectMemberSpy}
            />,
        );

        fireEvent.click(screen.getByRole('button', { name: 'Select member' }));
        fireEvent.click(screen.getByRole('button', { name: 'Back' }));

        expect(onSelectMemberSpy).toHaveBeenCalledWith('DeckBoss');
        expect(onBack).toHaveBeenCalled();
    });

    it('copies the clan URL and clears the copied state after the timeout', async () => {
        jest.useFakeTimers();
        mockClipboardWriteText.mockResolvedValue(undefined);

        render(
            <ClanDetail
                clan={{
                    clan_id: 5555,
                    name: 'Fixture Clan',
                    tag: 'FX',
                    members_count: 12,
                }}
                onBack={() => undefined}
                onSelectMember={() => undefined}
            />,
        );

        fireEvent.click(screen.getByRole('button', { name: 'Copy shareable clan URL' }));

        await waitFor(() => {
            expect(mockClipboardWriteText).toHaveBeenCalled();
        });
        expect(await screen.findByText('Copied')).toBeInTheDocument();

        await act(async () => {
            jest.advanceTimersByTime(1800);
        });

        await waitFor(() => {
            expect(screen.queryByText('Copied')).not.toBeInTheDocument();
        });
    });

    it('shows a share failure state when clipboard copying fails', async () => {
        mockClipboardWriteText.mockRejectedValue(new Error('no clipboard'));

        render(
            <ClanDetail
                clan={{
                    clan_id: 5555,
                    name: 'Fixture Clan',
                    tag: 'FX',
                    members_count: 12,
                }}
                onBack={() => undefined}
                onSelectMember={() => undefined}
            />,
        );

        fireEvent.click(screen.getByRole('button', { name: 'Copy shareable clan URL' }));

        expect(await screen.findByText('Copy failed')).toBeInTheDocument();
        expect(consoleErrorSpy).toHaveBeenCalled();
    });
});