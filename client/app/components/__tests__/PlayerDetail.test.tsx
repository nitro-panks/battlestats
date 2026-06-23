import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import PlayerDetail from '../PlayerDetail';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
    getChartFetchesInFlight: jest.fn(() => 0),
    incrementChartFetches: jest.fn(),
    decrementChartFetches: jest.fn(),
}));

jest.mock('../../lib/umami', () => ({
    trackEvent: (...args: unknown[]) => mockTrackEvent(...args),
}));

const mockUseClanMembers = jest.fn();
const mockClipboardWriteText = jest.fn();
const mockTrackEvent = jest.fn();
const mockClanSvg = jest.fn();
let mockClanBattleSummary:
    | { seasonsPlayed: number; totalBattles: number; overallWinRate: number; }
    | null
    | undefined;
let mockRankedHeatmapVisibility: boolean | undefined;
const mockFetchSharedJson = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

// Valid-but-empty battle-history payload for the embedded Activity-tab card.
// The card fetches battle-history on mount; without `by_day` as an array its
// sparkline builder throws. Zero battles + random-only also makes the card
// report "no activity", so the Activity tab darks out and selection falls back
// to Ships — the default these PlayerDetail tab tests assert against.
const emptyBattleHistoryPayload = {
    as_of: '2026-06-06T00:00:00Z',
    available_modes: ['random'],
    totals: {
        battles: 0, wins: 0, losses: 0, win_rate: 0, damage: 0, avg_damage: 0,
        frags: 0, xp: 0, planes_killed: 0, survived_battles: 0, survival_rate: 0,
    },
    by_ship: [],
    by_day: [],
};

jest.mock('next/dynamic', () => {
    return () => function MockDynamicComponent(props: {
        clanId?: number;
        highlightedPlayerName?: string;
        playerId?: number;
        onSummaryChange?: (summary: { seasonsPlayed: number; totalBattles: number; overallWinRate: number; } | null) => void;
        onVisibilityChange?: (isVisible: boolean) => void;
        title?: string;
    }) {
        const React = require('react');
        const { onSummaryChange, onVisibilityChange, playerId } = props;

        React.useEffect(() => {
            if (typeof onSummaryChange === 'function' && playerId && mockClanBattleSummary !== undefined) {
                onSummaryChange(mockClanBattleSummary);
            }
            if (typeof onVisibilityChange === 'function' && mockRankedHeatmapVisibility !== undefined) {
                onVisibilityChange(mockRankedHeatmapVisibility);
            }
        }, [onSummaryChange, onVisibilityChange, playerId]);

        return <div data-testid="dynamic-component" />;
    };
});

