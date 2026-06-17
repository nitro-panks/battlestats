import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import PlayerSearch from '../PlayerSearch';

const pushMock = jest.fn();
const trackEventMock = jest.fn();
const capturedPlayerDetailProps: { current: null | Record<string, unknown> } = { current: null };

const buildJsonResponse = (payload: unknown) => ({
    ok: true,
    status: 200,
    headers: {
        get: (name: string) => name.toLowerCase() === 'content-type' ? 'application/json' : null,
    },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
});

const buildErrorResponse = (status: number, body = 'missing') => ({
    ok: false,
    status,
    headers: {
        get: (name: string) => name.toLowerCase() === 'content-type' ? 'text/html' : null,
    },
    text: async () => body,
});

const buildPlayerPayload = (overrides: Record<string, unknown> = {}) => ({
    id: 1,
    name: 'Player One',
    player_id: 77,
    kill_ratio: 1.5,
    actual_kdr: 1.8,
    player_score: 980,
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
    ...overrides,
});

const defaultClans = [
    {
        clan_id: 501,
        name: 'ClanAlpha',
        tag: 'ALPHA',
        members_count: 40,
        clan_wr: 57.4,
        total_battles: 180000,
        active_members: 18,
    },
];

const defaultPlayersByBestSort = {
    overall: [
        {
            name: 'BestPlayer',
            pvp_ratio: 64.1,
            is_hidden: false,
            is_streamer: true,
            is_ranked_player: true,
            is_pve_player: true,
            is_sleepy_player: false,
            is_clan_battle_player: true,
            clan_battle_win_rate: 58.4,
            highest_ranked_league: 'Gold',
            efficiency_rank_percentile: 0.99,
            efficiency_rank_tier: 'E',
            has_efficiency_rank_icon: true,
            efficiency_rank_population_size: 367,
            efficiency_rank_updated_at: '2026-03-17T00:00:00Z',
        },
        {
            name: 'BestRunnerUp',
            pvp_ratio: 57.2,
            is_hidden: false,
            is_ranked_player: false,
            is_pve_player: false,
            is_sleepy_player: false,
            is_clan_battle_player: false,
            clan_battle_win_rate: null,
            highest_ranked_league: null,
            efficiency_rank_percentile: 0.91,
            efficiency_rank_tier: 'I',
            has_efficiency_rank_icon: true,
            efficiency_rank_population_size: 367,
            efficiency_rank_updated_at: '2026-03-17T00:00:00Z',
        },
    ],
    efficiency: [
        {
            name: 'SigmaLeader',
            pvp_ratio: 59.8,
            is_hidden: false,
            is_ranked_player: false,
            is_pve_player: false,
            is_sleepy_player: false,
            is_clan_battle_player: false,
            clan_battle_win_rate: null,
            highest_ranked_league: null,
            efficiency_rank_percentile: 0.97,
            efficiency_rank_tier: 'E',
            has_efficiency_rank_icon: true,
            efficiency_rank_population_size: 367,
            efficiency_rank_updated_at: '2026-03-17T00:00:00Z',
        },
        {
            name: 'SigmaRunnerUp',
            pvp_ratio: 57.2,
            is_hidden: false,
            is_ranked_player: false,
            is_pve_player: false,
            is_sleepy_player: false,
            is_clan_battle_player: false,
            clan_battle_win_rate: null,
            highest_ranked_league: null,
            efficiency_rank_percentile: 0.91,
            efficiency_rank_tier: 'I',
            has_efficiency_rank_icon: true,
            efficiency_rank_population_size: 367,
            efficiency_rank_updated_at: '2026-03-17T00:00:00Z',
        },
    ],
    ranked: [],
    wr: [],
    cb: [],
};

