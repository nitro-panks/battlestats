import React from 'react';
import { render, screen, waitFor, act, fireEvent, cleanup } from '@testing-library/react';
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
import { LocaleProvider } from '../../context/LocaleContext';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
    isAbortError: (error: unknown) => error instanceof DOMException && error.name === 'AbortError',
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
// window fetch and the always-90d strip fetch (second useEffect). A fixed
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
// defaults to 'month' while the strip always fetches 'ninety', so the two are
// now distinct requests rather than one deduped call
// fetch (the strip uses label 'BattleHistoryCard:sparkline'). Optionally
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
        // The window pill persists per (realm, player, mode). Without this a
        // pill click in one test restores as the starting window in the next.
        window.localStorage.clear();
        // Default response for the always-90d strip fetch (second useEffect call).
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
        // Win-rate cell renders the bare value with one decimal (the % lives
        // in the "WR %" header).
        expect(screen.getByText('66.7')).toBeInTheDocument();
        expect(screen.getByText('50.0')).toBeInTheDocument();
        expect(screen.getByLabelText(/30-day battle activity/i)).toBeInTheDocument();
    });

    test('clicking a ship row opens the combat profile as a body-portaled modal; backdrop and Escape close it', async () => {
        resolveWith(buildPayload());
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);

        fireEvent.click(await screen.findByRole('button', { name: /Toggle combat profile for Yamato/i }));

        const dialog = await screen.findByRole('dialog');
        expect(dialog).toHaveAttribute('aria-modal', 'true');
        // Portaled straight under <body> so the card's clamped/overflow ancestors
        // can't clip the overlay (and the inline panel can't push the table out).
        expect(dialog.parentElement).toBe(document.body);
        expect(screen.getByTestId('ship-stats')).toBeInTheDocument();
        // The ships table stays mounted underneath the overlay.
        expect(screen.getByText('Dalian')).toBeInTheDocument();

        // Backdrop click closes (only when the backdrop itself is the target).
        fireEvent.click(dialog);
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        expect(mockTrackEvent).toHaveBeenCalledWith(
            'ship-stats-close', expect.objectContaining({ ship_id: 42, source: 'backdrop' }),
        );

        // Reopen, then Escape closes.
        fireEvent.click(screen.getByRole('button', { name: /Toggle combat profile for Yamato/i }));
        await screen.findByRole('dialog');
        fireEvent.keyDown(document, { key: 'Escape' });
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        expect(mockTrackEvent).toHaveBeenCalledWith(
            'ship-stats-close', expect.objectContaining({ ship_id: 42, source: 'escape' }),
        );
    });

    test('Avg dmg colors against the ship population baseline; F/B pads to two decimals', async () => {
        const payload = buildPayload();
        payload.by_ship[0].ship_pop_avg_damage = 40_000; // 47,900 / 40,000 → +20%
        resolveWith(payload);
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);

        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });

        // Baselined ship: diverging color + comparison tooltip.
        const baselined = screen.getByTitle(/\+20% vs this ship's realm 30d average \(40,000\)/);
        expect(baselined).toHaveTextContent('47,900');
        expect((baselined as HTMLElement).style.color).not.toBe('');

        // No baseline: neutral fallback with the explanatory tooltip.
        const neutral = screen.getByTitle(/No ship-average damage baseline/);
        expect(neutral).toHaveTextContent('47,500');

        // F/B is fixed one decimal: 12 frags / 6 battles → "2.0", 3/2 → "1.5".
        expect(screen.getByText('2.0')).toBeInTheDocument();
        expect(screen.getByText('1.5')).toBeInTheDocument();
    });

    test('caps sparkline bars at 50 battles/day: over-cap days pin to full height, and the crosshair tracks the day under the pointer', async () => {
        // The sparkline windows monthByDay to the last 30 UTC days, so build
        // dates relative to UTC "today" to keep them in-window without faking
        // the clock.
        const utcDay = (offset: number): string => {
            const d = new Date();
            d.setUTCDate(d.getUTCDate() - offset);
            return d.toISOString().slice(0, 10);
        };
        const byDay: BattleHistoryByDay[] = [
            { date: utcDay(3), battles: 250, wins: 130, damage: 0, frags: 0 }, // far over cap
            { date: utcDay(2), battles: 60, wins: 30, damage: 0, frags: 0 },   // just over cap
            { date: utcDay(1), battles: 25, wins: 12, damage: 0, frags: 0 },   // half the cap
            { date: utcDay(0), battles: 5, wins: 3, damage: 0, frags: 0 },     // small day
        ];
        // Drive every fetch (main window + always-month sparkline) with this by_day.
        mockFetchSharedJson.mockReset();
        // A lifetime baseline so the strip draws its overall-WR line and the
        // readout has a second field to report.
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({
                available_modes: ['random'],
                by_day: byDay,
                totals: {
                    ...buildPayload().totals,
                    lifetime_battles: 4_000,
                    lifetime_win_rate: 55.0,
                },
            }),
            headers: {},
        });

        const { container } = render(<BattleHistoryCard playerName="grinder" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });

        // Bars carry their day + true count as data attributes (the per-bar
        // <title> tooltip they used to carry is gone — the crosshair readout
        // replaced it).
        const heightFor = (battles: number): number => {
            const rect = container.querySelector(
                `.sparkline-bar-rise[data-battles="${battles}"] rect[fill="rgba(120,120,120,0.25)"]`,
            );
            expect(rect).toBeTruthy();
            return parseFloat(rect!.getAttribute('height') ?? '0');
        };

        // Both over-cap days (250 and 60) pin to the same full-height bar — neither
        // towers over the other.
        expect(heightFor(250)).toBeCloseTo(heightFor(60), 5);

        // A sub-cap day scales against the cap (25/50 → half height), not the
        // 250-game spike.
        expect(heightFor(25)).toBeLessThan(heightFor(60));
        expect(heightFor(25)).toBeCloseTo(heightFor(60) / 2, 1);

        // The true count survives in the crosshair readout. jsdom has no layout,
        // so hand the hit area a box and drive the pointer across it: the strip
        // shows 30 days, and the 250-battle day sits at index 26 (3 days back).
        const hit = screen.getByTestId('strip-hit-area');
        const WIDTH = 300;
        jest.spyOn(hit, 'getBoundingClientRect').mockReturnValue({
            left: 0, top: 0, right: WIDTH, bottom: 64, width: WIDTH, height: 64, x: 0, y: 0,
            toJSON: () => ({}),
        } as DOMRect);
        const gap = 0.5;
        const barW = (100 - gap * 29) / 30;
        const clientXForIndex = (i: number): number =>
            ((i * (barW + gap) + barW / 2) / 100) * WIDTH;

        // The crosshair coalesces pointer events onto an animation frame, so let
        // the frame run before reading the readout back.
        const hoverIndex = async (i: number): Promise<void> => {
            fireEvent.pointerMove(hit, { clientX: clientXForIndex(i) });
            await act(async () => { await new Promise((r) => setTimeout(r, 32)); });
        };
        await hoverIndex(26);
        const readout = () => screen.getByTestId('strip-readout').textContent ?? '';
        // The row reports the hovered day, the overall (lifetime) win rate at the
        // end of it, and — only when the line actually moved — a signed delta.
        expect(readout()).toContain(utcDay(3));
        expect(readout()).toMatch(
            /^\d{4}-\d{2}-\d{2}\d+\.\d{2}%(?:[+\u2212]\d+\.\d{2})?\d+W \d+L$/,
        );
        // ...and, right-justified, the day's own record. That day is 130 wins in
        // 250 battles, so the losses are derived, not read: 250 - 130 = 120.
        expect(screen.getByTestId('strip-readout-record').textContent).toBe('130W 120L');
        // 130 wins in 250 battles is below the 55% lifetime baseline, so that day
        // pulled the line down: the delta reads negative, in the table's red.
        const delta = screen.getByTestId('strip-readout-delta');
        expect(delta.textContent).toMatch(/^\u2212\d+\.\d{2}$/);
        expect(delta).toHaveStyle({ color: '#a50f15' });

        // Mousing one bar right moves the readout to the next day.
        await hoverIndex(28);
        expect(readout()).toContain(utcDay(1));

        // The rule exists only while the pointer is over the strip, and the
        // hovered day carries a 1px inner halo that does NOT change the bar's
        // footprint: the halo rect matches the bar rect exactly, and reads its
        // 1px inward from a 2px stroke clipped to that same rect.
        expect(screen.getByTestId('strip-crosshair')).toBeInTheDocument();
        const halo = screen.getByTestId('strip-bar-halo');
        const bar = container.querySelector(
            '.sparkline-bar-rise[data-battles="25"] rect[fill="rgba(120,120,120,0.25)"]',
        )!;
        for (const attr of ['x', 'y', 'width', 'height']) {
            expect(halo.getAttribute(attr)).toBe(bar.getAttribute(attr));
        }
        expect(halo.getAttribute('stroke-width')).toBe('2');
        expect(halo.getAttribute('clip-path')).toMatch(/^url\(#sparkline-halo-/);
        expect(halo.getAttribute('fill')).toBe('none');

        // An empty day gets no halo — its bar is a 2px stub a 1px inset would
        // fill solid; the rule alone marks those.
        await hoverIndex(5);
        // Index 5 of a 30-day domain is 24 days back. No battles that day means the
        // line did not move, so there is no delta to show.
        expect(screen.getByTestId('strip-readout').textContent).toContain(utcDay(24));
        expect(screen.queryByTestId('strip-readout-delta')).not.toBeInTheDocument();
        // Nor is there a record to state: a day with no battles reports no W/L
        // rather than "0W 0L".
        expect(screen.queryByTestId('strip-readout-record')).not.toBeInTheDocument();
        expect(screen.queryByTestId('strip-bar-halo')).not.toBeInTheDocument();

        fireEvent.pointerLeave(hit);
        expect(screen.queryByTestId('strip-crosshair')).not.toBeInTheDocument();
        expect(screen.queryByTestId('strip-bar-halo')).not.toBeInTheDocument();
        // ...but the readout row stays mounted (fixed height), falling back to
        // the newest day carrying battles so nothing below it shifts.
        expect(screen.getByTestId('strip-readout').textContent).toContain(utcDay(0));
    });

    test('anchors the strip readout to the exact career integers, not the payload\'s rounded WR', async () => {
        // The payload's lifetime_win_rate is rounded to ONE decimal by the
        // backend, so a strip anchored to it reads 63.50 where the page header
        // says 63.53 (nekonomae/na, 9164 wins in 14425 battles). The host passes
        // the integers the header itself is computed from; the strip must use
        // them.
        const utcToday = new Date().toISOString().slice(0, 10);
        const byDay: BattleHistoryByDay[] = [
            { date: utcToday, battles: 4, wins: 3, damage: 0, frags: 0 },
        ];
        const payload = buildPayload({
            available_modes: ['random'],
            by_day: byDay,
            totals: {
                ...buildPayload().totals,
                lifetime_battles: 14_425,
                lifetime_win_rate: 63.5,
            },
        });
        mockFetchSharedJson.mockReset();
        mockFetchSharedJson.mockResolvedValue({ data: payload, headers: {} });

        const { unmount } = render(
            <BattleHistoryCard
                playerName="nekonomae"
                realm="na"
                overallBattles={14_425}
                overallWins={9_164}
            />,
        );
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        // Idle readout sits on the newest day carrying battles — today.
        await waitFor(() => {
            expect(screen.getByTestId('strip-readout').textContent).toContain('63.53%');
        });
        unmount();

        // Without the override, and against a backend too old to send
        // `lifetime_wins`, the strip falls back to the rounded rate and reads the
        // coarser 63.50 — correct to the precision it was given.
        mockFetchSharedJson.mockReset();
        mockFetchSharedJson.mockResolvedValue({ data: payload, headers: {} });
        const stale = render(<BattleHistoryCard playerName="nekonomae" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        await waitFor(() => {
            expect(screen.getByTestId('strip-readout').textContent).toContain('63.50%');
        });
        stale.unmount();

        // Ranked passes no override — its career baseline is a different
        // aggregate — so it reconciles through the payload's own `lifetime_wins`.
        mockFetchSharedJson.mockReset();
        mockFetchSharedJson.mockResolvedValue({
            data: {
                ...payload,
                available_modes: ['ranked'],
                totals: { ...payload.totals, lifetime_wins: 9_164 },
            },
            headers: {},
        });
        render(<BattleHistoryCard playerName="nekonomae" realm="na" mode="ranked" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        await waitFor(() => {
            expect(screen.getByTestId('strip-readout').textContent).toContain('63.53%');
        });
    });

    test('splits Win Rate into sortable WR (window) and Overall WR (overall + delta) columns', async () => {
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
        // ("Overall WR" also appears in the totals bar, so scope these to the
        // table via the columnheader role rather than getByText.)
        expect(screen.getByRole('columnheader', { name: 'WR %' })).toBeInTheDocument();
        expect(screen.getByRole('columnheader', { name: 'Overall WR %' })).toBeInTheDocument();
        expect(screen.queryByRole('columnheader', { name: /^Win Rate$/i })).not.toBeInTheDocument();

        // Session WR (WR/S), overall WR (WR/O), and the delta all render —
        // bare values; the % sign lives in the column headers.
        expect(screen.getByText('66.7')).toBeInTheDocument();   // Yamato session
        expect(screen.getByText('55.0')).toBeInTheDocument();   // Yamato overall
        expect(screen.getByText('Δ+11.7')).toBeInTheDocument();
        expect(screen.getByText('Δ-10.0')).toBeInTheDocument();

        // Default sort is battles desc → Yamato (6) before Dalian (2).
        expect(screen.getAllByRole('row')[1].textContent).toContain('Yamato');

        // Sort by overall WR, desc → Dalian (60.0) above Yamato (55.0).
        fireEvent.click(screen.getByRole('columnheader', { name: 'Overall WR %' }));
        let rows = screen.getAllByRole('row');
        expect(rows[1].textContent).toContain('Dalian');
        expect(rows[2].textContent).toContain('Yamato');

        // Window WR sorts independently, desc → Yamato (66.7) above Dalian (50.0).
        fireEvent.click(screen.getByRole('columnheader', { name: 'WR %' }));
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
        // mockResolvedValue, not resolveWith's ...Once: availability is now
        // judged on the 90-day STRIP payload, so "no battles" has to hold for
        // the wider span too. A player empty at 30d but not at 90d is available
        // — the fallback opens them on 90d — which is the case below this one.
        mockFetchSharedJson.mockResolvedValue({ headers: {}, data: buildPayload({
            available_modes: ['random'],
            totals: {
                battles: 0, wins: 0, losses: 0, win_rate: 0,
                damage: 0, avg_damage: 0, frags: 0, xp: 0,
                planes_killed: 0, survived_battles: 0, survival_rate: 0,
            },
            by_ship: [],
            by_day: [],
        }) });
        render(
            <BattleHistoryCard
                embedded
                playerName="empty"
                realm="na"
                onAvailabilityChange={onAvailabilityChange}
            />,
        );
        await waitFor(() => {
            expect(onAvailabilityChange).toHaveBeenCalledWith(false, ['random']);
        });
    });

    test('embedded: a 30d-empty player is still AVAILABLE — judged on the 90d strip', async () => {
        // The regression this guards: the card opens on Month, so a player whose
        // last battles were ~45 days ago has an empty month payload. Reading
        // availability off that reported "no activity", the parent disabled the
        // Activity tab, and the 30d-empty fallback never got to promote them to
        // 90d — the tab went dark for exactly the population it serves.
        const onAvailabilityChange = jest.fn();
        const utcDay = (o: number): string => {
            const d = new Date();
            d.setUTCDate(d.getUTCDate() - o);
            return d.toISOString().slice(0, 10);
        };
        mockFetchSharedJson.mockImplementation((url: string) => {
            const w = new URL(url, 'http://t').searchParams.get('window');
            return Promise.resolve({
                headers: {},
                data: buildPayload(w === 'ninety'
                    ? { by_day: [{ date: utcDay(45), battles: 9, wins: 5, damage: 0, frags: 0 }] }
                    : {
                        by_day: [],
                        by_ship: [],
                        totals: {
                            battles: 0, wins: 0, losses: 0, win_rate: 0,
                            damage: 0, avg_damage: 0, frags: 0, xp: 0,
                            planes_killed: 0, survived_battles: 0, survival_rate: 0,
                        },
                    }),
            });
        });
        render(
            <BattleHistoryCard
                embedded
                playerName="lapsed"
                realm="na"
                onAvailabilityChange={onAvailabilityChange}
            />,
        );
        await waitFor(() => {
            expect(onAvailabilityChange).toHaveBeenCalledWith(true, ['random']);
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
            expect(onAvailabilityChange).toHaveBeenCalledWith(true, ['random']);
        });
    });

    test('embedded: reports availability false + surfaces available modes for a ranked-only player', async () => {
        const onAvailabilityChange = jest.fn();
        // Activity availability is now random-scoped: a ranked-only player darks
        // the Activity tab, and the second callback arg lets the parent fall
        // back to the Ranked tab (where their history lives now).
        mockByMode({ available_modes: ['ranked'] }, {
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
            expect(onAvailabilityChange).toHaveBeenCalledWith(false, ['ranked']);
        });
    });

    test('ranked: reports availability false when the window holds no ranked rows', async () => {
        const onAvailabilityChange = jest.fn();
        // The ranked branch of `battleHistoryIndicatesActivity` accepts
        // `available_modes` as a stand-in for "recent ranked rows", which is
        // only sound because the backend scopes that probe to the REQUESTED
        // window (views.py `_build_battle_history_payload`). It used to run
        // across all dates, so its width was the rollup retention (105d) while
        // the strip, the pills and the 30d->90d fallback were all judged on the window.
        // A player whose last ranked battles were 66 days ago then lit the
        // Ranked tab onto an activity view with nothing to draw
        // (2026-08-24, `briansayshello` NA).
        mockByMode({ available_modes: ['random'] }, {
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
        render(
            <BattleHistoryCard
                embedded
                mode="ranked"
                playerName="briansayshello"
                realm="na"
                onAvailabilityChange={onAvailabilityChange}
            />,
        );
        await waitFor(() => {
            expect(onAvailabilityChange).toHaveBeenCalledWith(false, ['random']);
        });
    });

    test('ranked: stays available on a season-edge zero-window', async () => {
        const onAvailabilityChange = jest.fn();
        // The counterweight to the test above. Ranked totals are scoped to the
        // player's CURRENT season server-side, so someone who played the
        // previous season a few days ago has zero in-window battles while being
        // genuinely ranked-active. The probe is deliberately not season-filtered
        // so this case keeps the tab lit.
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
        render(
            <BattleHistoryCard
                embedded
                mode="ranked"
                playerName="seasonedge"
                realm="na"
                onAvailabilityChange={onAvailabilityChange}
            />,
        );
        await waitFor(() => {
            expect(onAvailabilityChange).toHaveBeenCalledWith(true, ['random', 'ranked']);
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

    test('fires TWO distinct fetches: month for the view, ninety for the strip', () => {
        mockFetchSharedJson.mockReturnValue(new Promise(() => {}));
        render(<BattleHistoryCard playerName="lil_boots" realm="eu" />);
        // These no longer share a url. The card opens on Month while the strip
        // always pulls the full 90-day backdrop — the span the 90d pill animates
        // out to, and the data the 30d-empty fallback reads. They cannot be
        // collapsed into one request: totals and by_ship are aggregated
        // server-side per window, so a 30d view is not derivable from 90d.
        const urls = mockFetchSharedJson.mock.calls.map((c) => c[0] as string);
        const main = urls.find((u) => u.includes('window=month'));
        const strip = urls.find((u) => u.includes('window=ninety'));
        expect(main).toBeDefined();
        expect(strip).toBeDefined();
        expect(main).toContain('/api/player/lil_boots/battle-history/');
        expect(main).toContain('realm=eu');
        expect(strip).toContain('realm=eu');
        // The view's window is requested BEFORE the backdrop: the reader is
        // waiting on the former, and the shared queue serves in arrival order.
        expect(urls.indexOf(main!)).toBeLessThan(urls.indexOf(strip!));
    });

    test('initial fetch uses mode=random (default)', () => {
        mockFetchSharedJson.mockReturnValueOnce(new Promise(() => {}));
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        const [url] = mockFetchSharedJson.mock.calls[0];
        expect(url).toContain('mode=random');
    });

    test('never renders a mode pill row; a static caption labels the fixed mode', async () => {
        // Even a dual-mode player gets no toggle — the mode is fixed by prop
        // now (pill removed 2026-07-13; ranked history lives on the Ranked tab).
        mockByMode({ available_modes: ['random', 'ranked'] });
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        expect(screen.queryByRole('group', { name: /battle mode/i })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /^Ranked$/ })).not.toBeInTheDocument();
        // (An "All" button DOES exist now — it's the ships-treemap scope
        // filter, not a mode pill; the group + Ranked checks above prove the
        // pill stayed dead.)
        expect(screen.getByText('Random Battles')).toBeInTheDocument();
        // No combined fetch ever fires.
        expect(mainFetchCalls('combined').length).toBe(0);
    });

    test('mode="ranked" drives both fetches with mode=ranked and shows the static Ranked caption', async () => {
        mockByMode({ available_modes: ['random', 'ranked'] });
        render(<BattleHistoryCard playerName="lil_boots" realm="na" mode="ranked" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        expect(mainFetchCalls('ranked').length).toBeGreaterThan(0);
        expect(mainFetchCalls('random').length).toBe(0);
        expect(screen.getByText('Ranked')).toBeInTheDocument();
        expect(screen.queryByText('Random Battles')).not.toBeInTheDocument();
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
        render(<BattleHistoryCard playerName="ranked_only" realm="na" mode="ranked" />);
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

    test('clicking each visible window pill refetches with the matching ?window= param', async () => {
        // Year is intentionally not in the visible pill row (capture started
        // 2026-04-28 — won't have meaningful 365-day data for ~12 months).
        // Give the (sparkline) month fetch recent by_day so Week/Month aren't
        // treated as empty windows and disabled — this test exercises the
        // pill→refetch wiring for an active player.
        const utcDay = (o: number): string => {
            const d = new Date();
            d.setUTCDate(d.getUTCDate() - o);
            return d.toISOString().slice(0, 10);
        };
        mockFetchSharedJson.mockReset();
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({
                by_day: [
                    { date: utcDay(1), battles: 4, wins: 2, damage: 0, frags: 0 },
                    { date: utcDay(0), battles: 3, wins: 1, damage: 0, frags: 0 },
                ],
            }),
            headers: {},
        });
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        // Year pill must NOT be in the DOM.
        expect(screen.queryByRole('button', { name: /^Year$/ })).toBeNull();
        for (const w of ['day', 'week', 'month'] as const) {
            const beforeCount = mockFetchSharedJson.mock.calls.length;
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
        // Recent by_day so Week isn't disabled as an empty window (this test is
        // about the click→event wiring, not the empty-pill disable).
        const utcDay = (o: number): string => {
            const d = new Date();
            d.setUTCDate(d.getUTCDate() - o);
            return d.toISOString().slice(0, 10);
        };
        mockFetchSharedJson.mockReset();
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({
                by_day: [
                    { date: utcDay(1), battles: 4, wins: 2, damage: 0, frags: 0 },
                    { date: utcDay(0), battles: 3, wins: 1, damage: 0, frags: 0 },
                ],
            }),
            headers: {},
        });
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });

        // Default window is Month, so switching to Week/Day fires distinct named events.
        await act(async () => { screen.getByRole('button', { name: /^Week$/ }).click(); });
        expect(mockTrackEvent).toHaveBeenCalledWith('player-history-week', expect.objectContaining({ realm: 'na' }));

        await act(async () => { screen.getByRole('button', { name: /^Day$/ }).click(); });
        expect(mockTrackEvent).toHaveBeenCalledWith('player-history-day', expect.objectContaining({ realm: 'na' }));

        // Re-clicking the now-active Day pill does not re-fire.
        mockTrackEvent.mockClear();
        await act(async () => { screen.getByRole('button', { name: /^Day$/ }).click(); });
        expect(mockTrackEvent).not.toHaveBeenCalledWith('player-history-day', expect.anything());
    });

    test('Day pill is disabled when today has no battles (derived from the strip)', async () => {
        // Day is a UTC calendar window like every other pill (2026-07-30), so
        // its emptiness comes off the same strip array — no backend flag. This
        // player played yesterday but not today: Day dims, Week does not.
        const utcDay = (o: number): string => {
            const d = new Date();
            d.setUTCDate(d.getUTCDate() - o);
            return d.toISOString().slice(0, 10);
        };
        mockFetchSharedJson.mockReset();
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({
                by_day: [{ date: utcDay(1), battles: 4, wins: 2, damage: 0, frags: 0 }],
            }),
            headers: {},
        });
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        const dayBtn = screen.getByRole('button', { name: /^Day$/ });
        await waitFor(() => expect(dayBtn).toBeDisabled());
        expect(dayBtn.getAttribute('aria-disabled')).toBe('true');
        expect(dayBtn.getAttribute('title')).toBe('No battles today');
        // Yesterday's battles are inside the week window, so Week stays live.
        expect(screen.getByRole('button', { name: /^Week$/ })).not.toBeDisabled();

        // Clicking the disabled pill does NOT trigger a refetch.
        const beforeCount = mockFetchSharedJson.mock.calls.length;
        await act(async () => { dayBtn.click(); });
        expect(mockFetchSharedJson.mock.calls.length).toBe(beforeCount);
    });

    test('Week pill is disabled when the trailing 7 days have no battles (derived from month by_day)', async () => {
        // A player whose only recent battle is ~10 days ago: inside the 30-day
        // month window but outside the 7-day week window. Week must dim/disable;
        // Month (which has the data) and the 90d pill stay enabled.
        const utcDay = (o: number): string => {
            const d = new Date();
            d.setUTCDate(d.getUTCDate() - o);
            return d.toISOString().slice(0, 10);
        };
        mockFetchSharedJson.mockReset();
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({
                by_day: [{ date: utcDay(10), battles: 5, wins: 3, damage: 0, frags: 0 }],
            }),
            headers: {},
        });
        render(<BattleHistoryCard playerName="lapsed" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });

        const weekBtn = screen.getByRole('button', { name: /^Week$/ });
        await waitFor(() => expect(weekBtn).toBeDisabled());
        expect(weekBtn.getAttribute('aria-disabled')).toBe('true');
        expect(weekBtn.getAttribute('title')).toBe('No battles in the last 7 days');

        // Day is disabled too (no 24h activity); Month and 90d both contain the
        // 10-day-old data, so both stay enabled.
        expect(screen.getByRole('button', { name: /^Day$/ })).toBeDisabled();
        expect(screen.getByRole('button', { name: /^Month$/ })).not.toBeDisabled();

        // Clicking the disabled Week pill does not refetch.
        const beforeCount = mockFetchSharedJson.mock.calls.length;
        await act(async () => { weekBtn.click(); });
        expect(mockFetchSharedJson.mock.calls.length).toBe(beforeCount);
    });

    // The trend strip is one fixed 90-day domain on every pill; the bracket
    // beneath it is what reports the selected span. Bar geometry across a 0–100
    // viewBox: barW = (100 − 0.5×89) ÷ 90 = 0.61667, so barW + gap = 1.11667,
    // and the bracket's left edge = (90 − span) × 1.11667. The right edge is
    // always pinned at 100 (the newest day).
    describe('window range bracket', () => {
        const renderActive = async () => {
            const utcDay = (o: number): string => {
                const d = new Date();
                d.setUTCDate(d.getUTCDate() - o);
                return d.toISOString().slice(0, 10);
            };
            mockFetchSharedJson.mockReset();
            mockFetchSharedJson.mockResolvedValue({
                data: buildPayload({
                    by_day: [
                        { date: utcDay(1), battles: 4, wins: 2, damage: 0, frags: 0 },
                        { date: utcDay(0), battles: 3, wins: 1, damage: 0, frags: 0 },
                    ],
                }),
                headers: {},
            });
            render(<BattleHistoryCard embedded playerName="lil_boots" realm="na" />);
            await waitFor(() => {
                expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
            });
        };
        const bracket = () => screen.getByTestId('window-range-bracket');

        // The strip SHOWS 30 days on Day/Week/Month and 90 on the 90d pill, but
        // it HOLDS all 90 at every setting: the days outside the shown domain
        // sit at a negative x and are clipped. That is what makes the change a
        // glide in both directions instead of a glide one way and a pop the
        // other, so "still 90 groups mounted" is the mechanism under test, not
        // an implementation detail.
        const strip = () => screen.getByLabelText(/-day battle activity/i);
        const barGroups = () => strip().querySelectorAll('.sparkline-bar-rise');
        const firstBarWidth = () => Number(
            strip().querySelector('.sparkline-bar-rise rect')!.getAttribute('width'));

        test('shows 30 days on Day/Week/Month and 90 on 90d', async () => {
            await renderActive();
            // Opens on Month → a 30-day backdrop, bars 2.85 wide.
            expect(strip().getAttribute('aria-label')).toMatch(/^30-day/);
            expect(firstBarWidth()).toBeCloseTo(2.85, 5);

            // 90d is the only pill whose span exceeds 30, so it is the only one
            // that widens the backdrop to the full held domain.
            await act(async () => { screen.getByRole('button', { name: /^90d$/ }).click(); });
            expect(strip().getAttribute('aria-label')).toMatch(/^90-day/);
            expect(firstBarWidth()).toBeCloseTo(0.61667, 5);

            // ...and back, so the widen is not one-way.
            await act(async () => { screen.getByRole('button', { name: /^Week$/ }).click(); });
            expect(strip().getAttribute('aria-label')).toMatch(/^30-day/);
            expect(firstBarWidth()).toBeCloseTo(2.85, 5);
        });

        test('every held day stays mounted across a domain change, so it glides', async () => {
            await renderActive();
            expect(barGroups()).toHaveLength(90);
            for (const label of [/^Day$/, /^Week$/, /^90d$/, /^Month$/]) {
                await act(async () => { screen.getByRole('button', { name: label }).click(); });
                expect(barGroups()).toHaveLength(90);
            }
        });

        test('at the Month default it spans the full 30-day domain at zero opacity', async () => {
            await renderActive();
            expect(bracket().style.transform).toBe('translate(0.000px, 0px) scale(1.00000, 1)');
            expect(bracket().style.opacity).toBe('0');
        });

        test('90d also spans its full domain at zero opacity', async () => {
            // The bracket dissolves whenever the span equals the shown domain —
            // it has stopped carrying information. That happens at Month (span
            // 30 of a 30-day backdrop) and at 90d (span 90 of the full held
            // domain), so the bracket is visible only on Day and Week.
            await renderActive();
            await act(async () => { screen.getByRole('button', { name: /^90d$/ }).click(); });
            expect(bracket().style.transform).toBe('translate(0.000px, 0px) scale(1.00000, 1)');
            expect(bracket().style.opacity).toBe('0');
        });

        test('narrowing the window contracts it rightward, opaque', async () => {
            await renderActive();
            // At a 30-bar domain the pitch is 3.35 (bar 2.85 + gap 0.5).
            // Week: left = 23 × 3.35 = 77.05, scale = 22.95 ÷ 100.
            await act(async () => { screen.getByRole('button', { name: /^Week$/ }).click(); });
            expect(bracket().style.transform).toBe('translate(77.050px, 0px) scale(0.22950, 1)');
            expect(bracket().style.opacity).toBe('1');
            // Day: left = 29 × 3.35 = 97.15, one bar wide.
            await act(async () => { screen.getByRole('button', { name: /^Day$/ }).click(); });
            expect(bracket().style.transform).toBe('translate(97.150px, 0px) scale(0.02850, 1)');
            expect(bracket().style.opacity).toBe('1');
            // Clicking through the 90d pill and back to Week: the domain for
            // `week` is still 30 (WINDOW_SPAN_DAYS.week=7 does not exceed 30),
            // so this lands on the SAME 30-bar geometry as the plain Week case.
            await act(async () => { screen.getByRole('button', { name: /^90d$/ }).click(); });
            await act(async () => { screen.getByRole('button', { name: /^Week$/ }).click(); });
            expect(bracket().style.transform).toBe('translate(77.050px, 0px) scale(0.22950, 1)');
        });

        test('stays mounted for a player with no battles at all', async () => {
            // CSS transitions do not run on first render, so the bracket must
            // never unmount — a conditionally-rendered one would pop into place
            // with no motion on Month → Week, and the motion is the feature. It
            // is mounted even for a player with an entirely empty strip; at the
            // Month default it is merely transparent.
            mockFetchSharedJson.mockReset();
            mockFetchSharedJson.mockResolvedValue({
                data: buildPayload({ by_day: [] }), headers: {},
            });
            render(<BattleHistoryCard embedded playerName="quiet" realm="na" />);
            await waitFor(() => {
                expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
            });
            expect(bracket()).toBeInTheDocument();
            expect(bracket().style.opacity).toBe('0');
            expect(bracket().style.transform).toBe('translate(0.000px, 0px) scale(1.00000, 1)');
        });

        test('it is decorative — the header already names the window', async () => {
            await renderActive();
            expect(bracket().closest('svg')).toHaveAttribute('aria-hidden', 'true');
        });
    });

    test('an inactive 90d pill is disabled when the trailing 90 days have no battles', async () => {
        // Battles 90 days back: present in the payload, outside every pill's
        // span. With the strip 90 days deep, 90d's emptiness is derivable
        // the same way week's and month's are.
        const utcDay = (o: number): string => {
            const d = new Date();
            d.setUTCDate(d.getUTCDate() - o);
            return d.toISOString().slice(0, 10);
        };
        mockFetchSharedJson.mockReset();
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({
                by_day: [{ date: utcDay(90), battles: 5, wins: 3, damage: 0, frags: 0 }],
            }),
            headers: {},
        });
        render(<BattleHistoryCard embedded playerName="lapsed" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        // Month is the active window now, so the isActive guard keeps IT
        // interactive; 90d and Week are inactive and equally empty, so they dim.
        // The single battle sits at day 90 — outside even the 90-day span — so
        // the 30d-empty fallback does not fire and Month stays selected.
        await waitFor(() => {
            expect(screen.getByRole('button', { name: /^90d$/ })).toBeDisabled();
        });
        expect(screen.getByRole('button', { name: /^90d$/ }).getAttribute('title'))
            .toBe('No battles in the last 90 days');
        expect(screen.getByRole('button', { name: /^Week$/ })).toBeDisabled();
        expect(screen.getByRole('button', { name: /^Month$/ })).not.toBeDisabled();
    });

    test('the currently-viewed window is never disabled, even when its span is empty', async () => {
        // All windows empty. Month is the active default → it must NOT be
        // disabled (you are viewing it); the inactive Week pill IS disabled.
        // Nothing anywhere means the fallback has no wider span to promote to,
        // so the card stays on Month rather than bouncing to 90d.
        // Embedded so the empty card renders its chrome (pills) instead of null.
        mockFetchSharedJson.mockReset();
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({
                totals: {
                    battles: 0, wins: 0, losses: 0, win_rate: 0,
                    damage: 0, avg_damage: 0, frags: 0, xp: 0,
                    planes_killed: 0, survived_battles: 0, survival_rate: 0,
                },
                by_ship: [],
                by_day: [],
            }),
            headers: {},
        });
        render(<BattleHistoryCard embedded playerName="empty" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });

        // Active Month pill: enabled despite the empty window (isActive guard).
        const activeBtn = screen.getByRole('button', { name: /^Month$/ });
        expect(activeBtn).not.toBeDisabled();
        expect(activeBtn.getAttribute('aria-pressed')).toBe('true');
        // Inactive, equally-empty Week pill: disabled.
        await waitFor(() => {
            expect(screen.getByRole('button', { name: /^Week$/ })).toBeDisabled();
        });
    });

    test('polls when X-Ranked-Observation-Pending is true on a ranked-mode response', async () => {
        jest.useFakeTimers();
        try {
            // The FIRST main ranked fetch returns the pending header so the
            // card schedules a poll, the next does not. The header is keyed to
            // the MAIN fetch's window (month) — the strip's own ninety-day fetch
            // is a separate request now and never drives the poll.
            let rankedSeen = 0;
            mockByMode({ available_modes: ['random', 'ranked'] }, {}, (params): Record<string, string> => {
                if (params.get('mode') === 'ranked' && params.get('window') === 'month') {
                    rankedSeen += 1;
                    if (rankedSeen === 1) {
                        return { 'X-Ranked-Observation-Pending': 'true' };
                    }
                }
                return {};
            });
            render(<BattleHistoryCard playerName="lil_boots" realm="na" mode="ranked" />);
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

    test('embedded ranked card renders the empty state with the Ranked caption when the season has zero data', async () => {
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
        render(<BattleHistoryCard embedded playerName="lil_boots" realm="na" mode="ranked" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        await waitFor(() => {
            expect(screen.getByText(/No ranked battles in this window/i)).toBeInTheDocument();
        });
        // The static caption still names the mode on the empty state.
        expect(screen.getByText('Ranked')).toBeInTheDocument();
    });
});

describe('battle-history prefetch dedupe contract', () => {
    beforeEach(() => {
        mockFetchSharedJson.mockReset();
        window.localStorage.clear();
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


// Activity-card locale wiring (2026-08-11). The card is the player page's
// default tab and carries the densest label band on the site; until this round
// every one of these strings was a hardcoded English literal, so a detected
// ko/ja visitor read a translated header above an English card. Rendered under
// the REAL dictionaries — an English-only assertion cannot tell a working t()
// call from the literal it replaced.
describe('BattleHistoryCard — locale coverage', () => {
    beforeEach(() => {
        localStorage.clear();
        mockFetchSharedJson.mockReset();
        mockTrackEvent.mockReset();
        mockFetchSharedJson.mockResolvedValue({ data: buildPayload({ by_day: [] }), headers: {} });
    });

    const renderInLocale = async (locale: string) => {
        localStorage.setItem('bs-locale', locale);
        resolveWith(buildPayload());
        render(
            <LocaleProvider>
                <BattleHistoryCard playerName="lil_boots" realm="na" />
            </LocaleProvider>,
        );
        await waitFor(() => expect(screen.getByTestId('battle-history-card')).toBeInTheDocument());
    };

    it('renders the Korean window header, pills, mode caption and totals tiles', async () => {
        await renderInLocale('ko');
        expect(screen.getByText('최근 30일')).toBeInTheDocument();
        expect(screen.getByText('일')).toBeInTheDocument();
        expect(screen.getByText('주')).toBeInTheDocument();
        expect(screen.getByText('월')).toBeInTheDocument();
        expect(screen.getByText('랜덤전')).toBeInTheDocument();
        expect(screen.getByText('전투 수')).toBeInTheDocument();
        // Twice: the totals tile and the per-ship column. English abbreviates
        // the column ('Avg dmg' vs 'Avg damage'); Korean has no such
        // abbreviation convention, so both render the same word — expected,
        // not a duplicate-key bug.
        expect(screen.getAllByText('평균 데미지')).toHaveLength(2);
        // 평균 격침, not the corpus's 함선 격침: that form only means "per
        // battle" under a 전투 평균치 block header we do not render, and the
        // same page uses it for a raw record count elsewhere.
        expect(screen.getByText('평균 격침')).toBeInTheDocument();
        expect(screen.queryByText('Last 30 days')).toBeNull();
        expect(screen.getByText('함선 수')).toBeInTheDocument();
        expect(screen.queryByText('Frags/Battle')).toBeNull();
    });

    it('renders the Japanese window header, pills, mode caption and totals tiles', async () => {
        await renderInLocale('ja');
        expect(screen.getByText('直近30日間')).toBeInTheDocument();
        expect(screen.getByText('ランダム戦')).toBeInTheDocument();
        expect(screen.getByText('戦闘数')).toBeInTheDocument();
        // Twice, same reason as the Korean case above.
        expect(screen.getAllByText('平均ダメージ')).toHaveLength(2);
        // 平均撃沈数, not the corpus's bare 艦船撃沈: that form only means
        // "per battle" under a 期間平均値 section header we do not render.
        expect(screen.getByText('平均撃沈数')).toBeInTheDocument();
        expect(screen.queryByText('Avg damage')).toBeNull();
    });

    it('keeps Window WR and the WR columns in English, deliberately', async () => {
        // Not an oversight: "window" as a span is our own framing with no
        // corpus analogue (pinned in dictionaries.test.ts's NEEDS_NATIVE_CHECK),
        // and WR stays Latin in both locales by the documented rule — the
        // localized wows-numbers tables keep it Latin too.
        await renderInLocale('ko');
        expect(screen.getByText('Window WR')).toBeInTheDocument();
        expect(screen.getByText('WR Δ')).toBeInTheDocument();
        expect(screen.getByText('WR %')).toBeInTheDocument();
    });

    it('leaves the English card exactly as it was', async () => {
        await renderInLocale('en');
        expect(screen.getByText('Last 30 days')).toBeInTheDocument();
        expect(screen.getByText('Random Battles')).toBeInTheDocument();
        expect(screen.getByText('Frags/Battle')).toBeInTheDocument();
        expect(screen.getByText('Avg damage')).toBeInTheDocument();
    });
});

// The window pill is remembered per (realm, player, mode) so a reader who works
// in Week on one account returns to Week there — without imposing it on the next
// player they open, or on the Ranked tab of the same player.
describe('window pill persistence', () => {
    const KEY = 'battlestats:battle-history:window';

    beforeEach(() => {
        mockFetchSharedJson.mockReset();
        mockTrackEvent.mockReset();
        window.localStorage.clear();
        mockFetchSharedJson.mockResolvedValue({ data: buildPayload({ by_day: [] }), headers: {} });
    });

    const mainWindows = (): string[] =>
        mainFetchCalls().map((c) => {
            const u = (c as unknown[])[0] as string;
            return new URL(u, 'http://t').searchParams.get('window') ?? '';
        });

    test('a stored pick drives the FIRST main fetch — the default window is never requested', async () => {
        window.localStorage.setItem(`${KEY}:na:lil_boots:random`, 'week');
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        // The whole point of gating the fetch on the restored value: one request
        // for the remembered window, not a default fetch followed by a correction.
        expect(mainWindows()).toEqual(['week']);
        expect(screen.getByRole('button', { name: /^Week$/ })).toHaveAttribute('aria-pressed', 'true');
    });

    test('clicking a pill persists it under the realm+player+mode scope', async () => {
        // Recent battles so the Month pill is not dimmed by the empty-window
        // disable — a disabled pill's onClick returns before it can persist.
        const day = (offset: number): string => {
            const d = new Date();
            d.setUTCDate(d.getUTCDate() - offset);
            return d.toISOString().slice(0, 10);
        };
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({
                by_day: [
                    { date: day(1), battles: 4, wins: 2, damage: 0, frags: 0 },
                    { date: day(0), battles: 3, wins: 1, damage: 0, frags: 0 },
                ],
            }),
            headers: {},
        });
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        await act(async () => { screen.getByRole('button', { name: /^Month$/ }).click(); });
        expect(window.localStorage.getItem(`${KEY}:na:lil_boots:random`)).toBe('month');
    });

    test('the 90d pill round-trips: click persists it, reload restores it', async () => {
        // The extended pill goes through the exact same
        // isStickyWindow/VISIBLE_WINDOWS membership check as every other pill —
        // this pins that it was actually added to that set, not just typed.
        // Recent battles so the 90d pill is not dimmed by the empty-window
        // disable — a disabled pill's onClick returns before it can persist.
        const day = (offset: number): string => {
            const d = new Date();
            d.setUTCDate(d.getUTCDate() - offset);
            return d.toISOString().slice(0, 10);
        };
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({
                by_day: [{ date: day(0), battles: 3, wins: 1, damage: 0, frags: 0 }],
            }),
            headers: {},
        });
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        await act(async () => { screen.getByRole('button', { name: /^90d$/ }).click(); });
        expect(window.localStorage.getItem(`${KEY}:na:lil_boots:random`)).toBe('ninety');
        cleanup();

        mockFetchSharedJson.mockClear();
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        expect(mainWindows()).toEqual(['ninety']);
        expect(screen.getByRole('button', { name: /^90d$/ })).toHaveAttribute('aria-pressed', 'true');
    });

    test('the pick does NOT cross realms for the same player name', async () => {
        // The same name can be a different account on another realm, so an EU
        // Wara39 must not inherit the NA Wara39's window.
        window.localStorage.setItem(`${KEY}:na:lil_boots:random`, 'day');
        render(<BattleHistoryCard playerName="lil_boots" realm="eu" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        expect(mainWindows()).toEqual(['month']);
        expect(screen.getByRole('button', { name: /^Month$/ })).toHaveAttribute('aria-pressed', 'true');
    });

    test('the pick does NOT cross players, and matches case-insensitively', async () => {
        window.localStorage.setItem(`${KEY}:na:lil_boots:random`, 'day');
        render(<BattleHistoryCard playerName="someone_else" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        expect(mainWindows()).toEqual(['month']);
        cleanup();

        // A link that differs only in case is the same account, so it resolves
        // to the same stored pick.
        mockFetchSharedJson.mockClear();
        render(<BattleHistoryCard playerName="LIL_BOOTS" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        expect(mainWindows()).toEqual(['day']);
    });

    test('a stored window with no pill is ignored — the reader is never stranded', async () => {
        // `year` is a real BattleHistoryWindow the backend still accepts, but no
        // pill exposes it: restoring it would show a window the reader cannot see
        // selected and cannot leave by clicking the pill they are on.
        window.localStorage.setItem(`${KEY}:na:lil_boots:random`, 'year');
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        expect(mainWindows()).toEqual(['month']);
        cleanup();

        mockFetchSharedJson.mockClear();
        window.localStorage.setItem(`${KEY}:na:lil_boots:random`, 'not-a-window');
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        expect(mainWindows()).toEqual(['month']);
    });

    test('the Ranked tab keeps its own pick, separate from Activity', async () => {
        // The player page mounts this card twice over different data; a window
        // chosen on one must not move the other underneath the reader.
        window.localStorage.setItem(`${KEY}:na:lil_boots:random`, 'week');
        mockByMode({ by_day: [] });
        render(<BattleHistoryCard playerName="lil_boots" realm="na" mode="ranked" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        expect(mainWindows()).toEqual(['month']);
    });
});

// The card opens on Month. A player with nothing in the last 30 days would see
// an empty card, so the strip's own data promotes them to 90d once it lands.
describe('30d-empty fallback to 90d', () => {
    const KEY = 'battlestats:battle-history:window';
    const utcDay = (o: number): string => {
        const d = new Date();
        d.setUTCDate(d.getUTCDate() - o);
        return d.toISOString().slice(0, 10);
    };
    // Battles 45 days back: inside the 90-day strip, outside the 30-day default.
    const lapsedStrip = [{ date: utcDay(45), battles: 9, wins: 5, damage: 0, frags: 0 }];

    beforeEach(() => {
        mockFetchSharedJson.mockReset();
        mockTrackEvent.mockReset();
        window.localStorage.clear();
    });

    test('promotes a 30d-empty player to 90d, and grays the narrower pills', async () => {
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({ by_day: lapsedStrip }), headers: {},
        });
        render(<BattleHistoryCard embedded playerName="lapsed" realm="na" />);
        await waitFor(() => {
            expect(screen.getByRole('button', { name: /^90d$/ })).toHaveAttribute('aria-pressed', 'true');
        });
        // ...and the windows with nothing in them dim, per the usual rule.
        expect(screen.getByRole('button', { name: /^Day$/ })).toBeDisabled();
        expect(screen.getByRole('button', { name: /^Week$/ })).toBeDisabled();
        expect(screen.getByRole('button', { name: /^Month$/ })).toBeDisabled();
    });

    test('the fallback is a derivation, not a pick — it never persists', async () => {
        // If it wrote the pref, a returning player would be pinned to 90d long
        // after they start playing again and Month becomes the better view.
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({ by_day: lapsedStrip }), headers: {},
        });
        render(<BattleHistoryCard embedded playerName="lapsed" realm="na" />);
        await waitFor(() => {
            expect(screen.getByRole('button', { name: /^90d$/ })).toHaveAttribute('aria-pressed', 'true');
        });
        expect(window.localStorage.getItem(`${KEY}:na:lapsed:random`)).toBeNull();
    });

    test('a player WITH recent battles stays on Month', async () => {
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({
                by_day: [{ date: utcDay(2), battles: 6, wins: 3, damage: 0, frags: 0 }],
            }),
            headers: {},
        });
        render(<BattleHistoryCard embedded playerName="active" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        expect(screen.getByRole('button', { name: /^Month$/ })).toHaveAttribute('aria-pressed', 'true');
    });

    test('a stored pick outranks the fallback', async () => {
        // The reader chose Week on this player before. Even though Week is empty
        // and the fallback would otherwise fire, their choice wins — the pill
        // simply shows an empty window, which is what they asked to see.
        window.localStorage.setItem(`${KEY}:na:lapsed:random`, 'week');
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({ by_day: lapsedStrip }), headers: {},
        });
        render(<BattleHistoryCard embedded playerName="lapsed" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        expect(screen.getByRole('button', { name: /^Week$/ })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: /^90d$/ })).toHaveAttribute('aria-pressed', 'false');
    });

    test('a player with nothing in 75 days either stays on Month', async () => {
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({ by_day: [] }), headers: {},
        });
        render(<BattleHistoryCard embedded playerName="silent" realm="na" />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        // There is no wider span carrying data, so promoting would gain nothing.
        expect(screen.getByRole('button', { name: /^Month$/ })).toHaveAttribute('aria-pressed', 'true');
    });
});

