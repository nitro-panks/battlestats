import React from 'react';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import BattleHistoryCard, {
    type BattleHistoryPayload,
    type BattleHistoryByDay,
    battleHistoryCacheKey,
    battleHistoryFetchUrl,
    buildWindowedDays,
    prefetchBattleHistory,
    BATTLE_HISTORY_FETCH_TTL_MS,
} from '../BattleHistoryCard';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
}));

const mockTrackEvent = jest.fn();
jest.mock('../../lib/umami', () => ({
    trackEvent: (...args: unknown[]) => mockTrackEvent(...args),
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

// URL/mode-aware mock. The card fires TWO fetches per (window, mode): the main
// window fetch and the always-month sparkline fetch (second useEffect). A fixed
// mockResolvedValueOnce queue misaligns when the sparkline call consumes a
// response meant for the main fetch, so for multi-mode tests we drive responses
// off the request's ?mode= instead. `base` applies to every response; `perMode`
// overrides specific modes; `makeHeaders` optionally sets per-request headers.
const mockByMode = (
    base: Partial<BattleHistoryPayload>,
    perMode: Partial<Record<string, Partial<BattleHistoryPayload>>> = {},
    makeHeaders?: (params: URLSearchParams) => Record<string, string>,
) => {
    mockFetchSharedJson.mockImplementation((url: string) => {
        const params = new URL(url, 'http://t').searchParams;
        const mode = (params.get('mode') ?? 'random') as BattleHistoryPayload['mode'];
        return Promise.resolve({
            data: buildPayload({ ...base, mode, ...(perMode[mode as string] ?? {}) }),
            headers: makeHeaders ? makeHeaders(params) : {},
        });
    });
};

// Main (non-sparkline) fetch calls — identified by label, since the main window
// now defaults to 'month' and shares the same url as the always-month sparkline
// fetch (the sparkline uses label 'BattleHistoryCard:sparkline'). Optionally
// filtered by mode. Lets assertions target the main fetch without depending on
// call order/count.
const mainFetchCalls = (mode?: string): unknown[] =>
    mockFetchSharedJson.mock.calls.filter((c) => {
        const label = (c[1] as { label?: string } | undefined)?.label ?? '';
        const u = c[0] as string;
        return label.startsWith('BattleHistoryCard:')
            && label !== 'BattleHistoryCard:sparkline'
            && (mode ? u.includes(`mode=${mode}`) : true);
    });

describe('BattleHistoryCard', () => {
    beforeEach(() => {
        mockFetchSharedJson.mockReset();
        mockTrackEvent.mockReset();
        // Default response for the always-month sparkline fetch (second useEffect call).
        // Individual tests override the main window fetch via resolveWith().
        mockFetchSharedJson.mockResolvedValue({ data: buildPayload({ by_day: [] }), headers: {} });
    });

    test('renders the totals row, sparkline, and per-ship table once the API resolves', async () => {
        resolveWith(buildPayload());
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);

        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });

        expect(screen.getByText(/Last 30 days/i)).toBeInTheDocument();
        // Two ships present, sorted Yamato first.
        const rows = screen.getAllByRole('row');
        // 1 header + 2 data rows.
        expect(rows.length).toBe(3);
        expect(rows[1].textContent).toContain('Yamato');
        expect(rows[2].textContent).toContain('Dalian');
        // Win-rate cell renders the percentage with one decimal.
        expect(screen.getByText('66.7%')).toBeInTheDocument();
        expect(screen.getByText('50.0%')).toBeInTheDocument();
        expect(screen.getByLabelText(/30-day battle activity/i)).toBeInTheDocument();
    });

    test('splits Win Rate into sortable WR/S (session) and WR/O (overall + delta) columns', async () => {
        resolveWith(buildPayload({
            by_ship: [
                {
                    ship_id: 42, ship_name: 'Yamato', ship_tier: 10, ship_type: 'Battleship',
                    battles: 6, wins: 4, losses: 2, win_rate: 66.7,
                    lifetime_win_rate: 55.0, delta_win_rate: 11.7,
                    damage: 287_400, avg_damage: 47_900, frags: 12, xp: 16_400,
                    planes_killed: 0, survived_battles: 3,
                },
                {
                    ship_id: 43, ship_name: 'Dalian', ship_tier: 9, ship_type: 'Destroyer',
                    battles: 2, wins: 1, losses: 1, win_rate: 50.0,
                    lifetime_win_rate: 60.0, delta_win_rate: -10.0,
                    damage: 95_000, avg_damage: 47_500, frags: 3, xp: 5_000,
                    planes_killed: 0, survived_battles: 1,
                },
            ],
        }));
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });

        // The single "Win Rate" column is now two distinct sortable columns.
        expect(screen.getByRole('columnheader', { name: /WR\/S/ })).toBeInTheDocument();
        expect(screen.getByRole('columnheader', { name: /WR\/O/ })).toBeInTheDocument();
        expect(screen.queryByRole('columnheader', { name: /^Win Rate$/i })).not.toBeInTheDocument();

        // Session WR (WR/S), overall WR (WR/O), and the delta all render.
        expect(screen.getByText('66.7%')).toBeInTheDocument();   // Yamato session
        expect(screen.getByText('55.0%')).toBeInTheDocument();   // Yamato overall
        expect(screen.getByText('Δ+11.7%')).toBeInTheDocument();
        expect(screen.getByText('Δ-10.0%')).toBeInTheDocument();

        // Default sort is battles desc → Yamato (6) before Dalian (2).
        expect(screen.getAllByRole('row')[1].textContent).toContain('Yamato');

        // Sort by overall WR (WR/O), desc → Dalian (60.0) above Yamato (55.0).
        fireEvent.click(screen.getByText('WR/O'));
        let rows = screen.getAllByRole('row');
        expect(rows[1].textContent).toContain('Dalian');
        expect(rows[2].textContent).toContain('Yamato');

        // Session WR (WR/S) sorts independently, desc → Yamato (66.7) above Dalian (50.0).
        fireEvent.click(screen.getByText('WR/S'));
        rows = screen.getAllByRole('row');
        expect(rows[1].textContent).toContain('Yamato');
        expect(rows[2].textContent).toContain('Dalian');
    });

    test('stays mounted with prior data during a refreshNonce rehydrate (no blink/reflow)', async () => {
        resolveWith(buildPayload());
        const { rerender } = render(
            <BattleHistoryCard playerName="lil_boots" realm="na" refreshNonce={0} />,
        );
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        expect(screen.getByText('Yamato')).toBeInTheDocument();

        // The live-update rehydrate bumps refreshNonce → a re-fetch starts. Keep
        // that fetch in flight (never resolves) to hold the component in its
        // loading state, then assert the card did NOT unmount — the old data
        // stays on screen so there's no disappear/reappear blink or layout shift.
        mockFetchSharedJson.mockReturnValueOnce(new Promise<never>(() => {}));
        rerender(<BattleHistoryCard playerName="lil_boots" realm="na" refreshNonce={1} />);

        expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        expect(screen.getByText('Yamato')).toBeInTheDocument();
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

    test('embedded: renders chrome (not null) at the pristine-empty default instead of collapsing', async () => {
        // Same zero-battle payload that hides the standalone card — embedded it
        // must render the "no battles" chrome so the active Activity tab is never
        // blank.
        resolveWith(buildPayload({
            totals: {
                battles: 0, wins: 0, losses: 0, win_rate: 0,
                damage: 0, avg_damage: 0, frags: 0, xp: 0,
                planes_killed: 0, survived_battles: 0, survival_rate: 0,
            },
            by_ship: [],
            by_day: [],
        }));
        render(<BattleHistoryCard embedded playerName="empty" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        expect(screen.getByText(/no random battles in this window/i)).toBeInTheDocument();
    });

    test('embedded: reports availability false for a zero-battle, random-only player', async () => {
        const onAvailabilityChange = jest.fn();
        resolveWith(buildPayload({
            available_modes: ['random'],
            totals: {
                battles: 0, wins: 0, losses: 0, win_rate: 0,
                damage: 0, avg_damage: 0, frags: 0, xp: 0,
                planes_killed: 0, survived_battles: 0, survival_rate: 0,
            },
            by_ship: [],
            by_day: [],
        }));
        render(
            <BattleHistoryCard
                embedded
                playerName="empty"
                realm="na"
                onAvailabilityChange={onAvailabilityChange}
            />,
        );
        await waitFor(() => {
            expect(onAvailabilityChange).toHaveBeenCalledWith(false);
        });
    });

    test('embedded: reports availability true when the player has battles', async () => {
        const onAvailabilityChange = jest.fn();
        resolveWith(buildPayload());
        render(
            <BattleHistoryCard
                embedded
                playerName="active"
                realm="na"
                onAvailabilityChange={onAvailabilityChange}
            />,
        );
        await waitFor(() => {
            expect(onAvailabilityChange).toHaveBeenCalledWith(true);
        });
    });

    test('embedded: reports availability true for a ranked-only player with zero random battles', async () => {
        const onAvailabilityChange = jest.fn();
        // First (default random) fetch reports ranked availability with no random
        // battles — the player still has activity (the card auto-switches mode).
        mockByMode({ available_modes: ['ranked'] }, {
            ranked: {},
            random: {
                totals: {
                    battles: 0, wins: 0, losses: 0, win_rate: 0,
                    damage: 0, avg_damage: 0, frags: 0, xp: 0,
                    planes_killed: 0, survived_battles: 0, survival_rate: 0,
                },
                by_ship: [],
                by_day: [],
            },
        });
        render(
            <BattleHistoryCard
                embedded
                playerName="rankedonly"
                realm="na"
                onAvailabilityChange={onAvailabilityChange}
            />,
        );
        await waitFor(() => {
            expect(onAvailabilityChange).toHaveBeenCalledWith(true);
        });
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

    test('initial fetch uses window=month (default) + realm', () => {
        mockFetchSharedJson.mockReturnValue(new Promise(() => {}));
        render(<BattleHistoryCard playerName="lil_boots" realm="eu" />);
        // The card fires the main window fetch plus the always-month sparkline
        // fetch; the main fetch is the window=month one.
        const url = mockFetchSharedJson.mock.calls
            .map((c) => c[0] as string)
            .find((u) => u.includes('window=month'));
        expect(url).toBeDefined();
        expect(url).toContain('/api/player/lil_boots/battle-history/');
        expect(url).toContain('window=month');
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
        // Ranked-only player: every response reports available_modes=['ranked'],
        // so the initial mode=random fetch triggers an auto-switch + refetch to
        // mode=ranked.
        mockByMode({ available_modes: ['ranked'] }, {
            ranked: {
                totals: {
                    battles: 12, wins: 8, losses: 4, win_rate: 66.7,
                    damage: 480_000, avg_damage: 40_000, frags: 18,
                    xp: 7_200, planes_killed: 0, survived_battles: 8,
                    survival_rate: 66.7,
                },
            },
        });
        render(<BattleHistoryCard playerName="ranked_only" realm="na" />);
        // The auto-mode-switch refetches the main window with mode=ranked.
        await waitFor(() => {
            expect(mainFetchCalls('ranked').length).toBeGreaterThanOrEqual(1);
        });
        // No interactive pill row (nothing to switch to), but a static
        // "Ranked" label so the card isn't silently misread as Random.
        expect(screen.queryByRole('group', { name: /battle mode/i })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /^Random$/ })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /^All$/ })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /^Ranked$/ })).not.toBeInTheDocument();
        expect(screen.getByText(/^Ranked$/)).toBeInTheDocument();
    });

    test('labels the ranked header with the season name when provided', async () => {
        // Ranked is current-season-scoped server-side, so the header reads the
        // season (e.g. "Season 29") instead of the date-window label.
        mockByMode({ available_modes: ['ranked'], ranked_season_name: 'Season 29' }, {
            ranked: {
                totals: {
                    battles: 12, wins: 8, losses: 4, win_rate: 66.7,
                    damage: 480_000, avg_damage: 40_000, frags: 18,
                    xp: 7_200, planes_killed: 0, survived_battles: 8,
                    survival_rate: 66.7, lifetime_battles: 40,
                    lifetime_win_rate: 60.0,
                },
            },
        });
        render(<BattleHistoryCard playerName="ranked_only" realm="na" />);
        await waitFor(() => {
            expect(mainFetchCalls('ranked').length).toBeGreaterThanOrEqual(1);
        });
        expect(
            screen.getByRole('heading', { name: /season 29/i }),
        ).toBeInTheDocument();
        // The date-window label is replaced, not appended.
        expect(
            screen.queryByRole('heading', { name: /last 30 days/i }),
        ).not.toBeInTheDocument();
    });

    test('renders mode pill row with three options + defaults to Random when both modes available', async () => {
        // Dual-mode availability → the card keeps its initial 'random' default
        // (no auto-switch); Ranked/All pills remain reachable.
        mockByMode({ available_modes: ['random', 'ranked'] });
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        // The main window fetch stays mode=random and no combined auto-switch fires.
        await waitFor(() => {
            expect(mainFetchCalls('random').length).toBeGreaterThanOrEqual(1);
        });
        expect(mainFetchCalls('combined').length).toBe(0);
        const group = screen.getByRole('group', { name: /battle mode/i });
        expect(group).toBeInTheDocument();
        const random = screen.getByRole('button', { name: /^Random$/ });
        const ranked = screen.getByRole('button', { name: /^Ranked$/ });
        const all = screen.getByRole('button', { name: /^All$/ });
        // Default: Random is pressed when both modes are available.
        expect(random).toHaveAttribute('aria-pressed', 'true');
        expect(ranked).toHaveAttribute('aria-pressed', 'false');
        expect(all).toHaveAttribute('aria-pressed', 'false');
    });

    test('clicking ranked pill refetches with mode=ranked', async () => {
        // Dual-mode payload → default stays random (no auto-switch).
        mockByMode({ available_modes: ['random', 'ranked'] });
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        await waitFor(() => {
            expect(mainFetchCalls('random').length).toBeGreaterThanOrEqual(1);
        });
        await act(async () => {
            screen.getByRole('button', { name: /^Ranked$/ }).click();
        });
        await waitFor(() => {
            expect(mainFetchCalls('ranked').length).toBeGreaterThanOrEqual(1);
        });
        // Picking a different mode emits a battle-history-mode umami event.
        expect(mockTrackEvent).toHaveBeenCalledWith(
            'battle-history-mode', expect.objectContaining({ mode: 'ranked', realm: 'na' }));
    });

    test('clicking each visible window pill refetches with the matching ?window= param', async () => {
        // Year is intentionally not in the visible pill row (capture started
        // 2026-04-28 — won't have meaningful 365-day data for ~12 months).
        resolveWith(buildPayload({ has_recent_24h_activity: true }));
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        // Year pill must NOT be in the DOM.
        expect(screen.queryByRole('button', { name: /^Year$/ })).toBeNull();
        for (const w of ['day', 'week', 'month'] as const) {
            const beforeCount = mockFetchSharedJson.mock.calls.length;
            resolveWith(buildPayload({ has_recent_24h_activity: true }));
            await act(async () => {
                const labelMatch = new RegExp(
                    `^${w[0].toUpperCase()}${w.slice(1)}$`,
                );
                screen.getByRole('button', { name: labelMatch }).click();
            });
            await waitFor(() => {
                expect(mockFetchSharedJson.mock.calls.length).toBe(beforeCount + 1);
            });
            const lastUrl = mockFetchSharedJson.mock.calls[beforeCount][0] as string;
            expect(lastUrl).toContain(`window=${w}`);
        }
    });

    test('fires name-baked player-history-<window> events when a non-active pill is picked', async () => {
        resolveWith(buildPayload({ has_recent_24h_activity: true }));
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });

        // Default window is 'month', so switching to Week/Day fires distinct named events.
        await act(async () => { screen.getByRole('button', { name: /^Week$/ }).click(); });
        expect(mockTrackEvent).toHaveBeenCalledWith('player-history-week', expect.objectContaining({ realm: 'na' }));

        await act(async () => { screen.getByRole('button', { name: /^Day$/ }).click(); });
        expect(mockTrackEvent).toHaveBeenCalledWith('player-history-day', expect.objectContaining({ realm: 'na' }));

        // Re-clicking the now-active Day pill does not re-fire.
        mockTrackEvent.mockClear();
        await act(async () => { screen.getByRole('button', { name: /^Day$/ }).click(); });
        expect(mockTrackEvent).not.toHaveBeenCalledWith('player-history-day', expect.anything());
    });

    test('Day pill is disabled when has_recent_24h_activity is false', async () => {
        resolveWith(buildPayload({ has_recent_24h_activity: false }));
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        const dayBtn = screen.getByRole('button', { name: /^Day$/ });
        expect(dayBtn).toBeDisabled();
        expect(dayBtn.getAttribute('aria-disabled')).toBe('true');
        expect(dayBtn.getAttribute('title'))
            .toBe('No battles in the last 24 hours');

        // Clicking the disabled pill does NOT trigger a refetch.
        const beforeCount = mockFetchSharedJson.mock.calls.length;
        await act(async () => { dayBtn.click(); });
        expect(mockFetchSharedJson.mock.calls.length).toBe(beforeCount);
    });

    test('polls when X-Ranked-Observation-Pending is true on a ranked-mode response', async () => {
        jest.useFakeTimers();
        try {
            // Dual-mode availability; the FIRST main ranked fetch returns the
            // pending header so the card schedules a poll, the next does not.
            let rankedMainSeen = 0;
            mockByMode({ available_modes: ['random', 'ranked'] }, {}, (params) => {
                if (params.get('mode') === 'ranked' && params.get('window') === 'month') {
                    rankedMainSeen += 1;
                    if (rankedMainSeen === 1) {
                        return { 'X-Ranked-Observation-Pending': 'true' };
                    }
                }
                return {};
            });
            render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
            await waitFor(() => {
                expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
            });
            await waitFor(() => {
                expect(mainFetchCalls('random').length).toBeGreaterThanOrEqual(1);
            });

            await act(async () => {
                screen.getByRole('button', { name: /^Ranked$/ }).click();
            });
            // First ranked main fetch landed (pending header set).
            await waitFor(() => {
                expect(mainFetchCalls('ranked').length).toBe(1);
            });
            // Advance the polling delay; the second (poll) ranked fetch fires.
            await act(async () => {
                jest.advanceTimersByTime(2100);
            });
            await waitFor(() => {
                expect(mainFetchCalls('ranked').length).toBe(2);
            });
        } finally {
            jest.useRealTimers();
        }
    });

    test('renders empty state with pill row when ranked mode has zero data', async () => {
        // Dual-mode availability; ranked mode has zero battles so the card stays
        // visible with the pill row (user can switch back).
        mockByMode({ available_modes: ['random', 'ranked'] }, {
            ranked: {
                totals: {
                    battles: 0, wins: 0, losses: 0, win_rate: 0,
                    damage: 0, avg_damage: 0, frags: 0, xp: 0,
                    planes_killed: 0, survived_battles: 0, survival_rate: 0,
                },
                by_ship: [],
                by_day: [],
            },
        });
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        await waitFor(() => {
            expect(mainFetchCalls('random').length).toBeGreaterThanOrEqual(1);
        });
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

describe('battle-history prefetch dedupe contract', () => {
    beforeEach(() => {
        mockFetchSharedJson.mockReset();
        // Default for the always-month sparkline fetch (second useEffect);
        // tests override the main fetch via resolveWith().
        mockFetchSharedJson.mockResolvedValue({ data: buildPayload({ by_day: [] }), headers: {} });
    });

    it('builders produce the canonical month/random url + cache key', () => {
        // Drift guard: PlayerRouteView's prefetch and the card's first fetch must
        // share these EXACT strings, or the prefetch becomes a duplicate request.
        expect(battleHistoryFetchUrl('lil_boots', 'na')).toBe(
            '/api/player/lil_boots/battle-history/?window=month&mode=random&realm=na');
        expect(battleHistoryCacheKey('lil_boots', 'na')).toBe(
            'battle-history:lil_boots:na:month:random:0:0');
    });

    it('prefetchBattleHistory fires the canonical month/random fetch', () => {
        mockFetchSharedJson.mockResolvedValueOnce({ data: buildPayload(), headers: {} });
        prefetchBattleHistory('lil_boots', 'na');
        expect(mockFetchSharedJson).toHaveBeenCalledWith(
            '/api/player/lil_boots/battle-history/?window=month&mode=random&realm=na',
            expect.objectContaining({
                ttlMs: BATTLE_HISTORY_FETCH_TTL_MS,
                cacheKey: 'battle-history:lil_boots:na:month:random:0:0',
            }),
        );
    });

    it("the card's first fetch uses the same url + cache key (so the prefetch dedupes onto it)", async () => {
        resolveWith(buildPayload());
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(mockFetchSharedJson).toHaveBeenCalled();
        });
        const [url, opts] = mockFetchSharedJson.mock.calls[0];
        expect(url).toBe('/api/player/lil_boots/battle-history/?window=month&mode=random&realm=na');
        expect(opts).toEqual(expect.objectContaining({
            cacheKey: 'battle-history:lil_boots:na:month:random:0:0',
            ttlMs: BATTLE_HISTORY_FETCH_TTL_MS,
        }));
    });
});