const installFetchMock = ({
    clans = defaultClans,
    clansByBestSort,
    playersByBestSort = defaultPlayersByBestSort,
    playersByBestSortResponses,
    playerResponses = {},
}: {
    clans?: unknown[];
    clansByBestSort?: Record<string, unknown[]>;
    playersByBestSort?: Record<string, unknown[]>;
    playersByBestSortResponses?: Record<string, unknown[][]>;
    playerResponses?: Record<string, Array<ReturnType<typeof buildJsonResponse> | ReturnType<typeof buildErrorResponse>>>;
} = {}) => {
    const responseQueues = new Map(
        Object.entries(playerResponses).map(([playerName, queue]) => [playerName, [...queue]]),
    );
    const bestPlayerQueues = playersByBestSortResponses
        ? new Map(Object.entries(playersByBestSortResponses).map(([sort, queue]) => [sort, [...queue]]))
        : null;

    global.fetch = jest.fn((input: RequestInfo | URL) => {
        const url = input.toString();

        if (url.startsWith('/api/landing/clans/') || url.startsWith('/api/landing/clans?')) {
            const params = new URL(url, 'http://localhost').searchParams;
            const sort = params.get('sort') || 'overall';
            const payload = clansByBestSort ? (clansByBestSort[sort] ?? []) : clans;
            return Promise.resolve(buildJsonResponse(payload));
        }

        if (url.startsWith('/api/landing/warm-best')) {
            return Promise.resolve(buildJsonResponse({ status: 'queued' }));
        }

        if (url.startsWith('/api/landing/players/') || url.startsWith('/api/landing/players?')) {
            const params = new URL(url, 'http://localhost').searchParams;
            const sort = params.get('sort') || 'overall';
            if (bestPlayerQueues) {
                const queue = bestPlayerQueues.get(sort);
                if (queue && queue.length > 0) {
                    return Promise.resolve(buildJsonResponse(queue.shift() ?? []));
                }
            }
            return Promise.resolve(buildJsonResponse((playersByBestSort ?? defaultPlayersByBestSort)[sort] ?? []));
        }

        if (url.startsWith('/api/player/')) {
            const strippedPath = url.replace(/\?.*$/, '');
            const playerName = decodeURIComponent(strippedPath.replace('/api/player/', '').replace(/\/$/, ''));
            const queue = responseQueues.get(playerName);
            if (!queue || queue.length === 0) {
                return Promise.reject(new Error(`Unexpected player fetch: ${playerName}`));
            }

            return Promise.resolve(queue.shift() as ReturnType<typeof buildJsonResponse>);
        }

        return Promise.reject(new Error(`Unexpected fetch: ${url}`));
    }) as jest.Mock;
};

jest.mock('next/navigation', () => ({
    useRouter: () => ({
        push: pushMock,
    }),
}));

jest.mock('../PlayerDetail', () => {
    return function MockPlayerDetail(props: Record<string, unknown>) {
        capturedPlayerDetailProps.current = props;
        const player = props.player as { name?: string; clan_name?: string | null };
        return (
            <div data-testid="player-detail">
                <span>{player?.name}</span>
                <span>{player?.clan_name || 'No clan yet'}</span>
            </div>
        );
    };
});

jest.mock('../ClanDetail', () => {
    return function MockClanDetail() {
        return <div data-testid="clan-detail" />;
    };
});

jest.mock('../LandingClanSVG', () => {
    return function MockLandingClanSVG(props: { clans?: Array<{ name?: string }> }) {
        return <div data-testid="landing-clan-svg" data-clan-count={props.clans?.length ?? 0} />;
    };
});

jest.mock('../../lib/umami', () => ({
    trackEvent: (...args: unknown[]) => trackEventMock(...args),
}));

