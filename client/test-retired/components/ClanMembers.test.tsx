import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import ClanMembers from '../ClanMembers';
import type { ClanMemberData } from '../clanMembersShared';

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
    it('shows a hydration status while efficiency ranks are still warming', () => {
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

        expect(screen.getByText('Updating: 2 members.')).toBeInTheDocument();
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