import React from 'react';
import { render, screen, within } from '@testing-library/react';
import PlayerClanSection from '../PlayerClanSection';
import type { ClanMemberData, ActivityBucketKey } from '../clanMembersShared';

const mockUseClanMembers = jest.fn();
const mockRouterPush = jest.fn();
const mockClanSvg = jest.fn();
const mockTrackEvent = jest.fn();

jest.mock('next/navigation', () => ({
    useRouter: () => ({
        push: mockRouterPush,
    }),
}));

jest.mock('../useClanMembers', () => ({
    useClanMembers: (...args: unknown[]) => mockUseClanMembers(...args),
}));

jest.mock('../DeferredSection', () => {
    return function MockDeferredSection({ children }: { children: React.ReactNode }) {
        return <>{children}</>;
    };
});

jest.mock('next/dynamic', () => {
    return () => function MockClanSvg(props: Record<string, unknown>) {
        mockClanSvg(props);
        return <div data-testid="clan-chart" />;
    };
});

jest.mock('../../lib/umami', () => ({
    trackEvent: (...args: unknown[]) => mockTrackEvent(...args),
}));

const makeMember = (name: string, activityBucket: ActivityBucketKey): ClanMemberData => ({
    name,
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
    activity_bucket: activityBucket,
});

const renderSection = (members: ClanMemberData[]) => {
    mockUseClanMembers.mockReturnValue({ members, loading: false, error: '' });
    return render(
        <PlayerClanSection
            clanId={4444}
            clanName="Fixture Clan"
            clanTag="FIX"
            playerId={101}
            playerName="Current Player"
        />,
    );
};

describe('PlayerClanSection', () => {
    beforeEach(() => {
        mockUseClanMembers.mockReset();
        mockRouterPush.mockClear();
        mockClanSvg.mockClear();
        mockTrackEvent.mockClear();
    });

    it('links the clan heading to the clan page', () => {
        renderSection([makeMember('Current Player', 'active_7d')]);

        const clanLink = screen.getByRole('link', { name: /open clan page for fixture clan/i });
        expect(clanLink).toHaveTextContent('[FIX] Fixture Clan');
        expect(clanLink.getAttribute('href')).toContain('/clan/4444');
    });

    it('groups member names into collapsed activity paragraphs', () => {
        renderSection([
            makeMember('Alpha', 'active_7d'),
            makeMember('Bravo', 'active_30d'),
            makeMember('Charlie', 'cooling_90d'),
            makeMember('Delta', 'dormant_180d'),
            makeMember('Echo', 'inactive_180d_plus'),
        ]);

        const active = screen.getByTestId('clan-phase-active_7d');
        expect(active).toHaveTextContent('Active now (2)');
        expect(within(active).getByText('Alpha')).toBeInTheDocument();
        expect(within(active).getByText('Bravo')).toBeInTheDocument();

        const cooling = screen.getByTestId('clan-phase-cooling_90d');
        expect(cooling).toHaveTextContent('Cooling (2)');
        expect(within(cooling).getByText('Charlie')).toBeInTheDocument();
        expect(within(cooling).getByText('Delta')).toBeInTheDocument();

        const goneDark = screen.getByTestId('clan-phase-inactive_180d_plus');
        expect(goneDark).toHaveTextContent('Gone dark (1)');
        expect(within(goneDark).getByText('Echo')).toBeInTheDocument();

        // No unknown-recency members: the fourth paragraph is omitted.
        expect(screen.queryByTestId('clan-phase-unknown')).not.toBeInTheDocument();
    });

    it('renders clanmates as player links but the viewed player as plain text', () => {
        renderSection([
            makeMember('Alpha', 'active_7d'),
            makeMember('Current Player', 'active_7d'),
        ]);

        const active = screen.getByTestId('clan-phase-active_7d');
        const alphaLink = within(active).getByRole('link', { name: 'Alpha' });
        expect(alphaLink.getAttribute('href')).toContain('/player/Alpha');
        expect(within(active).getByText('Current Player')).toBeInTheDocument();
        expect(within(active).queryByRole('link', { name: 'Current Player' })).not.toBeInTheDocument();
    });

    it('feeds the roster to the clan chart and highlights the viewed player', () => {
        const members = [makeMember('Alpha', 'active_7d')];
        renderSection(members);

        expect(screen.getByTestId('clan-chart')).toBeInTheDocument();
        expect(mockClanSvg).toHaveBeenCalledWith(
            expect.objectContaining({
                clanId: 4444,
                membersData: members,
                highlightedPlayerName: 'Current Player',
            }),
        );
    });
});