describe('onWindowChange — publishing the live window to a sibling surface', () => {
    const KEY = 'battlestats:battle-history:window';
    const utcDay = (o: number): string => {
        const d = new Date();
        d.setUTCDate(d.getUTCDate() - o);
        return d.toISOString().slice(0, 10);
    };

    beforeEach(() => {
        mockFetchSharedJson.mockReset();
        mockTrackEvent.mockReset();
        window.localStorage.clear();
    });

    test('reports the default window on a clean mount', async () => {
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({ by_day: [{ date: utcDay(1), battles: 4, wins: 2, damage: 0, frags: 0 }] }),
            headers: {},
        });
        const onWindowChange = jest.fn();
        render(<BattleHistoryCard embedded playerName="lil_boots" realm="na" onWindowChange={onWindowChange} />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        expect(onWindowChange.mock.calls.map(([w]) => w)).toEqual(['month']);
    });

    test('reports a STORED pick, not just a click — and never the default first', async () => {
        // Two wedges in one. A host wired only to the pill handler would leave
        // a reader whose remembered pick is 90d reading a 30d sibling. And an
        // UNGATED publish would announce the default 'month' on mount and
        // correct to 'ninety' a tick later — which a host that fetches per
        // window renders as a visible flash of the wrong span. Hence the
        // sequence assertion: 'ninety' alone, never ['month', 'ninety'].
        window.localStorage.setItem(`${KEY}:na:lil_boots:random`, 'ninety');
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({ by_day: [{ date: utcDay(1), battles: 4, wins: 2, damage: 0, frags: 0 }] }),
            headers: {},
        });
        const onWindowChange = jest.fn();
        render(<BattleHistoryCard embedded playerName="lil_boots" realm="na" onWindowChange={onWindowChange} />);
        await waitFor(() => {
            expect(onWindowChange).toHaveBeenCalled();
        });
        expect(onWindowChange.mock.calls.map(([w]) => w)).toEqual(['ninety']);
    });

    test('a pref naming a RETIRED pill is ignored, and the card opens on Month', async () => {
        // 45d/60d/75d were selectable until 2026-09-19 and their picks are still
        // in readers' localStorage. They are no longer in VISIBLE_WINDOWS, so
        // isStickyWindow rejects them and the card opens on its default rather
        // than announcing a window with no pill to show for it — which would
        // strand the reader on a span they can neither see selected nor leave.
        window.localStorage.setItem(`${KEY}:na:lil_boots:random`, 'sixty');
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({ by_day: [{ date: utcDay(1), battles: 4, wins: 2, damage: 0, frags: 0 }] }),
            headers: {},
        });
        const onWindowChange = jest.fn();
        render(<BattleHistoryCard embedded playerName="lil_boots" realm="na" onWindowChange={onWindowChange} />);
        await waitFor(() => {
            expect(onWindowChange).toHaveBeenCalled();
        });
        expect(onWindowChange.mock.calls.map(([w]) => w)).toEqual(['month']);
        expect(screen.getByRole('button', { name: /^Month$/ })).toHaveAttribute('aria-pressed', 'true');
    });

    test('reports the automatic 90d fallback for a 30d-empty player', async () => {
        // The other windowless path: nobody clicked, no pref is stored, and the
        // card promotes itself to 90d.
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({ by_day: [{ date: utcDay(45), battles: 9, wins: 5, damage: 0, frags: 0 }] }),
            headers: {},
        });
        const onWindowChange = jest.fn();
        render(<BattleHistoryCard embedded playerName="lapsed" realm="na" onWindowChange={onWindowChange} />);
        await waitFor(() => {
            expect(onWindowChange).toHaveBeenLastCalledWith('ninety');
        });
        // The fallback is a correction to the default, so 'month' legitimately
        // precedes it here — unlike the stored-pick case above, nothing knew
        // the wider window was wanted until the strip landed.
        expect(onWindowChange.mock.calls.map(([w]) => w)).toEqual(['month', 'ninety']);
    });

    test('reports a pill click', async () => {
        mockFetchSharedJson.mockResolvedValue({
            data: buildPayload({
                by_day: [
                    { date: utcDay(1), battles: 4, wins: 2, damage: 0, frags: 0 },
                    { date: utcDay(0), battles: 3, wins: 1, damage: 0, frags: 0 },
                ],
            }),
            headers: {},
        });
        const onWindowChange = jest.fn();
        render(<BattleHistoryCard embedded playerName="lil_boots" realm="na" onWindowChange={onWindowChange} />);
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
        await act(async () => { screen.getByRole('button', { name: /^Week$/ }).click(); });
        expect(onWindowChange.mock.calls.map(([w]) => w)).toEqual(['month', 'week']);
    });
});
