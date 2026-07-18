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

const makeMember = (name: string, activityBucket: ActivityBucketKey, isActivePvp = false): ClanMemberData => ({
    name,
    is_hidden: false,
    is_active_pvp: isActivePvp,
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

    it('splits the roster into a labeled Active PvP block and one unlabeled block of everyone else', () => {
        renderSection([
            makeMember('Echo', 'inactive_180d_plus'),
            makeMember('Charlie', 'cooling_90d'),
            makeMember('Alpha', 'active_7d', true),
            makeMember('Delta', 'dormant_180d', true),
            // Recently active on the WG account clock but with no random or
            // ranked battles in the window (e.g. co-op-only) — must NOT make
            // the Active PvP block.
            makeMember('Bravo', 'active_30d'),
            // Current-season CB player with no 30d randoms/ranked rides
            // along in the Active PvP block — the this-season shield must
            // not sit under the idle rule.
            { ...makeMember('Foxtrot', 'cooling_90d'), is_clan_battle_player: true, clan_battle_win_rate: 55 },
        ]);

        const roster = screen.getByTestId('clan-activity-roster');

        // Top: the Active PvP label + the members with random/ranked battles
        // in the 30d window (or a current-season CB shield), regardless of
        // activity bucket.
        expect(roster).toHaveTextContent('Active PvP (3)');
        const active = screen.getByTestId('clan-roster-active');
        expect(within(active).getByText('Alpha')).toBeInTheDocument();
        expect(within(active).getByText('Delta')).toBeInTheDocument();
        expect(within(active).getByText('Foxtrot')).toBeInTheDocument();

        // A rule separates the two blocks.
        expect(roster.querySelector('hr')).toBeInTheDocument();

        // Below: everyone else in one alphabetical block with NO second label.
        const others = screen.getByTestId('clan-roster-others');
        const names = ['Bravo', 'Charlie', 'Echo'];
        names.forEach((name) => expect(within(others).getByText(name)).toBeInTheDocument());
        const positions = names.map((name) => others.textContent!.indexOf(name));
        expect([...positions].sort((a, b) => a - b)).toEqual(positions);
        expect(roster).not.toHaveTextContent('Cooling Off');
        expect(roster).not.toHaveTextContent('Gone dark');
    });

    it('lays each block out as a fixed four-column grid with a WR-colored mark per name', () => {
        renderSection([
            { ...makeMember('Alpha', 'active_7d', true), pvp_ratio: 61 },
            makeMember('Bravo', 'active_7d', true),
            makeMember('Charlie', 'cooling_90d'),
            makeMember('Delta', 'inactive_180d_plus'),
            makeMember('Echo', 'inactive_180d_plus'),
            { ...makeMember('Foxtrot', 'inactive_180d_plus'), pvp_ratio: null },
            makeMember('Golf', 'inactive_180d_plus'),
            makeMember('Hotel', 'inactive_180d_plus'),
        ]);

        // Both blocks use the same fixed grid: 4 even columns (2 below sm).
        const active = screen.getByTestId('clan-roster-active');
        expect(active.className).toContain('grid');
        expect(active.className).toContain('sm:grid-cols-4');
        const others = screen.getByTestId('clan-roster-others');
        expect(others.className).toContain('sm:grid-cols-4');
        // One grid cell per member.
        expect(others.querySelectorAll('li')).toHaveLength(6);

        // Every name leads with a diamond on the shared WR color scale.
        const marks = screen.getAllByTestId('roster-wr-mark');
        expect(marks).toHaveLength(8);
        const markFillFor = (name: string) => {
            const cell = screen.getByText(name).closest('li')!;
            return within(cell as HTMLElement)
                .getByTestId('roster-wr-mark')
                .querySelector('polygon')!
                .getAttribute('fill');
        };
        // 61% → the unicum magenta band; 52% → the light-green band.
        expect(markFillFor('Alpha')).toBe('#D042F3');
        expect(markFillFor('Bravo')).toBe('#a1d99b');
        // Null WR → the scale's pale no-data blue.
        expect(markFillFor('Foxtrot')).toBe('#c6dbef');
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

    it('marks gone-dark members with the bed icon, and only them', () => {
        renderSection([
            makeMember('Alpha', 'active_7d', true),
            makeMember('Cooling', 'cooling_90d'),
            makeMember('Sleeper', 'inactive_180d_plus'),
            makeMember('DeepSleeper', 'dormant_180d'),
        ]);

        // dormant_180d collapses to Cooling — only true 181d+ members get
        // the bed. One bed for Sleeper, none for the others.
        const beds = screen.getAllByLabelText(/Asleep — inactive 180\+ days/);
        expect(beds).toHaveLength(1);
        const sleeperCell = screen.getByText('Sleeper').closest('li')!;
        expect(within(sleeperCell as HTMLElement).getByLabelText(/Asleep/)).toBeInTheDocument();
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
