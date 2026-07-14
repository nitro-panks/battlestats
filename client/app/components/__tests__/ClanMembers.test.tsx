import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import ClanMembers from '../ClanMembers';
import type { ClanMemberData } from '../clanMembersShared';

const trackEventMock = jest.fn();
jest.mock('../../lib/umami', () => ({
    trackEvent: (...args: unknown[]) => trackEventMock(...args),
}));

const baseMember: ClanMemberData = {
    name: 'Member One',
    is_hidden: false,
    pvp_ratio: 57.4,
    days_since_last_battle: 2,
    is_leader: false,
    is_pve_player: false,
    is_sleepy_player: false,
    is_ranked_player: false,
    is_clan_battle_player: false,
    clan_battle_win_rate: null,
    efficiency_hydration_pending: false,
    highest_ranked_league: null,
    ranked_hydration_pending: false,
    ranked_updated_at: null,
    efficiency_rank_percentile: null,
    efficiency_rank_tier: null,
    has_efficiency_rank_icon: false,
    efficiency_rank_population_size: null,
    efficiency_rank_updated_at: null,
    activity_bucket: 'active_7d',
};

describe('ClanMembers efficiency-rank icon', () => {
    it('stays silent while efficiency ranks are still warming', () => {
        render(
            <ClanMembers
                members={[
                    {
                        ...baseMember,
                        efficiency_hydration_pending: true,
                    },
                    {
                        ...baseMember,
                        name: 'Member Two',
                        efficiency_hydration_pending: true,
                    },
                ]}
                onSelectMember={() => undefined}
            />,
        );

        expect(screen.queryByText(/Updating:/i)).not.toBeInTheDocument();
    });

    it('does not show a hydration status once efficiency warming is complete', () => {
        render(
            <ClanMembers
                members={[baseMember]}
                onSelectMember={() => undefined}
            />,
        );

        expect(screen.queryByText(/Updating:/i)).not.toBeInTheDocument();
    });

    it('does not render the icon for non-Expert ranked members', () => {
        render(
            <ClanMembers
                members={[
                    {
                        ...baseMember,
                        efficiency_rank_percentile: 0.81,
                        efficiency_rank_tier: 'II',
                        has_efficiency_rank_icon: true,
                        efficiency_rank_population_size: 124,
                    },
                ]}
                onSelectMember={() => undefined}
            />,
        );

        expect(screen.queryByLabelText(/Battlestats efficiency rank/i)).not.toBeInTheDocument();
    });

    it('does not render the icon for legacy non-Expert fallback tiers', () => {
        render(
            <ClanMembers
                members={[
                    {
                        ...baseMember,
                        efficiency_rank_percentile: 0.62,
                        efficiency_rank_tier: null,
                        has_efficiency_rank_icon: true,
                        efficiency_rank_population_size: 84,
                    },
                ]}
                onSelectMember={() => undefined}
            />,
        );

        expect(screen.queryByLabelText(/Battlestats efficiency rank/i)).not.toBeInTheDocument();
    });

    it('does not render the icon when the member is not published into the tracked rank field', () => {
        render(
            <ClanMembers
                members={[baseMember]}
                onSelectMember={() => undefined}
            />,
        );

        expect(screen.queryByLabelText(/Battlestats efficiency rank/i)).not.toBeInTheDocument();
    });

    it('keeps the row clickable while showing the Expert icon', () => {
        const onSelectMember = jest.fn();

        render(
            <ClanMembers
                members={[
                    {
                        ...baseMember,
                        efficiency_rank_percentile: 0.97,
                        efficiency_rank_tier: 'E',
                        has_efficiency_rank_icon: true,
                        efficiency_rank_population_size: 367,
                    },
                ]}
                onSelectMember={onSelectMember}
            />,
        );

        fireEvent.click(screen.getByRole('button', { name: /Show player Member One/i }));

        expect(screen.getByLabelText(/Battlestats efficiency rank Expert: 97th percentile among eligible tracked players\. Based on stored WG badge profile for 367 tracked players\./i)).toBeInTheDocument();
        expect(screen.getByText('Σ')).toBeInTheDocument();
        expect(onSelectMember).toHaveBeenCalledWith('Member One');
    });
});

