import React from 'react';
import { render, screen, waitFor, act } from '@testing-library/react';
import BattleHistoryCard, { type BattleHistoryPayload } from '../BattleHistoryCard';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
}));

const mockFetchSharedJson = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

const buildPayload = (overrides: Partial<BattleHistoryPayload> = {}): BattleHistoryPayload => ({
    window_days: 7,
    available_modes: ['random'],
    as_of: '2026-04-28T18:30:00Z',
    totals: {
        battles: 8,
        wins: 5,
        losses: 3,
        win_rate: 62.5,
        damage: 382_400,
        avg_damage: 47_800,
        frags: 15,
        xp: 21_400,
        planes_killed: 0,
        survived_battles: 4,
        survival_rate: 50.0,
    },
    by_ship: [
        {
            ship_id: 42,
            ship_name: 'Yamato',
            ship_tier: 10,
            ship_type: 'Battleship',
            battles: 6,
            wins: 4,
            losses: 2,
            win_rate: 66.7,
            damage: 287_400,
            avg_damage: 47_900,
            frags: 12,
            xp: 16_400,
            planes_killed: 0,
            survived_battles: 3,
        },
        {
            ship_id: 43,
            ship_name: 'Dalian',
            ship_tier: 9,
            ship_type: 'Destroyer',
            battles: 2,
            wins: 1,
            losses: 1,
            win_rate: 50.0,
            damage: 95_000,
            avg_damage: 47_500,
            frags: 3,
            xp: 5_000,
            planes_killed: 0,
            survived_battles: 1,
        },
    ],
    by_day: [
        { date: '2026-04-27', battles: 3, wins: 2, damage: 142_300, frags: 6 },
        { date: '2026-04-28', battles: 5, wins: 3, damage: 240_100, frags: 9 },
    ],
    ...overrides,
});

const resolveWith = (payload: BattleHistoryPayload) => {
    mockFetchSharedJson.mockResolvedValueOnce({ data: payload, headers: {} });
};

