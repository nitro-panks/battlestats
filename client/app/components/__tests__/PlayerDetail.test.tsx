import React from 'react';
import { render, screen } from '@testing-library/react';
import PlayerDetail from '../PlayerDetail';

const mockUseClanMembers = jest.fn();
let mockClanBattleSummary:
    | { seasonsPlayed: number; totalBattles: number; overallWinRate: number; }
    | null
    | undefined;

jest.mock('next/dynamic', () => {
    return () => function MockDynamicComponent(props: { playerId?: number; onSummaryChange?: (summary: { seasonsPlayed: number; totalBattles: number; overallWinRate: number; } | null) => void }) {
        const React = require('react');

        React.useEffect(() => {
            if (typeof props?.onSummaryChange === 'function' && props?.playerId && mockClanBattleSummary !== undefined) {
                props.onSummaryChange(mockClanBattleSummary);
            }
        }, [props?.onSummaryChange, props?.playerId]);

        return null;
    };
});

jest.mock('../DeferredSection', () => {
    return function MockDeferredSection({ children }: { children: React.ReactNode }) {
        return <>{children}</>;
    };
});

jest.mock('../PlayerEfficiencyBadges', () => {
    return function MockPlayerEfficiencyBadges() {
        return <div>Efficiency badges</div>;
    };
});

jest.mock('../SectionHeadingWithTooltip', () => {
    return function MockSectionHeadingWithTooltip({ title }: { title: string }) {
        return <div>{title}</div>;
    };
});

jest.mock('../HiddenAccountIcon', () => {
    return function MockHiddenAccountIcon() {
        return <span>hidden</span>;
    };
});

jest.mock('../useClanMembers', () => ({
    useClanMembers: (...args: unknown[]) => mockUseClanMembers(...args),
}));

const basePlayer = {
    id: 1,
    name: 'Rank Captain',
    player_id: 101,
    kill_ratio: 1.22,
    actual_kdr: 1.67,
    player_score: 5.15,
    total_battles: 1000,
    pvp_battles: 800,
    pvp_wins: 440,
    pvp_losses: 360,
    pvp_ratio: 55,
    pvp_survival_rate: 40,
    wins_survival_rate: null,
    creation_date: '2024-01-01',
    days_since_last_battle: 2,
    last_battle_date: '2026-03-01',
    recent_games: {},
    is_hidden: false,
    stats_updated_at: '2026-03-01T00:00:00Z',
    last_fetch: '2026-03-01T00:00:00Z',
    last_lookup: '2026-03-01T00:00:00Z',
    clan: 0,
    clan_name: '',
    clan_tag: null,
    clan_id: 0,
    is_pve_player: false,
    verdict: null,
    randoms_json: [],
    efficiency_json: [],
    ranked_json: [],
};