describe('ClanMembers activity icon', () => {
    it('renders a graded activity icon per bucket instead of raw idle text', () => {
        render(
            <ClanMembers
                members={[
                    { ...baseMember, name: 'Sunny', activity_bucket: 'active_7d' },
                    { ...baseMember, name: 'Dusk', activity_bucket: 'active_30d' },
                    { ...baseMember, name: 'Cool', activity_bucket: 'cooling_90d' },
                    { ...baseMember, name: 'Dorm', activity_bucket: 'dormant_180d' },
                    { ...baseMember, name: 'Dark', activity_bucket: 'inactive_180d_plus' },
                ]}
                onSelectMember={() => undefined}
            />,
        );

        expect(screen.getByLabelText(/Active —/i)).toBeInTheDocument();
        expect(screen.getByLabelText(/Warm —/i)).toBeInTheDocument();
        expect(screen.getByLabelText(/Cooling —/i)).toBeInTheDocument();
        expect(screen.getByLabelText(/Cold —/i)).toBeInTheDocument();
        expect(screen.getByLabelText(/Asleep —/i)).toBeInTheDocument();
    });

    it('drops the old "Nd idle" / "played today" recency text', () => {
        render(
            <ClanMembers
                members={[{ ...baseMember, days_since_last_battle: 2, activity_bucket: 'active_7d' }]}
                onSelectMember={() => undefined}
            />,
        );

        expect(screen.queryByText(/idle/i)).not.toBeInTheDocument();
        expect(screen.queryByText(/played today/i)).not.toBeInTheDocument();
    });

    it('renders no activity icon when the bucket is unknown', () => {
        render(
            <ClanMembers
                members={[{ ...baseMember, activity_bucket: 'unknown' }]}
                onSelectMember={() => undefined}
            />,
        );

        expect(screen.queryByLabelText(/Active —|Warm —|Cooling —|Cold —|Asleep —/i)).not.toBeInTheDocument();
    });
});

describe('ClanMembers current-player marker', () => {
    it('renders the current player as a non-interactive "you are here" marker, not a self-link', () => {
        const onSelectMember = jest.fn();
        render(
            <ClanMembers
                members={[baseMember, { ...baseMember, name: 'Member Two' }]}
                onSelectMember={onSelectMember}
                highlightedPlayerName="Member One"
            />,
        );

        // The current player is no longer a clickable link to their own page.
        expect(screen.queryByRole('button', { name: /Show player Member One/i })).not.toBeInTheDocument();

        // Other members stay clickable links.
        expect(screen.getByRole('button', { name: /Show player Member Two/i })).toBeInTheDocument();

        // The current player's row is marked aria-current and carries the name.
        const marker = screen.getByText('Member One').closest('[aria-current="page"]');
        expect(marker).not.toBeNull();
        expect(marker).toHaveTextContent('Member One');
    });

    it('matches the current player case-insensitively', () => {
        render(
            <ClanMembers
                members={[baseMember]}
                onSelectMember={() => undefined}
                highlightedPlayerName="member one"
            />,
        );

        expect(screen.queryByRole('button', { name: /Show player Member One/i })).not.toBeInTheDocument();
        expect(screen.getByText('Member One').closest('[aria-current="page"]')).not.toBeNull();
    });

    it('keeps every member clickable when no current player is highlighted', () => {
        render(
            <ClanMembers
                members={[baseMember]}
                onSelectMember={() => undefined}
            />,
        );

        expect(screen.getByRole('button', { name: /Show player Member One/i })).toBeInTheDocument();
        expect(screen.getByText('Member One').closest('[aria-current="page"]')).toBeNull();
    });
});

describe('ClanMembers click tracking', () => {
    beforeEach(() => trackEventMock.mockReset());

    it('fires a clan-member-click umami event and still navigates on a roster click', () => {
        const onSelectMember = jest.fn();
        render(<ClanMembers members={[baseMember]} onSelectMember={onSelectMember} />);

        fireEvent.click(screen.getByRole('button', { name: /Show player Member One/i }));

        expect(trackEventMock).toHaveBeenCalledWith('clan-member-click', { realm: 'na', source: 'clan' });
        expect(onSelectMember).toHaveBeenCalledWith('Member One');
    });

    it('tags the clan-member-click with the rendering surface via source', () => {
        const onSelectMember = jest.fn();
        render(<ClanMembers members={[baseMember]} onSelectMember={onSelectMember} source="player" />);

        fireEvent.click(screen.getByRole('button', { name: /Show player Member One/i }));

        expect(trackEventMock).toHaveBeenCalledWith('clan-member-click', { realm: 'na', source: 'player' });
    });
});