describe('BattleHistoryCard', () => {
    beforeEach(() => {
        mockFetchSharedJson.mockReset();
    });

    test('renders the totals row, sparkline, and per-ship table once the API resolves', async () => {
        resolveWith(buildPayload());
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);

        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });

        expect(screen.getByText(/Last 7 days/i)).toBeInTheDocument();
        // Two ships present, sorted Yamato first.
        const rows = screen.getAllByRole('row');
        // 1 header + 2 data rows.
        expect(rows.length).toBe(3);
        expect(rows[1].textContent).toContain('Yamato');
        expect(rows[2].textContent).toContain('Dalian');
        // Win-rate cell renders the percentage with one decimal.
        expect(screen.getByText('66.7%')).toBeInTheDocument();
        expect(screen.getByText('50.0%')).toBeInTheDocument();
        // Sparkline is intentionally hidden (kept in component code for future
        // re-enable). Confirm it is NOT rendered.
        expect(screen.queryByLabelText(/Win-rate trend across the period/i)).not.toBeInTheDocument();
    });

    test('renders nothing while loading', () => {
        // Never resolve.
        mockFetchSharedJson.mockReturnValueOnce(new Promise(() => {}));
        const { container } = render(<BattleHistoryCard playerName="x" realm="na" />);
        expect(container).toBeEmptyDOMElement();
    });

    test('renders nothing when totals.battles is zero', async () => {
        resolveWith(buildPayload({
            totals: {
                battles: 0, wins: 0, losses: 0, win_rate: 0,
                damage: 0, avg_damage: 0, frags: 0, xp: 0,
                planes_killed: 0, survived_battles: 0, survival_rate: 0,
            },
            by_ship: [],
            by_day: [],
        }));
        const { container } = render(<BattleHistoryCard playerName="empty" realm="na" />);
        // Wait for the fetch to settle; the component should stay empty.
        await waitFor(() => {
            expect(mockFetchSharedJson).toHaveBeenCalled();
        });
        await new Promise((r) => setTimeout(r, 0));
        expect(container).toBeEmptyDOMElement();
    });

    test('renders nothing when the API returns 404 (capture API disabled)', async () => {
        mockFetchSharedJson.mockRejectedValueOnce(new Error('404 not found'));
        const { container } = render(<BattleHistoryCard playerName="x" realm="na" />);
        await waitFor(() => {
            expect(mockFetchSharedJson).toHaveBeenCalled();
        });
        await new Promise((r) => setTimeout(r, 0));
        expect(container).toBeEmptyDOMElement();
    });

    test('passes period + windows + realm to the URL on initial daily fetch', () => {
        mockFetchSharedJson.mockReturnValueOnce(new Promise(() => {}));
        render(<BattleHistoryCard playerName="lil_boots" realm="eu" days={14} />);
        expect(mockFetchSharedJson).toHaveBeenCalledTimes(1);
        const [url] = mockFetchSharedJson.mock.calls[0];
        expect(url).toContain('/api/player/lil_boots/battle-history/');
        expect(url).toContain('period=daily');
        expect(url).toContain('windows=14');
        expect(url).toContain('realm=eu');
    });

    test('initial fetch uses mode=random (default)', () => {
        mockFetchSharedJson.mockReturnValueOnce(new Promise(() => {}));
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        const [url] = mockFetchSharedJson.mock.calls[0];
        expect(url).toContain('mode=random');
    });

    test('hides mode pills when player has only random data', async () => {
        resolveWith(buildPayload({ available_modes: ['random'] }));
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        // No mode pill row at all — single available mode is implicit.
        expect(screen.queryByRole('group', { name: /battle mode/i })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /^Ranked$/ })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /^All$/ })).not.toBeInTheDocument();
    });

    test('defaults to Ranked + hides Random/All when player has only ranked data', async () => {
        // Initial fetch returns mode=random with available_modes=['ranked'].
        // The card auto-switches mode and refetches; second response is ranked.
        mockFetchSharedJson.mockResolvedValueOnce({
            data: buildPayload({
                mode: 'random',
                available_modes: ['ranked'],
                totals: {
                    battles: 0, wins: 0, losses: 0, win_rate: 0,
                    damage: 0, avg_damage: 0, frags: 0, xp: 0,
                    planes_killed: 0, survived_battles: 0, survival_rate: 0,
                },
                by_ship: [], by_day: [],
            }),
            headers: {},
        });
        mockFetchSharedJson.mockResolvedValueOnce({
            data: buildPayload({
                mode: 'ranked',
                available_modes: ['ranked'],
                totals: {
                    battles: 12, wins: 8, losses: 4, win_rate: 66.7,
                    damage: 480_000, avg_damage: 40_000, frags: 18,
                    xp: 7_200, planes_killed: 0, survived_battles: 8,
                    survival_rate: 66.7,
                },
            }),
            headers: {},
        });
        render(<BattleHistoryCard playerName="ranked_only" realm="na" />);
        // Wait for the second fetch (auto-mode-switch) to land.
        await waitFor(() => {
            expect(mockFetchSharedJson.mock.calls.length).toBeGreaterThanOrEqual(2);
        });
        const lastUrl = mockFetchSharedJson.mock.calls[1][0] as string;
        expect(lastUrl).toContain('mode=ranked');
        // No pill row (single visible mode → group hidden).
        expect(screen.queryByRole('group', { name: /battle mode/i })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /^Random$/ })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /^All$/ })).not.toBeInTheDocument();
    });

    test('renders mode pill row with three options + defaults to All when both modes available', async () => {
        // Initial fetch returns dual-mode availability → auto-switch to
        // combined ('All') fires, triggering a second fetch.
        resolveWith(buildPayload({ available_modes: ['random', 'ranked'] }));
        resolveWith(buildPayload({ available_modes: ['random', 'ranked'], mode: 'combined' }));
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        // Wait for the auto-switch refetch to complete.
        await waitFor(() => {
            expect(mockFetchSharedJson.mock.calls.length).toBeGreaterThanOrEqual(2);
        });
        const group = screen.getByRole('group', { name: /battle mode/i });
        expect(group).toBeInTheDocument();
        const random = screen.getByRole('button', { name: /^Random$/ });
        const ranked = screen.getByRole('button', { name: /^Ranked$/ });
        const all = screen.getByRole('button', { name: /^All$/ });
        // New default: All is pressed when both modes are available.
        expect(random).toHaveAttribute('aria-pressed', 'false');
        expect(ranked).toHaveAttribute('aria-pressed', 'false');
        expect(all).toHaveAttribute('aria-pressed', 'true');
        // The auto-switch fetch should have requested mode=combined.
        const secondUrl = mockFetchSharedJson.mock.calls[1][0] as string;
        expect(secondUrl).toContain('mode=combined');
    });

    test('clicking ranked pill refetches with mode=ranked', async () => {
        // Dual-mode payload triggers auto-switch to combined (refetch).
        resolveWith(buildPayload({ available_modes: ['random', 'ranked'] }));
        resolveWith(buildPayload({ available_modes: ['random', 'ranked'], mode: 'combined' }));
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        await waitFor(() => {
            expect(mockFetchSharedJson.mock.calls.length).toBeGreaterThanOrEqual(2);
        });
        const initialCalls = mockFetchSharedJson.mock.calls.length;
        resolveWith(buildPayload({ available_modes: ['random', 'ranked'], mode: 'ranked' }));
        await act(async () => {
            screen.getByRole('button', { name: /^Ranked$/ }).click();
        });
        await waitFor(() => {
            expect(mockFetchSharedJson.mock.calls.length).toBe(initialCalls + 1);
        });
        const lastUrl = mockFetchSharedJson.mock.calls[initialCalls][0] as string;
        expect(lastUrl).toContain('mode=ranked');
    });

    test('polls when X-Ranked-Observation-Pending is true on a ranked-mode response', async () => {
        jest.useFakeTimers();
        try {
            // Initial fetch with dual modes → auto-switch to combined.
            mockFetchSharedJson.mockResolvedValueOnce({
                data: buildPayload({ available_modes: ['random', 'ranked'] }),
                headers: {},
            });
            mockFetchSharedJson.mockResolvedValueOnce({
                data: buildPayload({ available_modes: ['random', 'ranked'], mode: 'combined' }),
                headers: {},
            });
            render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
            await waitFor(() => {
                expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
            });
            await waitFor(() => {
                expect(mockFetchSharedJson.mock.calls.length).toBeGreaterThanOrEqual(2);
            });

            // Switch to ranked: first response is pending; second is fresh.
            mockFetchSharedJson.mockResolvedValueOnce({
                data: buildPayload({ available_modes: ['random', 'ranked'], mode: 'ranked' }),
                headers: { 'X-Ranked-Observation-Pending': 'true' },
            });
            mockFetchSharedJson.mockResolvedValueOnce({
                data: buildPayload({
                    available_modes: ['random', 'ranked'],
                    mode: 'ranked',
                    totals: {
                        battles: 25, wins: 18, losses: 7, win_rate: 72.0,
                        damage: 900_000, avg_damage: 36_000, frags: 30,
                        xp: 11_000, planes_killed: 0, survived_battles: 18,
                        survival_rate: 72.0,
                    },
                }),
                headers: {},
            });
            const callsBefore = mockFetchSharedJson.mock.calls.length;
            await act(async () => {
                screen.getByRole('button', { name: /^Ranked$/ }).click();
            });
            // First ranked fetch landed (pending header set).
            await waitFor(() => {
                expect(mockFetchSharedJson.mock.calls.length).toBe(callsBefore + 1);
            });
            // Advance the polling delay; the second fetch fires.
            await act(async () => {
                jest.advanceTimersByTime(2100);
            });
            await waitFor(() => {
                expect(mockFetchSharedJson.mock.calls.length).toBe(callsBefore + 2);
            });
        } finally {
            jest.useRealTimers();
        }
    });

    test('renders empty state with pill row when ranked mode has zero data', async () => {
        // Initial dual-mode fetch → auto-switch to combined.
        resolveWith(buildPayload({ available_modes: ['random', 'ranked'] }));
        resolveWith(buildPayload({ available_modes: ['random', 'ranked'], mode: 'combined' }));
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        await waitFor(() => {
            expect(mockFetchSharedJson.mock.calls.length).toBeGreaterThanOrEqual(2);
        });
        // Switch to ranked: zero battles → card stays visible with pill so
        // user can switch back.
        resolveWith(buildPayload({
            available_modes: ['random', 'ranked'],
            mode: 'ranked',
            totals: {
                battles: 0, wins: 0, losses: 0, win_rate: 0,
                damage: 0, avg_damage: 0, frags: 0, xp: 0,
                planes_killed: 0, survived_battles: 0, survival_rate: 0,
            },
            by_ship: [],
            by_day: [],
        }));
        await act(async () => {
            screen.getByRole('button', { name: /^Ranked$/ }).click();
        });
        await waitFor(() => {
            expect(screen.getByText(/No ranked battles in this window/i)).toBeInTheDocument();
        });
        // Pills still reachable.
        expect(screen.getByRole('button', { name: /^Random$/ })).toBeInTheDocument();
    });
});