describe('PlayerDetail efficiency-rank icon', () => {
    beforeEach(() => {
        mockUseClanMembers.mockReturnValue({ members: [], loading: false, error: null });
        mockClanBattleSummary = undefined;
    });

    afterEach(() => {
        mockUseClanMembers.mockClear();
    });

    it('loads clan members through the shared hook using the player clan id', () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    clan_id: 4444,
                    clan_name: 'Fixture Clan',
                }}
                onBack={() => undefined}
                onSelectMember={() => undefined}
                onSelectClan={() => undefined}
            />,
        );

        expect(mockUseClanMembers).toHaveBeenCalledWith(4444);
    });

    it('renders actual KDR in the summary cards instead of weighted KDR', () => {
        render(
            <PlayerDetail
                player={basePlayer}
                onBack={() => undefined}
                onSelectMember={() => undefined}
                onSelectClan={() => undefined}
            />,
        );

        expect(screen.getByText('KDR')).toBeInTheDocument();
        expect(screen.getByText('1.67')).toBeInTheDocument();
        expect(screen.queryByText('Weighted KDR')).not.toBeInTheDocument();
    });

    it('does not render the icon for non-Expert tracked-player ranks on player detail', () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    efficiency_rank_tier: 'II',
                    has_efficiency_rank_icon: true,
                    efficiency_rank_percentile: 0.81,
                    efficiency_rank_population_size: 120,
                    efficiency_rank_updated_at: '2026-03-16T00:00:00Z',
                }}
                onBack={() => undefined}
                onSelectMember={() => undefined}
                onSelectClan={() => undefined}
            />,
        );

        expect(screen.queryByLabelText(/Battlestats efficiency rank/i)).not.toBeInTheDocument();
    });

    it('does not render the icon for legacy non-Expert fallback tiers on player detail', () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    efficiency_rank_tier: null,
                    has_efficiency_rank_icon: true,
                    efficiency_rank_percentile: 0.62,
                    efficiency_rank_population_size: 84,
                }}
                onBack={() => undefined}
                onSelectMember={() => undefined}
                onSelectClan={() => undefined}
            />,
        );

        expect(screen.queryByLabelText(/Battlestats efficiency rank/i)).not.toBeInTheDocument();
    });

    it('renders the sigma icon for Expert tracked-player ranks', () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    efficiency_rank_tier: 'E',
                    has_efficiency_rank_icon: true,
                    efficiency_rank_percentile: 0.99,
                    efficiency_rank_population_size: 120,
                    efficiency_rank_updated_at: '2026-03-16T00:00:00Z',
                }}
                onBack={() => undefined}
                onSelectMember={() => undefined}
                onSelectClan={() => undefined}
            />,
        );

        expect(screen.getByLabelText(/Battlestats efficiency rank Expert: 99th percentile among eligible tracked players\. Based on stored WG badge profile for 120 tracked players\./i)).toBeInTheDocument();
        expect(screen.getByText('Σ')).toBeInTheDocument();
    });

    it('hides the tracked-player efficiency icon when the API flag is false', () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    efficiency_rank_tier: null,
                    has_efficiency_rank_icon: false,
                    efficiency_rank_percentile: 0.81,
                }}
                onBack={() => undefined}
                onSelectMember={() => undefined}
                onSelectClan={() => undefined}
            />,
        );

        expect(screen.queryByLabelText(/Battlestats efficiency rank/i)).not.toBeInTheDocument();
    });

    it('does not render the icon for hidden players even if legacy rank fields are present', () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    is_hidden: true,
                    efficiency_rank_tier: 'E',
                    has_efficiency_rank_icon: true,
                    efficiency_rank_percentile: 0.99,
                    efficiency_rank_population_size: 120,
                }}
                onBack={() => undefined}
                onSelectMember={() => undefined}
                onSelectClan={() => undefined}
            />,
        );

        expect(screen.queryByLabelText(/Battlestats efficiency rank/i)).not.toBeInTheDocument();
    });

    it('does not render a sigma icon from the best stored WG efficiency badge when no published rank exists', () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    efficiency_json: [
                        {
                            ship_id: 1001,
                            ship_name: 'Fixture Cruiser',
                            top_grade_class: 3,
                            top_grade_label: 'II',
                            badge_label: 'II',
                        },
                        {
                            ship_id: 1002,
                            ship_name: 'Fixture Destroyer',
                            top_grade_class: 4,
                            top_grade_label: 'III',
                            badge_label: 'III',
                        },
                    ],
                }}
                onBack={() => undefined}
                onSelectMember={() => undefined}
                onSelectClan={() => undefined}
            />,
        );

        expect(screen.queryByLabelText(/Battlestats efficiency rank/i)).not.toBeInTheDocument();
        expect(screen.queryByText('Σ')).not.toBeInTheDocument();
    });

    it('does not render a sigma icon for stored Expert badge rows without a published rank', () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    efficiency_json: [
                        {
                            ship_id: 1003,
                            ship_name: 'Fixture Battleship',
                            top_grade_class: 1,
                            top_grade_label: 'Expert',
                            badge_label: 'Expert',
                        },
                    ],
                    efficiency_rank_tier: null,
                    has_efficiency_rank_icon: false,
                }}
                onBack={() => undefined}
                onSelectMember={() => undefined}
                onSelectClan={() => undefined}
            />,
        );

        expect(screen.queryByLabelText(/Battlestats efficiency rank/i)).not.toBeInTheDocument();
        expect(screen.queryByText('Σ')).not.toBeInTheDocument();
    });

    it('renders the PvE robot from the shared backend flag even when PvE does not exceed PvP', () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    total_battles: 14344,
                    pvp_battles: 9549,
                    is_pve_player: true,
                }}
                onBack={() => undefined}
                onSelectMember={() => undefined}
                onSelectClan={() => undefined}
            />,
        );

        expect(screen.getByLabelText(/pve enjoyer/i)).toBeInTheDocument();
    });

    it('does not render the PvE robot when the shared backend flag is false', () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    total_battles: 4951,
                    pvp_battles: 464,
                    is_pve_player: false,
                }}
                onBack={() => undefined}
                onSelectMember={() => undefined}
                onSelectClan={() => undefined}
            />,
        );

        expect(screen.queryByLabelText(/pve enjoyer/i)).not.toBeInTheDocument();
    });

    it('renders the clan battle shield immediately from cached player payload state', () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    clan_battle_header_eligible: true,
                    clan_battle_header_total_battles: 48,
                    clan_battle_header_seasons_played: 3,
                    clan_battle_header_overall_win_rate: 56.3,
                }}
                onBack={() => undefined}
                onSelectMember={() => undefined}
                onSelectClan={() => undefined}
            />,
        );

        expect(screen.getByLabelText(/clan battle enjoyer 56\.3 percent WR/i)).toBeInTheDocument();
    });

    it('updates the clan battle shield when fetched summary changes the rendered state', () => {
        mockClanBattleSummary = {
            seasonsPlayed: 4,
            totalBattles: 67,
            overallWinRate: 60.2,
        };

        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    clan_id: 4444,
                    clan_name: 'Fixture Clan',
                    clan_battle_header_eligible: true,
                    clan_battle_header_total_battles: 48,
                    clan_battle_header_seasons_played: 3,
                    clan_battle_header_overall_win_rate: 56.3,
                }}
                onBack={() => undefined}
                onSelectMember={() => undefined}
                onSelectClan={() => undefined}
            />,
        );

        expect(screen.getByLabelText(/clan battle enjoyer 60\.2 percent WR/i)).toBeInTheDocument();
    });

    it('clears a cached clan battle shield when fetched summary is no longer eligible', () => {
        mockClanBattleSummary = {
            seasonsPlayed: 1,
            totalBattles: 18,
            overallWinRate: 60.2,
        };

        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    clan_id: 4444,
                    clan_name: 'Fixture Clan',
                    clan_battle_header_eligible: true,
                    clan_battle_header_total_battles: 48,
                    clan_battle_header_seasons_played: 3,
                    clan_battle_header_overall_win_rate: 56.3,
                }}
                onBack={() => undefined}
                onSelectMember={() => undefined}
                onSelectClan={() => undefined}
            />,
        );

        expect(screen.queryByLabelText(/clan battle enjoyer/i)).not.toBeInTheDocument();
    });

    it('preserves cached clan battle shield state when the seasons component never reports a new summary', () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    clan_id: 4444,
                    clan_name: 'Fixture Clan',
                    clan_battle_header_eligible: true,
                    clan_battle_header_total_battles: 48,
                    clan_battle_header_seasons_played: 3,
                    clan_battle_header_overall_win_rate: 56.3,
                }}
                onBack={() => undefined}
                onSelectMember={() => undefined}
                onSelectClan={() => undefined}
            />,
        );

        expect(screen.getByLabelText(/clan battle enjoyer 56\.3 percent WR/i)).toBeInTheDocument();
    });
});