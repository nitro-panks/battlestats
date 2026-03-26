import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import PlayerRouteView from '../PlayerRouteView';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

const pushMock = jest.fn();
const trackEntityDetailViewMock = jest.fn();
const mockUseClanMembers = jest.fn();
const mockClanSvg = jest.fn();

jest.mock('next/navigation', () => ({
    useRouter: () => ({
        push: pushMock,
    }),
}));

jest.mock('../../lib/visitAnalytics', () => ({
    trackEntityDetailView: (...args: unknown[]) => trackEntityDetailViewMock(...args),
}));

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
}));

jest.mock('next/dynamic', () => {
    return () => function MockDynamicComponent(props: {
        onSummaryChange?: (summary: { seasonsPlayed: number; totalBattles: number; overallWinRate: number; } | null) => void;
        onVisibilityChange?: (isVisible: boolean) => void;
    }) {
        const React = require('react');

        React.useEffect(() => {
            if (typeof props.onVisibilityChange === 'function') {
                props.onVisibilityChange(true);
            }
            if (typeof props.onSummaryChange === 'function') {
                props.onSummaryChange(null);
            }
        }, [props.onSummaryChange, props.onVisibilityChange]);

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
        return <div>Efficiency Badges</div>;
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

const mockFetchSharedJson = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

const profileChartPayload = {
    metric: 'tier_type' as const,
    label: 'Tier vs Ship Type',
    x_label: 'Ship Type',
    y_label: 'Tier',
    tracked_population: 2,
    tiles: [
        { ship_type: 'Destroyer', ship_tier: 10, count: 40 },
        { ship_type: 'Cruiser', ship_tier: 8, count: 20 },
    ],
    trend: [
        { ship_type: 'Destroyer', avg_tier: 9.5, count: 40 },
        { ship_type: 'Cruiser', avg_tier: 8, count: 20 },
    ],
    player_cells: [
        { ship_type: 'Destroyer', ship_tier: 10, pvp_battles: 25, wins: 15, win_ratio: 0.6 },
        { ship_type: 'Cruiser', ship_tier: 8, pvp_battles: 10, wins: 5, win_ratio: 0.5 },
    ],
};

const playerRoutePayload = {
    id: 1,
    name: 'Player One',
    player_id: 77,
    kill_ratio: null,
    actual_kdr: null,
    player_score: null,
    total_battles: 100,
    pvp_battles: 80,
    pvp_wins: 44,
    pvp_losses: 36,
    pvp_ratio: 55,
    pvp_survival_rate: 30,
    wins_survival_rate: null,
    creation_date: '2024-01-01',
    days_since_last_battle: 2,
    last_battle_date: '2026-03-01',
    recent_games: {},
    is_hidden: false,
    stats_updated_at: '2026-03-01T00:00:00Z',
    last_fetch: '2026-03-01T00:00:00Z',
    last_lookup: '2026-03-01T00:00:00Z',
    clan: 100,
    clan_name: 'Test Clan',
    clan_tag: 'TEST',
    clan_id: 100,
    verdict: null,
    efficiency_json: [],
    ranked_json: [],
    randoms_json: [],
};

describe('PlayerRouteView tab warmup smoke', () => {
    let consoleErrorSpy: jest.SpyInstance;
    let resolvePlayerRoute: ((value: { data: typeof playerRoutePayload; headers: Record<string, string | null>; }) => void) | null;

    beforeEach(() => {
        jest.useFakeTimers();
        pushMock.mockReset();
        trackEntityDetailViewMock.mockReset();
        mockUseClanMembers.mockReturnValue({ members: [], loading: false, error: null });
        mockClanSvg.mockReset();
        resolvePlayerRoute = null;
        consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => undefined);
        mockFetchSharedJson.mockReset();
        mockFetchSharedJson.mockImplementation((url) => {
            if (url === '/api/player/Player%20One/') {
                return new Promise((resolve) => {
                    resolvePlayerRoute = resolve as typeof resolvePlayerRoute;
                });
            }

            if (url === '/api/fetch/player_correlation/tier_type/77/') {
                return Promise.resolve({ data: profileChartPayload, headers: {} });
            }

            return Promise.resolve({ data: [], headers: {} });
        });
    });

    afterEach(() => {
        consoleErrorSpy.mockRestore();
        jest.runOnlyPendingTimers();
        jest.useRealTimers();
    });

    it('waits for the routed player payload before warming tab data in the background', async () => {
        render(<PlayerRouteView playerName="Player One" />);

        expect(screen.getByText('Loading player profile...')).toBeInTheDocument();
        expect(mockFetchSharedJson).toHaveBeenCalledWith('/api/player/Player%20One/', {
            label: 'Player Player One',
            ttlMs: 1500,
        });

        await act(async () => {
            jest.advanceTimersByTime(250);
        });

        expect(mockFetchSharedJson).toHaveBeenCalledTimes(1);

        await act(async () => {
            resolvePlayerRoute?.({ data: playerRoutePayload, headers: {} });
        });

        expect(await screen.findByText('Player One')).toBeInTheDocument();

        await act(async () => {
            jest.advanceTimersByTime(250);
        });

        await waitFor(() => {
            expect(mockFetchSharedJson).toHaveBeenCalledWith('/api/fetch/player_correlation/ranked_wr_battles/77/', expect.objectContaining({ ttlMs: 30000 }));
        });

        expect(mockFetchSharedJson).toHaveBeenCalledWith('/api/fetch/ranked_data/77/', expect.objectContaining({ ttlMs: 30000, cacheKey: 'ranked-data:77:0:0' }));
        expect(mockFetchSharedJson).toHaveBeenCalledWith('/api/fetch/player_correlation/tier_type/77/', expect.objectContaining({ ttlMs: 30000 }));
        expect(mockFetchSharedJson).not.toHaveBeenCalledWith('/api/fetch/type_data/77/', expect.anything());
        expect(mockFetchSharedJson).not.toHaveBeenCalledWith('/api/fetch/tier_data/77/', expect.anything());
        expect(mockFetchSharedJson).toHaveBeenCalledWith('/api/fetch/player_clan_battle_seasons/77/', expect.objectContaining({ ttlMs: 30000 }));
        expect(mockFetchSharedJson).not.toHaveBeenCalledWith('/api/fetch/randoms_data/77/?all=true', expect.anything());
    });
});