jest.mock('../ClanSVG', () => ({
    __esModule: true,
    default: (props: unknown) => {
        mockClanSvg(props);
        return <div data-testid="player-clan-chart" />;
    },
}));

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
    let consoleErrorSpy: jest.SpyInstance;

    beforeEach(() => {
        mockUseClanMembers.mockReturnValue({ members: [], loading: false, error: null });
        mockTrackEvent.mockReset();
        mockClanSvg.mockReset();
        mockClanBattleSummary = undefined;
        mockRankedHeatmapVisibility = undefined;
        mockFetchSharedJson.mockReset();
        mockFetchSharedJson.mockImplementation((url: string) => {
            if (typeof url === 'string' && url.includes('/battle-history/')) {
                return Promise.resolve({ data: emptyBattleHistoryPayload, headers: {} });
            }
            if (typeof url === 'string' && url.includes('/api/fetch/player_correlation/tier_type/')) {
                return Promise.resolve({
                    data: {
                        metric: 'tier_type',
                        label: 'Tier vs Ship Type',
                        x_label: 'Ship Type',
                        y_label: 'Tier',
                        tracked_population: 1,
                        x_labels: ['Destroyer', 'Cruiser', 'Battleship', 'Aircraft Carrier', 'Submarine'],
                        y_values: [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
                        tiles: [],
                        trend: [],
                        player_cells: [],
                    },
                    headers: {},
                });
            }
            return Promise.resolve({ data: [], headers: {} });
        });
        mockClipboardWriteText.mockReset();
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

    it('shows the loading affordance while a visit refresh is pending', () => {
        render(
            <PlayerDetail
                player={basePlayer}
                refreshStatus={{ phase: 'loading', secondsRemaining: 0 }}
            />,
        );

        const status = screen.getByTestId('live-refresh-status');
        expect(status).toHaveTextContent('Updating');
        // The in-progress "Updating…" pill uses the animated rainbow text so the
        // refresh is hard to miss; the steady-state "Next update" text does not.
        expect(status).toHaveClass('rainbow-text');
    });

    it('shows the cooldown countdown in minutes left of the share button', () => {
        render(
            <PlayerDetail
                player={basePlayer}
                refreshStatus={{ phase: 'cooldown', secondsRemaining: 720 }}
            />,
        );

        expect(screen.getByTestId('live-refresh-status')).toHaveTextContent('Next update: 12 min');
    });

    it('renders no status pill once the cooldown reaches zero (auto-refresh handles it)', () => {
        // At zero the hook flips to phase "loading" in the same tick, so a
        // cooldown/zero render is only the brief parked state — show nothing
        // rather than a stale "Update available" prompt.
        render(
            <PlayerDetail
                player={basePlayer}
                refreshStatus={{ phase: 'cooldown', secondsRemaining: 0 }}
            />,
        );

        expect(screen.queryByTestId('live-refresh-status')).not.toBeInTheDocument();
    });

    it('suppresses the live-refresh badge for hidden players', () => {
        render(
            <PlayerDetail
                player={{ ...basePlayer, is_hidden: true }}
                refreshStatus={{ phase: 'loading', secondsRemaining: 0 }}
            />,
        );

        expect(screen.queryByTestId('live-refresh-status')).not.toBeInTheDocument();
    });

    it('renders actual KDR in the summary cards instead of weighted KDR', () => {
        render(
            <PlayerDetail
                player={basePlayer}
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
            />,
        );

        expect(screen.queryByLabelText(/pve enjoyer/i)).not.toBeInTheDocument();
    });

    it('renders the Twitch icon for flagged streamer accounts', () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    is_streamer: true,
                    twitch_handle: 'rankcaptain',
                    twitch_url: 'https://www.twitch.tv/rankcaptain',
                }}
            />,
        );

        expect(
            screen.getByLabelText(/twitch channel for rankcaptain/i),
        ).toBeInTheDocument();
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
            />,
        );

        fireEvent.click(screen.getByRole('tab', { name: 'Clan Battles' }));

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
            />,
        );

        fireEvent.click(screen.getByRole('tab', { name: 'Clan Battles' }));

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
            />,
        );

        expect(screen.getByLabelText(/clan battle enjoyer 56\.3 percent WR/i)).toBeInTheDocument();
    });

    it('renders hidden-player messaging and suppresses detail-only sections', () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    is_hidden: true,
                    clan_id: 4444,
                    clan_name: 'Fixture Clan',
                }}
            />,
        );

        expect(screen.getByText("This player's stats are hidden.")).toBeInTheDocument();
        expect(screen.queryByText('Win Rate')).not.toBeInTheDocument();
        expect(screen.queryByText('Efficiency badges')).not.toBeInTheDocument();
    });

    it('moves clan battle seasons, efficiency badges, and performance by tier behind focused tabs', async () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    clan_id: 4444,
                    clan_name: 'Fixture Clan',
                }}
            />,
        );

        // Activity is the default tab, but this fixture has no battle activity, so
        // selection falls back to Ships; the profile/efficiency/clan panels stay
        // gated behind their tabs.
        await waitFor(() => {
            expect(screen.getByText('Top Ships (Random Battles)')).toBeInTheDocument();
        });
        expect(screen.queryByText('Performance by Tier')).not.toBeInTheDocument();
        expect(screen.queryByText('Clan Battle Seasons')).not.toBeInTheDocument();
        expect(screen.queryByText('Efficiency badges')).not.toBeInTheDocument();

        // Performance by Tier lives behind the Profile tab.
        fireEvent.click(screen.getByRole('tab', { name: 'Profile' }));
        await waitFor(() => {
            expect(screen.getByText('Performance by Tier')).toBeInTheDocument();
        });

        fireEvent.click(screen.getByRole('tab', { name: 'Efficiency' }));

        expect(screen.getByText('Efficiency badges')).toBeInTheDocument();
        expect(screen.queryByText('Clan Battle Seasons')).not.toBeInTheDocument();
        expect(screen.queryByText('Performance by Tier')).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('tab', { name: 'Clan Battles' }));

        expect(screen.getByText('Clan Battle Seasons')).toBeInTheDocument();
        expect(screen.queryByText('Performance by Tier')).not.toBeInTheDocument();
        expect(screen.queryByText('Efficiency badges')).not.toBeInTheDocument();
    });

    it('renders header markers for leader, ranked, and sleepy states without the playstyle panel', () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    is_clan_leader: true,
                    days_since_last_battle: 500,
                    highest_ranked_league: 'Gold',
                    ranked_json: [{ total_battles: 120, total_wins: 70, highest_league_name: 'Gold' }],
                    verdict: 'Warrior',
                }}
            />,
        );

        expect(screen.getByLabelText('Clan leader')).toBeInTheDocument();
        expect(screen.getByLabelText(/Gone dark/i)).toBeInTheDocument();
        expect(screen.getByLabelText(/ranked enjoyer/i)).toBeInTheDocument();
        expect(screen.queryByText('Playstyle:')).not.toBeInTheDocument();
        expect(screen.queryByText('Warrior')).not.toBeInTheDocument();
    });

    it('copies the player URL and clears the copied state after the timeout', async () => {
        jest.useFakeTimers();
        mockClipboardWriteText.mockResolvedValue(undefined);

        render(
            <PlayerDetail
                player={basePlayer}
            />,
        );

        fireEvent.click(screen.getByRole('button', { name: 'Copy shareable player URL' }));

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
            <PlayerDetail
                player={basePlayer}
            />,
        );

        fireEvent.click(screen.getByRole('button', { name: 'Copy shareable player URL' }));

        expect(await screen.findByText('Copy failed')).toBeInTheDocument();
        expect(consoleErrorSpy).toHaveBeenCalled();
    });

    it('fires a player-share umami event when the share button is clicked', () => {
        mockClipboardWriteText.mockResolvedValue(undefined);

        render(
            <PlayerDetail
                player={basePlayer}
            />,
        );

        fireEvent.click(screen.getByRole('button', { name: 'Copy shareable player URL' }));

        expect(mockTrackEvent).toHaveBeenCalledWith('player-share', expect.objectContaining({ realm: expect.any(String) }));
    });

    it('defaults to Activity, then falls back to Ships when the player has no battle activity', async () => {
        render(
            <PlayerDetail
                player={{
                    ...basePlayer,
                    ranked_json: [{ total_battles: 0, total_wins: 0 }],
                }}
            />,
        );

        // Activity is the default (left-most) tab on initial render.
        expect(screen.getByRole('tab', { name: 'Activity' })).toHaveAttribute('aria-selected', 'true');

        // The empty battle-history payload reports no activity, so the Activity
        // tab darks out and selection falls back to Ships.
        await waitFor(() => {
            expect(screen.getByRole('tab', { name: 'Ships' })).toHaveAttribute('aria-selected', 'true');
        });
        expect(screen.getByRole('tab', { name: 'Activity' })).toBeDisabled();
        expect(screen.getByText('Top Ships (Random Battles)')).toBeInTheDocument();
        // Profile and Ranked lanes stay inactive until selected.
        expect(screen.queryByText('Tier vs Type Profile')).not.toBeInTheDocument();
        expect(screen.queryByText('Ranked Games vs Win Rate')).not.toBeInTheDocument();
    });

});
describe('PlayerDetail ship-badge banner', () => {
    let consoleErrorSpy: jest.SpyInstance;

    beforeEach(() => {
        mockUseClanMembers.mockReturnValue({ members: [], loading: false, error: null });
        mockClanBattleSummary = undefined;
        mockRankedHeatmapVisibility = undefined;
        mockFetchSharedJson.mockReset();
        mockFetchSharedJson.mockImplementation((url: string) => (
            typeof url === 'string' && url.includes('/battle-history/')
                ? Promise.resolve({ data: emptyBattleHistoryPayload, headers: {} })
                : Promise.resolve({ data: [], headers: {} })
        ));
        consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => undefined);
        jest.useRealTimers();
    });

    afterEach(() => {
        mockUseClanMembers.mockClear();
        consoleErrorSpy.mockRestore();
    });

    const renderWithBadges = (ship_badges: unknown) =>
        render(
            <PlayerDetail
                player={{ ...basePlayer, ship_badges } as never}
            />,
        );

    const badge = (over: Record<string, unknown> = {}) => ({
        ship_id: 10, ship_name: 'Shimakaze', rank: 1, win_rate: 64.0, battles: 312,
        avg_damage: 62431, window_days: 14, ...over,
    });

    it('renders a banner card per ship placement with win rate and damage', () => {
        renderWithBadges([
            badge(),
            badge({ ship_id: 20, ship_name: 'Zao', rank: 2, win_rate: 58.5, avg_damage: 41200 }),
        ]);

        expect(screen.getByText('Shimakaze')).toBeInTheDocument();
        expect(screen.getByText('Zao')).toBeInTheDocument();
        // Stat row: emphasized win rate · average damage (compact "dmg" label).
        expect(screen.getByText(/62,431 dmg/)).toBeInTheDocument();
        expect(screen.getByText('64.0%')).toBeInTheDocument();
        expect(screen.getByText('58.5%')).toBeInTheDocument();
        // Links to the ship standings page.
        const link = screen.getByTitle(/#1 in Shimakaze last 14d/);
        expect(link).toHaveAttribute('href', expect.stringContaining('/ship/10-shimakaze'));
    });

    it('renders no banner when ship_badges is empty', () => {
        renderWithBadges([]);

        expect(screen.queryByText(/dmg/)).not.toBeInTheDocument();
    });

    it('shows the ship tier chip on the banner card', () => {
        renderWithBadges([badge({ ship_id: 30, ship_name: 'Baltimore', tier: 8 })]);

        expect(screen.getByText('Baltimore')).toBeInTheDocument();
        expect(screen.getByText('T8')).toBeInTheDocument();
    });

    it('shows the realm under the medal and in the tooltip (awards are per realm)', () => {
        render(
            <PlayerDetail
                player={{ ...basePlayer, realm: 'na', ship_badges: [badge()] } as never}
            />,
        );

        const banner = screen.getByLabelText('Top ship rankings');
        expect(within(banner).getByText('NA')).toBeInTheDocument();
        expect(screen.getByTitle(/#1 in Shimakaze on NA last 14d/)).toBeInTheDocument();
    });

    it('stacks multiple badges (no overflow cap — backend already limits to top 3)', () => {
        renderWithBadges([
            badge({ ship_id: 1, ship_name: 'ShipA', rank: 1 }),
            badge({ ship_id: 2, ship_name: 'ShipB', rank: 2 }),
            badge({ ship_id: 3, ship_name: 'ShipC', rank: 3 }),
        ]);

        expect(screen.getByText('ShipA')).toBeInTheDocument();
        expect(screen.getByText('ShipB')).toBeInTheDocument();
        expect(screen.getByText('ShipC')).toBeInTheDocument();
    });

    it('renders a top-spot medal in the header tray per badge, with a rank tooltip', () => {
        render(
            <PlayerDetail
                player={{ ...basePlayer, realm: 'na', ship_badges: [
                    badge({ ship_id: 10, ship_name: 'Shimakaze', rank: 1 }),
                    badge({ ship_id: 20, ship_name: 'Zao', rank: 2 }),
                ] } as never}
            />,
        );

        // Tray icons use the "Currently #<n> <ship> on <REALM>" tooltip (distinct
        // from the banner card's "#<n> in <ship> … over the last N days" tooltip).
        expect(screen.getByTitle('Currently #1 Shimakaze on NA')).toBeInTheDocument();
        expect(screen.getByTitle('Currently #2 Zao on NA')).toBeInTheDocument();
    });
});