describe('PlayerSearch landing (Best-only)', () => {
    let consoleErrorSpy: jest.SpyInstance;

    beforeEach(() => {
        pushMock.mockReset();
        trackEventMock.mockReset();
        capturedPlayerDetailProps.current = null;
        window.history.replaceState({}, '', '/');
        jest.useRealTimers();
        installFetchMock();
        consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    });

    afterEach(() => {
        consoleErrorSpy.mockRestore();
        jest.useRealTimers();
    });

    const getClanToolbar = async () => {
        const heading = await screen.findByRole('heading', { name: 'Active Clans' });
        return heading.parentElement as HTMLElement;
    };
    const getPlayerToolbar = async () => {
        const heading = await screen.findByRole('heading', { name: 'Players' });
        return heading.parentElement as HTMLElement;
    };

    it('tracks landing-best-sort for the player toolbar without any mode toggle', async () => {
        render(<PlayerSearch />);
        await waitFor(() => {
            expect(screen.getByRole('heading', { name: 'Players' })).toBeInTheDocument();
        });

        // Best is the only filter — there is no landing-filter mode toggle to fire.
        const rankedSort = within(screen.getByTestId('player-best-sort-bar'))
            .getByRole('button', { name: 'Ranked' });
        fireEvent.click(rankedSort);
        expect(trackEventMock).toHaveBeenCalledWith(
            'landing-best-sort', expect.objectContaining({ entity: 'player', sort: 'ranked' }));
        expect(trackEventMock).not.toHaveBeenCalledWith(
            'landing-filter', expect.anything());

        // Re-clicking the already-active sub-sort does not double-fire.
        trackEventMock.mockReset();
        fireEvent.click(rankedSort);
        expect(trackEventMock).not.toHaveBeenCalledWith(
            'landing-best-sort', expect.objectContaining({ entity: 'player', sort: 'ranked' }));
    });

    it('tracks landing-best-sort for the clan toolbar', async () => {
        render(<PlayerSearch />);

        const wrSort = within(await screen.findByTestId('clan-best-sort-bar'))
            .getByRole('button', { name: 'WR' });
        fireEvent.click(wrSort);
        expect(trackEventMock).toHaveBeenCalledWith(
            'landing-best-sort', expect.objectContaining({ entity: 'clan', sort: 'wr' }));
        expect(trackEventMock).not.toHaveBeenCalledWith(
            'landing-filter', expect.anything());
    });

    it('renders the sigma only for Expert landing rows while preserving existing landing icons', async () => {
        render(<PlayerSearch />);

        const expertRow = await screen.findByRole('button', { name: /Show player BestPlayer/i });
        const nonExpertRow = screen.getByRole('button', { name: /Show player BestRunnerUp/i });

        expect(within(expertRow).getByText('Σ')).toBeInTheDocument();
        expect(within(expertRow).getByLabelText(/known streamer/i)).toBeInTheDocument();
        expect(within(expertRow).getByLabelText(/Battlestats efficiency rank Expert: 99th percentile among eligible tracked players\. Based on stored WG badge profile for 367 tracked players\./i)).toBeInTheDocument();
        expect(within(expertRow).getByLabelText(/ranked enjoyer \(Gold\)/i)).toBeInTheDocument();
        expect(within(expertRow).getByLabelText(/pve enjoyer/i)).toBeInTheDocument();
        expect(within(expertRow).getByLabelText(/clan battle enjoyer 58\.4 percent WR/i)).toBeInTheDocument();
        expect(within(nonExpertRow).queryByText('Σ')).not.toBeInTheDocument();
    });

    it('fires landing-player-click when a landing player row is clicked', async () => {
        render(<PlayerSearch />);
        const row = await screen.findByRole('button', { name: /Show player BestPlayer/i });
        trackEventMock.mockReset();

        fireEvent.click(row);

        expect(trackEventMock).toHaveBeenCalledWith(
            'landing-player-click', expect.objectContaining({ realm: expect.any(String) }));
    });

    it('fires landing-clan-click when a landing clan row is clicked', async () => {
        render(<PlayerSearch />);
        const row = await screen.findByRole('button', { name: /Show clan ClanAlpha/i });
        trackEventMock.mockReset();

        fireEvent.click(row);

        expect(trackEventMock).toHaveBeenCalledWith(
            'landing-clan-click', expect.objectContaining({ realm: expect.any(String) }));
    });

    it('shows Best as the only player filter with no Recent toggle and fetches best players on mount', async () => {
        render(<PlayerSearch />);

        const playerToolbar = within(await getPlayerToolbar());
        // Best is rendered as a static selected pill, not a toggle button.
        expect(playerToolbar.getByTestId('player-best-pill')).toHaveTextContent('Best');
        expect(playerToolbar.queryByRole('button', { name: 'Best' })).not.toBeInTheDocument();
        // No Recent control anywhere.
        expect(screen.queryByRole('button', { name: 'Recent' })).not.toBeInTheDocument();

        // Best players are fetched on mount (no toolbar interaction required).
        await waitFor(() => {
            expect((global.fetch as jest.Mock).mock.calls.some(
                ([url]) => typeof url === 'string' && url.startsWith('/api/landing/players') && url.includes('mode=best'),
            )).toBe(true);
        });
        // The /api/landing/recent endpoints are never called.
        expect((global.fetch as jest.Mock).mock.calls.some(
            ([url]) => typeof url === 'string' && url.includes('/api/landing/recent'),
        )).toBe(false);
    });

    it('renders only the supported best-sort controls for players and clans', async () => {
        render(<PlayerSearch />);

        await screen.findByTestId('player-best-sort-bar');
        await screen.findByTestId('clan-best-sort-bar');

        expect(within(screen.getByTestId('player-best-sort-bar')).queryByRole('button', { name: 'ABS' })).not.toBeInTheDocument();
        expect(within(screen.getByTestId('clan-best-sort-bar')).queryByRole('button', { name: 'ABS' })).not.toBeInTheDocument();
        expect(within(screen.getByTestId('clan-best-sort-bar')).queryByRole('button', { name: 'CB' })).not.toBeInTheDocument();
    });

    it('player and clan best sub-sort bars are always visible (Best is the only mode)', async () => {
        render(<PlayerSearch />);

        const playerSortBar = await screen.findByTestId('player-best-sort-bar');
        const clanSortBar = await screen.findByTestId('clan-best-sort-bar');

        expect(playerSortBar).not.toHaveAttribute('aria-hidden', 'true');
        expect(clanSortBar).not.toHaveAttribute('aria-hidden', 'true');
        expect(within(playerSortBar).getByRole('button', { name: 'Overall' })).toBeInTheDocument();
        expect(within(clanSortBar).getByRole('button', { name: 'Overall' })).toBeInTheDocument();
    });

    it('moves Efficiency under Best and switches the landing request to the efficiency sub-sort', async () => {
        render(<PlayerSearch />);

        await waitFor(() => {
            expect(screen.getByRole('button', { name: 'Efficiency' })).toBeInTheDocument();
        });

        fireEvent.click(screen.getByRole('button', { name: 'Efficiency' }));

        await waitFor(() => {
            expect((global.fetch as jest.Mock).mock.calls.some(
                ([url]) => url === '/api/landing/players?mode=best&limit=25&sort=efficiency&realm=na',
            )).toBe(true);
        });

        const sigmaLeaderRow = await screen.findByRole('button', { name: /Show player SigmaLeader/i });
        const sigmaRunnerUpRow = screen.getByRole('button', { name: /Show player SigmaRunnerUp/i });

        expect(within(sigmaLeaderRow).getByText('Σ')).toBeInTheDocument();
        expect(within(sigmaRunnerUpRow).queryByText('Σ')).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Efficiency' })).toHaveClass('text-[var(--accent-mid)]');
    });

    it('preserves backend best player ordering on the landing page', async () => {
        installFetchMock({
            playersByBestSort: {
                ...defaultPlayersByBestSort,
                overall: [
                    {
                        name: 'ZedTop',
                        pvp_ratio: 51.0,
                        is_hidden: false,
                        is_ranked_player: false,
                        is_pve_player: false,
                        is_sleepy_player: false,
                        is_clan_battle_player: false,
                        clan_battle_win_rate: null,
                        highest_ranked_league: null,
                        efficiency_rank_percentile: 0.72,
                        efficiency_rank_tier: 'II',
                        has_efficiency_rank_icon: true,
                        efficiency_rank_population_size: 367,
                        efficiency_rank_updated_at: '2026-03-17T00:00:00Z',
                    },
                    {
                        name: 'AlphaSecond',
                        pvp_ratio: 66.0,
                        is_hidden: false,
                        is_ranked_player: true,
                        is_pve_player: false,
                        is_sleepy_player: false,
                        is_clan_battle_player: true,
                        clan_battle_win_rate: 61.2,
                        highest_ranked_league: 'Gold',
                        efficiency_rank_percentile: 0.99,
                        efficiency_rank_tier: 'E',
                        has_efficiency_rank_icon: true,
                        efficiency_rank_population_size: 367,
                        efficiency_rank_updated_at: '2026-03-17T00:00:00Z',
                    },
                    {
                        name: 'MiddleThird',
                        pvp_ratio: 58.0,
                        is_hidden: false,
                        is_ranked_player: false,
                        is_pve_player: false,
                        is_sleepy_player: false,
                        is_clan_battle_player: false,
                        clan_battle_win_rate: null,
                        highest_ranked_league: null,
                        efficiency_rank_percentile: 0.83,
                        efficiency_rank_tier: 'I',
                        has_efficiency_rank_icon: true,
                        efficiency_rank_population_size: 367,
                        efficiency_rank_updated_at: '2026-03-17T00:00:00Z',
                    },
                ],
            },
        });

        render(<PlayerSearch />);

        await waitFor(() => {
            const playerButtons = screen.getAllByRole('button', { name: /Show player /i });
            expect(playerButtons.map((button) => button.getAttribute('aria-label'))).toEqual([
                'Show player ZedTop',
                'Show player AlphaSecond',
                'Show player MiddleThird',
            ]);
        });

        expect((global.fetch as jest.Mock).mock.calls.some(
            ([url]) => url === '/api/landing/players?mode=best&limit=25&sort=overall&realm=na',
        )).toBe(true);
    });

    it('requests 30 clans while keeping player landing requests at 25', async () => {
        render(<PlayerSearch />);

        await waitFor(() => {
            expect(screen.getByRole('heading', { name: 'Active Clans' })).toBeInTheDocument();
        });

        await waitFor(() => {
            expect((global.fetch as jest.Mock).mock.calls.some(
                ([url]) => url === '/api/landing/clans?mode=best&limit=30&sort=overall&realm=na',
            )).toBe(true);
        });

        await waitFor(() => {
            expect((global.fetch as jest.Mock).mock.calls.some(
                ([url]) => url === '/api/landing/players?mode=best&limit=25&sort=overall&realm=na',
            )).toBe(true);
        });
    });

    it('shows the clan warm-up notice over an empty board when best clans are empty', async () => {
        installFetchMock({ clans: [] });

        render(<PlayerSearch />);

        expect(await screen.findByText(/Best clan rankings are still warming up for this realm\./i)).toBeInTheDocument();
        // No recent fallback list — the board is empty behind the notice.
        expect(screen.queryAllByRole('button', { name: /Show clan /i })).toHaveLength(0);
        expect(screen.getByTestId('landing-clan-svg')).toHaveAttribute('data-clan-count', '0');
    });

    it('queues a best landing warmup once on page load', async () => {
        render(<PlayerSearch />);

        await waitFor(() => {
            expect((global.fetch as jest.Mock).mock.calls.some(
                ([url]) => url === '/api/landing/warm-best?realm=na',
            )).toBe(true);
        });

        const warmupCalls = (global.fetch as jest.Mock).mock.calls.filter(
            ([url]) => url === '/api/landing/warm-best?realm=na',
        );
        expect(warmupCalls).toHaveLength(1);
    });

    it('routes visible landing rows to canonical routes while keeping hidden rows non-interactive', async () => {
        installFetchMock({
            playersByBestSort: {
                ...defaultPlayersByBestSort,
                overall: [
                    {
                        name: 'AcePlayer',
                        pvp_ratio: 61.2,
                        is_hidden: false,
                        is_streamer: true,
                        is_ranked_player: true,
                        is_pve_player: true,
                        is_sleepy_player: false,
                        is_clan_battle_player: true,
                        clan_battle_win_rate: 58.4,
                        highest_ranked_league: 'Gold',
                        efficiency_rank_percentile: 0.97,
                        efficiency_rank_tier: 'E',
                        has_efficiency_rank_icon: true,
                        efficiency_rank_population_size: 367,
                        efficiency_rank_updated_at: '2026-03-17T00:00:00Z',
                    },
                    {
                        name: 'HiddenSkipper',
                        pvp_ratio: 48.4,
                        is_hidden: true,
                        is_ranked_player: false,
                        is_pve_player: false,
                        is_sleepy_player: true,
                        is_clan_battle_player: false,
                        clan_battle_win_rate: null,
                        highest_ranked_league: null,
                        efficiency_rank_percentile: null,
                        efficiency_rank_tier: null,
                        has_efficiency_rank_icon: false,
                        efficiency_rank_population_size: null,
                        efficiency_rank_updated_at: null,
                    },
                ],
            },
        });

        render(<PlayerSearch />);

        const playerButton = await screen.findByRole('button', { name: /Show player AcePlayer/i });
        fireEvent.click(playerButton);
        fireEvent.click(screen.getByRole('button', { name: /Show clan ClanAlpha/i }));

        expect(pushMock).toHaveBeenNthCalledWith(1, '/player/AcePlayer?realm=na');
        expect(pushMock).toHaveBeenNthCalledWith(2, '/clan/501-clanalpha?realm=na');
        expect(screen.getByLabelText('HiddenSkipper has hidden stats')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Show player HiddenSkipper/i })).not.toBeInTheDocument();
    });

    it('preserves backend best clan ordering without client-side filtering', async () => {
        installFetchMock({
            clansByBestSort: {
                overall: [
                    {
                        clan_id: 903,
                        name: 'Low Volume Clan',
                        tag: 'LOW',
                        members_count: 40,
                        clan_wr: 61.0,
                        total_battles: 40000,
                        active_members: 20,
                    },
                    {
                        clan_id: 901,
                        name: 'Alpha Clan',
                        tag: 'ALPHA',
                        members_count: 40,
                        clan_wr: 57.4,
                        total_battles: 180000,
                        active_members: 18,
                    },
                    {
                        clan_id: 904,
                        name: 'Inactive Clan',
                        tag: 'SLEEP',
                        members_count: 40,
                        clan_wr: 60.0,
                        total_battles: 180000,
                        active_members: 10,
                    },
                ],
            },
        });

        render(<PlayerSearch />);

        await waitFor(() => {
            const clanButtons = screen.getAllByRole('button', { name: /Show clan /i });
            expect(clanButtons.map((button) => button.getAttribute('title'))).toEqual(['LOW', 'ALPHA', 'SLEEP']);
        });

        expect(screen.getByRole('button', { name: /Show clan Low Volume Clan/i })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Show clan Alpha Clan/i })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Show clan Inactive Clan/i })).toBeInTheDocument();
        expect(screen.getByTestId('landing-clan-svg')).toHaveAttribute('data-clan-count', '3');
    });

    it('renders the clan battle enjoyers badge from the backend-owned clan payload flag', async () => {
        installFetchMock({
            clansByBestSort: {
                overall: [
                    {
                        clan_id: 905,
                        name: 'CB Active Clan',
                        tag: 'CBAC',
                        members_count: 40,
                        clan_wr: 56.0,
                        total_battles: 180000,
                        active_members: 18,
                        is_clan_battle_active: true,
                    },
                    {
                        clan_id: 906,
                        name: 'Quiet Clan',
                        tag: 'QUIET',
                        members_count: 40,
                        clan_wr: 54.0,
                        total_battles: 175000,
                        active_members: 17,
                        is_clan_battle_active: false,
                    },
                ],
            },
        });

        render(<PlayerSearch />);

        const activeClanButton = await screen.findByRole('button', { name: /Show clan CB Active Clan/i });
        const quietClanButton = screen.getByRole('button', { name: /Show clan Quiet Clan/i });

        expect(within(activeClanButton).getByLabelText(/clan battle enjoyers/i)).toBeInTheDocument();
        expect(within(quietClanButton).queryByLabelText(/clan battle enjoyers/i)).not.toBeInTheDocument();
    });

    it('requests backend-owned clan best sub-sorts and renders returned order directly', async () => {
        installFetchMock({
            clansByBestSort: {
                overall: [
                    { clan_id: 801, name: 'Overall One', tag: 'OV1', members_count: 40, clan_wr: 56.2, total_battles: 180000, active_members: 17 },
                    { clan_id: 802, name: 'Overall Two', tag: 'OV2', members_count: 40, clan_wr: 55.1, total_battles: 170000, active_members: 16 },
                ],
                wr: [
                    { clan_id: 811, name: 'WR First', tag: 'WR1', members_count: 40, clan_wr: 63.0, total_battles: 165000, active_members: 14, avg_cb_wr: 70.0 },
                    { clan_id: 812, name: 'WR Second', tag: 'WR2', members_count: 40, clan_wr: 61.0, total_battles: 150000, active_members: 13, avg_cb_wr: 66.0 },
                ],
            },
        });

        render(<PlayerSearch />);

        await screen.findByRole('button', { name: /Show clan Overall One/i });

        fireEvent.click(within(screen.getByTestId('clan-best-sort-bar')).getByRole('button', { name: 'WR' }));

        await waitFor(() => {
            const clanButtons = screen.getAllByRole('button', { name: /Show clan /i });
            expect(clanButtons.map((button) => button.getAttribute('title'))).toEqual(['WR1', 'WR2']);
        });

        expect((global.fetch as jest.Mock).mock.calls.some(
            ([url]) => url === '/api/landing/clans?mode=best&limit=30&sort=wr&realm=na',
        )).toBe(true);
    });

    it('loads player detail from the q parameter and returns to landing on back', async () => {
        window.history.replaceState({}, '', '/?q=Player%20One');
        installFetchMock({
            playerResponses: {
                'Player One': [buildJsonResponse(buildPlayerPayload())],
            },
        });

        render(<PlayerSearch />);

        expect(await screen.findByTestId('player-detail')).toBeInTheDocument();
        expect(screen.getByText('Player One')).toBeInTheDocument();

        const props = capturedPlayerDetailProps.current as { onBack: () => void };
        act(() => {
            props.onBack();
        });

        await waitFor(() => {
            expect(screen.queryByTestId('player-detail')).not.toBeInTheDocument();
        });
        expect(screen.getByRole('heading', { name: 'Players' })).toBeInTheDocument();
    });

    it('refreshes best players when the page becomes visible again', async () => {
        installFetchMock({
            playersByBestSortResponses: {
                overall: [
                    [{ name: 'for_the_kingdom_2022', pvp_ratio: 54.2, is_hidden: false }],
                    [
                        { name: 'AnotherCaptain', pvp_ratio: 58.1, is_hidden: false },
                        { name: 'for_the_kingdom_2022', pvp_ratio: 54.2, is_hidden: false },
                    ],
                ],
            },
        });

        render(<PlayerSearch />);

        expect(await screen.findByRole('button', { name: /Show player for_the_kingdom_2022/i })).toBeInTheDocument();

        Object.defineProperty(document, 'visibilityState', {
            configurable: true,
            value: 'visible',
        });

        act(() => {
            document.dispatchEvent(new Event('visibilitychange'));
        });

        expect(await screen.findByRole('button', { name: /Show player AnotherCaptain/i })).toBeInTheDocument();
    });

    it('executes nav search events and shows an error when player lookup fails', async () => {
        installFetchMock({
            playerResponses: {
                'Missing Player': [buildErrorResponse(404)],
            },
        });

        render(<PlayerSearch />);

        await screen.findByRole('heading', { name: 'Players' });

        act(() => {
            window.dispatchEvent(new CustomEvent('navSearch', {
                detail: { query: 'Missing Player' },
            }));
        });

        expect(await screen.findByText('Player not found')).toBeInTheDocument();
    });

    it('polls hydrated clan data until a clan name appears', async () => {
        jest.useFakeTimers();
        installFetchMock({
            playerResponses: {
                'Hydrated Player': [
                    buildJsonResponse(buildPlayerPayload({
                        name: 'Hydrated Player',
                        clan_id: 100,
                        clan_name: null,
                        clan_tag: null,
                    })),
                    buildJsonResponse(buildPlayerPayload({
                        name: 'Hydrated Player',
                        clan_id: 100,
                        clan_name: 'Hydrated Clan',
                        clan_tag: 'HYD',
                    })),
                ],
            },
        });

        render(<PlayerSearch />);

        await screen.findByRole('heading', { name: 'Players' });

        act(() => {
            window.dispatchEvent(new CustomEvent('navSearch', {
                detail: { query: 'Hydrated Player' },
            }));
        });

        expect(await screen.findByText('No clan yet')).toBeInTheDocument();

        await act(async () => {
            jest.advanceTimersByTime(2500);
        });

        await waitFor(() => {
            expect(screen.getByText('Hydrated Clan')).toBeInTheDocument();
        });
        expect((global.fetch as jest.Mock).mock.calls.some(
            ([url]) => url === '/api/player/Hydrated%20Player?realm=na',
        )).toBe(true);
        expect((global.fetch as jest.Mock).mock.calls.filter(([url]) => url === '/api/player/Hydrated%20Player?realm=na')).toHaveLength(2);
    });

    it('shows the best formula tooltip without cache timing copy', async () => {
        render(<PlayerSearch />);

        const infoButton = await screen.findByRole('button', {
            name: 'Best player ranking formula details',
        });

        expect(screen.getByText(/Best ≈ \(0\.40·WR_5-10 \+ 0\.22·Score \+ 0\.18·Eff \+ 0\.10·Vol_5-10 \+ 0\.06·Ranked \+ 0\.04·Clan\) × M_share/i)).toBeInTheDocument();
        expect(screen.queryByText(/^ABS$/i)).not.toBeInTheDocument();
        expect(screen.queryByText(/Current random cache refreshes in about/i)).not.toBeInTheDocument();
        expect(infoButton).toBeInTheDocument();
    });

    it('shows the clan best tooltip without cache timing copy', async () => {
        render(<PlayerSearch />);

        const infoButton = await screen.findByRole('button', {
            name: 'Clan ranking formula details',
        });

        expect(screen.getByText(/Overall ≈ 0\.30·WR \+ 0\.25·Activity \+ 0\.20·MemberScore \+ 0\.15·CB \+ 0\.10·log\(Battles\)/i)).toBeInTheDocument();
        expect(screen.queryByText(/^ABS$/i)).not.toBeInTheDocument();
        expect(screen.queryByText(/Current clan cache refreshes in about/i)).not.toBeInTheDocument();
        expect(infoButton).toBeInTheDocument();
    });
});
