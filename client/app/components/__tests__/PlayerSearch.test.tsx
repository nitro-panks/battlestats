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
    recentClans = [],
    recentPlayers = [],
    recentPlayersResponses,
    playersByMode = defaultPlayersByMode,
    playerResponses = {},
}: {
    clans?: unknown[];
    recentClans?: unknown[];
    recentPlayers?: unknown[];
    recentPlayersResponses?: unknown[][];
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
            return Promise.resolve(buildJsonResponse(clans));
        }

        if (url === '/api/landing/recent-clans/' || url === '/api/landing/recent-clans') {
            return Promise.resolve(buildJsonResponse(recentClans));
        }

        if (url === '/api/landing/recent/' || url === '/api/landing/recent') {
            if (recentPlayersQueue && recentPlayersQueue.length > 0) {
                return Promise.resolve(buildJsonResponse(recentPlayersQueue.shift() ?? []));
            }
            return Promise.resolve(buildJsonResponse(recentPlayers));
        }

        if (url.startsWith('/api/landing/players/') || url.startsWith('/api/landing/players?')) {
            const mode = new URL(url, 'http://localhost').searchParams.get('mode') || 'random';
            return Promise.resolve(buildJsonResponse(playersByMode[mode] ?? []));
        }

        if (url.startsWith('/api/player/')) {
            const playerName = decodeURIComponent(url.replace('/api/player/', '').replace(/\/$/, ''));
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
    return function MockLandingClanSVG() {
        return <div data-testid="landing-clan-svg" />;
    };
});

describe('PlayerSearch landing efficiency icon', () => {
    beforeEach(() => {
        pushMock.mockReset();
        capturedPlayerDetailProps.current = null;
        mockQueryParam = '';
        jest.useRealTimers();
        installFetchMock();
    });

    afterEach(() => {
        jest.useRealTimers();
    });

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
                ([url]) => url === '/api/landing/players/?mode=sigma&limit=40' || url === '/api/landing/players?mode=sigma&limit=40',
            )).toBe(true);
        });

        const sigmaLeaderRow = await screen.findByRole('button', { name: /Show player SigmaLeader/i });
        const sigmaRunnerUpRow = screen.getByRole('button', { name: /Show player SigmaRunnerUp/i });

        expect(within(sigmaLeaderRow).getByText('Σ')).toBeInTheDocument();
        expect(within(sigmaRunnerUpRow).queryByText('Σ')).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Sigma' })).toHaveAttribute('aria-pressed', 'true');
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

        expect(pushMock).toHaveBeenNthCalledWith(1, '/player/AcePlayer');
        expect(pushMock).toHaveBeenNthCalledWith(2, '/clan/501-clanalpha');
        expect(screen.getByLabelText('HiddenSkipper has hidden stats')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Show player HiddenSkipper/i })).not.toBeInTheDocument();
    });

    it('filters clan best mode by thresholds and tie-breaks by total battles', async () => {
        installFetchMock({
            clans: [
                {
                    clan_id: 900,
                    name: 'Great Clan',
                    tag: 'GREAT',
                    members_count: 40,
                    clan_wr: 58.1,
                    total_battles: 120000,
                    active_members: 12,
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
                    clan_id: 902,
                    name: 'Beta Clan',
                    tag: 'BETA',
                    members_count: 40,
                    clan_wr: 57.4,
                    total_battles: 220000,
                    active_members: 17,
                },
                {
                    clan_id: 903,
                    name: 'Low Volume Clan',
                    tag: 'LOW',
                    members_count: 40,
                    clan_wr: 61.0,
                    total_battles: 90000,
                    active_members: 20,
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
        });

        render(<PlayerSearch />);

        fireEvent.click((await screen.findAllByRole('button', { name: 'Best' }))[0]);

        await waitFor(() => {
            const clanButtons = screen.getAllByRole('button', { name: /Show clan /i });
            expect(clanButtons.map((button) => button.getAttribute('title'))).toEqual(['GREAT', 'BETA', 'ALPHA']);
        });

        expect(screen.queryByRole('button', { name: /Show clan Low Volume Clan/i })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Show clan Inactive Clan/i })).not.toBeInTheDocument();
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

        expect(await screen.findByRole('button', { name: /Show recent player for_the_kingdom_2022/i })).toBeInTheDocument();

        Object.defineProperty(document, 'visibilityState', {
            configurable: true,
            value: 'visible',
        });

        act(() => {
            document.dispatchEvent(new Event('visibilitychange'));
        });

        expect(await screen.findByRole('button', { name: /Show recent player AnotherCaptain/i })).toBeInTheDocument();
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
            ([url]) => url === '/api/player/Hydrated%20Player',
        )).toBe(true);
        expect((global.fetch as jest.Mock).mock.calls.filter(([url]) => url === '/api/player/Hydrated%20Player')).toHaveLength(2);
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

        expect(screen.getByText(/Best_clan ≈ WR × I\(Battles ≥ 100k\) × I\(ActiveShare ≥ 0\.30\), tie → Battles/i)).toBeInTheDocument();
        expect(screen.queryByText(/Current clan cache refreshes in about/i)).not.toBeInTheDocument();
        expect(infoButton).toBeInTheDocument();
    });
});