import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import PlayerSearch from '../PlayerSearch';

const pushMock = jest.fn();
const capturedPlayerDetailProps: { current: null | Record<string, unknown> } = { current: null };
let mockQueryParam = '';

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

const defaultPlayersByMode = {
    random: [
        {
            name: 'AcePlayer',
            pvp_ratio: 61.2,
            is_hidden: false,
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
            name: 'SolidPlayer',
            pvp_ratio: 55.6,
            is_hidden: false,
            is_ranked_player: false,
            is_pve_player: false,
            is_sleepy_player: false,
            is_clan_battle_player: false,
            clan_battle_win_rate: null,
            highest_ranked_league: null,
            efficiency_rank_percentile: 0.81,
            efficiency_rank_tier: 'II',
            has_efficiency_rank_icon: true,
            efficiency_rank_population_size: 124,
            efficiency_rank_updated_at: '2026-03-17T00:00:00Z',
        },
    ],
    best: [
        {
            name: 'BestPlayer',
            pvp_ratio: 64.1,
            is_hidden: false,
            is_ranked_player: true,
            is_pve_player: false,
            is_sleepy_player: false,
            is_clan_battle_player: true,
            clan_battle_win_rate: 60.2,
            highest_ranked_league: 'Typhoon',
            efficiency_rank_percentile: 0.99,
            efficiency_rank_tier: 'E',
            has_efficiency_rank_icon: true,
            efficiency_rank_population_size: 367,
            efficiency_rank_updated_at: '2026-03-17T00:00:00Z',
        },
    ],
    sigma: [
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
};

const installFetchMock = ({
    clans = defaultClans,
    clansByBestSort,
    recentClans = [],
    recentPlayers = [],
    recentPlayersResponses,
    recentClansResponse,
    playersByMode = defaultPlayersByMode,
    playerResponses = {},
}: {
    clans?: unknown[];
    clansByBestSort?: Record<string, unknown[]>;
    recentClans?: unknown[];
    recentPlayers?: unknown[];
    recentPlayersResponses?: unknown[][];
    recentClansResponse?: ReturnType<typeof buildJsonResponse> | ReturnType<typeof buildErrorResponse>;
    playersByMode?: Record<string, unknown[]>;
    playerResponses?: Record<string, Array<ReturnType<typeof buildJsonResponse> | ReturnType<typeof buildErrorResponse>>>;
} = {}) => {
    const responseQueues = new Map(
        Object.entries(playerResponses).map(([playerName, queue]) => [playerName, [...queue]]),
    );
    const recentPlayersQueue = recentPlayersResponses ? [...recentPlayersResponses] : null;

    global.fetch = jest.fn((input: RequestInfo | URL) => {
        const url = input.toString();

        if (url.startsWith('/api/landing/clans/') || url.startsWith('/api/landing/clans?')) {
            const params = new URL(url, 'http://localhost').searchParams;
            const mode = params.get('mode') || 'random';
            const sort = params.get('sort') || 'overall';
            const payload = mode === 'best' && clansByBestSort ? (clansByBestSort[sort] ?? []) : clans;
            return Promise.resolve(buildJsonResponse(payload));
        }

        if (url.startsWith('/api/landing/recent-clans')) {
            if (recentClansResponse) {
                return Promise.resolve(recentClansResponse);
            }
            return Promise.resolve(buildJsonResponse(recentClans));
        }

        if (url.startsWith('/api/landing/recent')) {
            if (recentPlayersQueue && recentPlayersQueue.length > 0) {
                return Promise.resolve(buildJsonResponse(recentPlayersQueue.shift() ?? []));
            }
            return Promise.resolve(buildJsonResponse(recentPlayers));
        }

        if (url.startsWith('/api/landing/warm-best')) {
            return Promise.resolve(buildJsonResponse({ status: 'queued' }));
        }

        if (url.startsWith('/api/landing/players/') || url.startsWith('/api/landing/players?')) {
            const mode = new URL(url, 'http://localhost').searchParams.get('mode') || 'random';
            return Promise.resolve(buildJsonResponse(playersByMode[mode] ?? []));
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
    useSearchParams: () => ({
        get: (key: string) => key === 'q' && mockQueryParam ? mockQueryParam : null,
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

describe('PlayerSearch landing efficiency icon', () => {
    let consoleErrorSpy: jest.SpyInstance;

    beforeEach(() => {
        pushMock.mockReset();
        capturedPlayerDetailProps.current = null;
        mockQueryParam = '';
        jest.useRealTimers();
        installFetchMock();
        consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    });

    afterEach(() => {
        consoleErrorSpy.mockRestore();
        jest.useRealTimers();
    });

    const getClanRecentButton = async () => (await screen.findAllByRole('button', { name: 'Recent' }))[0];
    const getPlayerRecentButton = async () => (await screen.findAllByRole('button', { name: 'Recent' }))[1];

    it('renders the sigma only for Expert landing rows while preserving existing landing icons', async () => {
        render(<PlayerSearch />);

        await waitFor(() => {
            expect(screen.getByRole('heading', { name: 'Active Players' })).toBeInTheDocument();
        });

        const expertRow = screen.getByRole('button', { name: /Show player AcePlayer/i });
        const nonExpertRow = screen.getByRole('button', { name: /Show player SolidPlayer/i });

        expect(within(expertRow).getByText('Σ')).toBeInTheDocument();
        expect(within(expertRow).getByLabelText(/Battlestats efficiency rank Expert: 97th percentile among eligible tracked players\. Based on stored WG badge profile for 367 tracked players\./i)).toBeInTheDocument();
        expect(within(expertRow).getByLabelText(/ranked enjoyer \(Gold\)/i)).toBeInTheDocument();
        expect(within(expertRow).getByLabelText(/pve enjoyer/i)).toBeInTheDocument();
        expect(within(expertRow).getByLabelText(/clan battle enjoyer 58\.4 percent WR/i)).toBeInTheDocument();
        expect(within(nonExpertRow).queryByText('Σ')).not.toBeInTheDocument();
    });

    it('adds a Sigma filter button and switches the landing request to sigma mode', async () => {
        render(<PlayerSearch />);

        await waitFor(() => {
            expect(screen.getByRole('button', { name: 'Sigma' })).toBeInTheDocument();
        });

        fireEvent.click(screen.getByRole('button', { name: 'Sigma' }));

        await waitFor(() => {
            expect((global.fetch as jest.Mock).mock.calls.some(
                ([url]) => url === '/api/landing/players?mode=sigma&limit=25&realm=na',
            )).toBe(true);
        });

        const sigmaLeaderRow = await screen.findByRole('button', { name: /Show player SigmaLeader/i });
        const sigmaRunnerUpRow = screen.getByRole('button', { name: /Show player SigmaRunnerUp/i });

        expect(within(sigmaLeaderRow).getByText('Σ')).toBeInTheDocument();
        expect(within(sigmaRunnerUpRow).queryByText('Σ')).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Sigma' })).toHaveAttribute('aria-pressed', 'true');
    });

    it('preserves backend best player ordering on the landing page', async () => {
        installFetchMock({
            playersByMode: {
                ...defaultPlayersByMode,
                best: [
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

        fireEvent.click((await screen.findAllByRole('button', { name: 'Best' }))[1]);

        await waitFor(() => {
            const playerButtons = screen.getAllByRole('button', { name: /Show player /i });
            expect(playerButtons.map((button) => button.getAttribute('aria-label'))).toEqual([
                'Show player ZedTop',
                'Show player AlphaSecond',
                'Show player MiddleThird',
            ]);
        });

        expect((global.fetch as jest.Mock).mock.calls.some(
            ([url]) => url === '/api/landing/players?mode=best&limit=25&realm=na',
        )).toBe(true);
    });

    it('folds recent players into the player mode switch after Sigma', async () => {
        installFetchMock({
            recentPlayers: [
                { name: 'RecentCaptain', pvp_ratio: 58.1, is_hidden: false },
            ],
        });

        render(<PlayerSearch />);

        const sigmaButton = await screen.findByRole('button', { name: 'Sigma' });
        const recentButton = await getPlayerRecentButton();
        expect(sigmaButton.compareDocumentPosition(recentButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

        fireEvent.click(recentButton);

        expect(await screen.findByRole('button', { name: /Show player RecentCaptain/i })).toBeInTheDocument();
        expect(screen.queryByText('Recently Viewed')).not.toBeInTheDocument();
        expect(await getPlayerRecentButton()).toHaveAttribute('aria-pressed', 'true');
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

        expect((global.fetch as jest.Mock).mock.calls.some(
            ([url]) => url === '/api/landing/players?mode=random&limit=25&realm=na',
        )).toBe(true);
    });

    it('falls back to recent clans when best clan results are empty', async () => {
        installFetchMock({
            clans: [],
            recentClans: [
                {
                    clan_id: 902,
                    name: 'FallbackClan',
                    tag: 'FALL',
                    members_count: 32,
                    clan_wr: 54.2,
                    total_battles: 120000,
                    active_members: 16,
                },
            ],
        });

        render(<PlayerSearch />);

        const bestClanButton = (await screen.findAllByRole('button', { name: 'Best' }))[0];
        fireEvent.click(bestClanButton);

        expect(await screen.findByText(/Best clan rankings are still warming up for this realm\./i)).toBeInTheDocument();
        expect(await screen.findByRole('button', { name: /Show clan FallbackClan/i })).toBeInTheDocument();
        expect(screen.getByTestId('landing-clan-svg')).toHaveAttribute('data-clan-count', '1');
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

    it('still queues the best landing warmup when recent landing data fails', async () => {
        installFetchMock({
            recentClansResponse: buildErrorResponse(500, 'boom'),
        });

        render(<PlayerSearch />);

        await waitFor(() => {
            expect((global.fetch as jest.Mock).mock.calls.some(
                ([url]) => url === '/api/landing/warm-best?realm=na',
            )).toBe(true);
        });

        await waitFor(() => {
            expect((global.fetch as jest.Mock).mock.calls.some(
                ([url]) => url === '/api/landing/recent-clans?realm=na',
            )).toBe(true);
        });
    });

    it('routes visible landing rows to canonical routes while keeping hidden rows non-interactive', async () => {
        installFetchMock({
            playersByMode: {
                ...defaultPlayersByMode,
                random: [
                    ...defaultPlayersByMode.random,
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

        fireEvent.click((await screen.findAllByRole('button', { name: 'Best' }))[0]);

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
                cb: [
                    { clan_id: 821, name: 'CB First', tag: 'CB1', members_count: 40, clan_wr: 54.0, total_battles: 160000, active_members: 18, avg_cb_battles: 80, avg_cb_wr: 65.0 },
                    { clan_id: 822, name: 'CB Second', tag: 'CB2', members_count: 40, clan_wr: 53.0, total_battles: 155000, active_members: 17, avg_cb_battles: 72, avg_cb_wr: 62.0 },
                ],
            },
        });

        render(<PlayerSearch />);

        await screen.findByRole('button', { name: /Show clan Overall One/i });

        fireEvent.click(screen.getByRole('button', { name: 'WR' }));

        await waitFor(() => {
            const clanButtons = screen.getAllByRole('button', { name: /Show clan /i });
            expect(clanButtons.map((button) => button.getAttribute('title'))).toEqual(['WR1', 'WR2']);
        });

        fireEvent.click(screen.getByRole('button', { name: 'CB' }));

        await waitFor(() => {
            const clanButtons = screen.getAllByRole('button', { name: /Show clan /i });
            expect(clanButtons.map((button) => button.getAttribute('title'))).toEqual(['CB1', 'CB2']);
        });

        expect((global.fetch as jest.Mock).mock.calls.some(
            ([url]) => url === '/api/landing/clans?mode=best&limit=30&sort=wr&realm=na',
        )).toBe(true);
        expect((global.fetch as jest.Mock).mock.calls.some(
            ([url]) => url === '/api/landing/clans?mode=best&limit=30&sort=cb&realm=na',
        )).toBe(true);
    });

    it('folds recent clans into the clan mode switch with a Recent button', async () => {
        installFetchMock({
            clans: defaultClans,
            recentClans: [
                {
                    clan_id: 777,
                    name: 'RecentClan',
                    tag: 'REC',
                    members_count: 35,
                    clan_wr: 54.3,
                    total_battles: 92000,
                    active_members: 14,
                },
            ],
        });

        render(<PlayerSearch />);

        fireEvent.click(await getClanRecentButton());

        expect(await screen.findByRole('button', { name: /Show clan RecentClan/i })).toBeInTheDocument();
        expect(screen.queryByText('Recently Viewed Clans')).not.toBeInTheDocument();
        expect(await getClanRecentButton()).toHaveAttribute('aria-pressed', 'true');
    });

    it('keeps the clan best sub-sort bar mounted to avoid header layout jumps', async () => {
        render(<PlayerSearch />);

        const sortBar = await screen.findByTestId('clan-best-sort-bar');
        expect(sortBar).toHaveAttribute('aria-hidden', 'false');

        fireEvent.click(await getClanRecentButton());

        expect(screen.getByTestId('clan-best-sort-bar-shell')).toBeInTheDocument();
        expect(screen.getByTestId('clan-best-sort-bar')).toHaveAttribute('aria-hidden', 'true');
    });

    it('shows the recent clan empty state inside the shared clan surface', async () => {
        installFetchMock({
            recentClans: [],
        });

        render(<PlayerSearch />);

        fireEvent.click(await getClanRecentButton());

        expect(await getClanRecentButton()).toHaveAttribute('aria-pressed', 'true');
        expect(screen.queryAllByRole('button', { name: /Show clan /i })).toHaveLength(0);
    });

    it('loads player detail from the q parameter and returns to landing on back', async () => {
        mockQueryParam = 'Player One';
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
        expect(screen.getByRole('heading', { name: 'Active Players' })).toBeInTheDocument();
    });

    it('refreshes recently viewed players when the page becomes visible again', async () => {
        installFetchMock({
            recentPlayersResponses: [
                [{ name: 'for_the_kingdom_2022', pvp_ratio: 54.2, is_hidden: false }],
                [
                    { name: 'AnotherCaptain', pvp_ratio: 58.1, is_hidden: false },
                    { name: 'for_the_kingdom_2022', pvp_ratio: 54.2, is_hidden: false },
                ],
            ],
        });

        render(<PlayerSearch />);

        fireEvent.click(await getPlayerRecentButton());

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

    it('shows the recent player empty state inside the shared player surface', async () => {
        installFetchMock({
            recentPlayers: [],
        });

        render(<PlayerSearch />);

        fireEvent.click(await getPlayerRecentButton());

        expect(await getPlayerRecentButton()).toHaveAttribute('aria-pressed', 'true');
        expect(screen.queryAllByRole('button', { name: /Show player /i })).toHaveLength(0);
    });

    it('executes nav search events and shows an error when player lookup fails', async () => {
        installFetchMock({
            playerResponses: {
                'Missing Player': [buildErrorResponse(404)],
            },
        });

        render(<PlayerSearch />);

        await screen.findByRole('heading', { name: 'Active Players' });

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

        await screen.findByRole('heading', { name: 'Active Players' });

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
            name: 'Best ranking formula details',
        });

        expect(screen.getByText(/Best ≈ \(0\.40·WR_5-10 \+ 0\.22·Score \+ 0\.18·Eff \+ 0\.10·Vol_5-10 \+ 0\.06·Ranked \+ 0\.04·Clan\) × M_share/i)).toBeInTheDocument();
        expect(screen.getByText(/Player detail now shows literal KDR separately, but Best still uses the composite score rather than overall KDR directly/i)).toBeInTheDocument();
        expect(screen.queryByText(/Current random cache refreshes in about/i)).not.toBeInTheDocument();
        expect(infoButton).toBeInTheDocument();
    });

    it('shows the clan best tooltip without cache timing copy', async () => {
        render(<PlayerSearch />);

        const infoButton = await screen.findByRole('button', {
            name: 'Clan ranking formula details',
        });

        expect(screen.getByText(/Overall ≈ 0\.30·WR \+ 0\.25·Activity \+ 0\.20·MemberScore \+ 0\.15·CB \+ 0\.10·log\(Battles\)/i)).toBeInTheDocument();
        expect(screen.queryByText(/Current clan cache refreshes in about/i)).not.toBeInTheDocument();
        expect(infoButton).toBeInTheDocument();
    });
});