describe('buildWindowedDays UTC anchoring', () => {
    // The backend buckets battles by UTC calendar date (Django USE_TZ=False,
    // TIME_ZONE=UTC). The sparkline window must anchor to the same UTC "today",
    // or a viewer behind UTC sees today's battles fall past the last slot and
    // vanish from the sparkline (the bug this guards against).
    const day = (date: string, battles: number): BattleHistoryByDay => ({
        date, battles, wins: 0, damage: 0, frags: 0,
    });

    beforeEach(() => {
        jest.useFakeTimers();
        // 02:34 UTC on 2026-06-06 — i.e. still 2026-06-05 in any timezone behind UTC.
        jest.setSystemTime(new Date('2026-06-06T02:34:00Z'));
    });
    afterEach(() => {
        jest.useRealTimers();
    });

    it('anchors the last slot to the UTC date, not the browser-local date', () => {
        const padded = buildWindowedDays([], 30);
        expect(padded).toHaveLength(30);
        expect(padded[padded.length - 1].date).toBe('2026-06-06');
        expect(padded[0].date).toBe('2026-05-08');
    });

    it("places today's UTC-keyed battles in the final slot (regression: sparkline dropped them)", () => {
        const padded = buildWindowedDays([day('2026-06-06', 2)], 30);
        const last = padded[padded.length - 1];
        expect(last.date).toBe('2026-06-06');
        expect(last.battles).toBe(2);
    });
});
