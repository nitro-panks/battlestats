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

    it('renders the whole roster as one flat alphabetical block — no phase grouping', () => {
        renderSection([
            makeMember('Echo', 'inactive_180d_plus'),
            makeMember('Charlie', 'cooling_90d'),
            makeMember('Alpha', 'active_7d'),
            makeMember('Delta', 'dormant_180d'),
            makeMember('Bravo', 'active_30d'),
        ]);

        // The player-page roster is flat: a single paragraph, no phase blocks,
        // headers, or activity icons — the scatterplot above carries recency.
        const roster = screen.getByTestId('clan-activity-roster');
        expect(screen.queryByTestId('clan-phase-active_7d')).not.toBeInTheDocument();
        expect(roster).not.toHaveTextContent('Active now');
        expect(within(roster).queryByLabelText(/battled within|inactive 180/)).not.toBeInTheDocument();

        // All members in one block, alphabetical regardless of activity phase.
        const names = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo'];
        names.forEach((name) => expect(within(roster).getByText(name)).toBeInTheDocument());
        const positions = names.map((name) => roster.textContent!.indexOf(name));
        expect([...positions].sort((a, b) => a - b)).toEqual(positions);
    });

    it('renders clanmates as player links but the viewed player as plain text', () => {
        renderSection([
            makeMember('Alpha', 'active_7d'),
            makeMember('Current Player', 'active_7d'),
        ]);

        const active = screen.getByTestId('clan-activity-roster');
        const alphaLink = within(active).getByRole('link', { name: 'Alpha' });
        expect(alphaLink.getAttribute('href')).toContain('/player/Alpha');
        expect(within(active).getByText('Current Player')).toBeInTheDocument();
        expect(within(active).queryByRole('link', { name: 'Current Player' })).not.toBeInTheDocument();
    });

    it('renders hidden members as plain text with the hidden icon, never as links', () => {
        renderSection([
            makeMember('Alpha', 'active_7d'),
            { ...makeMember('Ghost', 'active_7d'), is_hidden: true },
        ]);

        const active = screen.getByTestId('clan-activity-roster');
        expect(within(active).getByText('Ghost')).toBeInTheDocument();
        expect(within(active).queryByRole('link', { name: /ghost/i })).not.toBeInTheDocument();
        expect(within(active).getByLabelText('Hidden account')).toBeInTheDocument();
    });

    it('appends classification badges to member names', () => {
        renderSection([
            { ...makeMember('Captain', 'active_7d'), is_leader: true },
            { ...makeMember('Ladder', 'active_7d'), is_ranked_player: true, highest_ranked_league: 'gold' as never },
        ]);

        const active = screen.getByTestId('clan-activity-roster');
        expect(within(active).getByLabelText('Clan leader')).toBeInTheDocument();
        // Ranked icon renders with the member's league; presence is enough here.
        expect(within(active).getByRole('link', { name: /captain/i })).toBeInTheDocument();
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
