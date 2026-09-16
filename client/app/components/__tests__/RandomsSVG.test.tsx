import React from 'react';
import { act, render, screen, waitFor, fireEvent } from '@testing-library/react';
import * as d3 from 'd3';
import RandomsSVG from '../RandomsSVG';

jest.mock('d3', () => {
    const chain: any = {
        append: jest.fn(() => chain),
        attr: jest.fn(() => chain),
        style: jest.fn(() => chain),
        text: jest.fn(() => chain),
        call: jest.fn(() => chain),
        select: jest.fn(() => chain),
        selectAll: jest.fn(() => chain),
        remove: jest.fn(() => chain),
        data: jest.fn(() => chain),
        enter: jest.fn(() => chain),
        classed: jest.fn(() => chain),
        each: jest.fn(() => chain),
        insert: jest.fn(() => chain),
        node: jest.fn(() => ({ getBBox: () => ({ x: 0, y: 0, width: 24, height: 10 }) })),
        filter: jest.fn(() => chain),
        on: jest.fn(() => chain),
        transition: jest.fn(() => chain),
        duration: jest.fn(() => chain),
    };

    const createScale = () => {
        const scale = ((value: number) => value) as ((value: number) => number) & {
            domain: jest.Mock;
            range: jest.Mock;
            padding: jest.Mock;
            bandwidth: jest.Mock;
            step: jest.Mock;
            interpolate: jest.Mock;
            clamp: jest.Mock;
        };
        scale.domain = jest.fn(() => scale);
        scale.range = jest.fn(() => scale);
        scale.padding = jest.fn(() => scale);
        scale.bandwidth = jest.fn(() => 24);
        scale.step = jest.fn(() => 25);
        // Used by the color scale that BattleHistoryTreemaps builds at module
        // load (pulled in transitively via BattleHistoryCard's fetch helpers).
        scale.interpolate = jest.fn(() => scale);
        scale.clamp = jest.fn(() => scale);
        return scale;
    };

    const createAxis = () => {
        const axis = jest.fn(() => chain) as jest.Mock & {
            ticks: jest.Mock;
            tickSize: jest.Mock;
            tickFormat: jest.Mock;
            tickSizeOuter: jest.Mock;
            tickPadding: jest.Mock;
        };
        axis.ticks = jest.fn(() => axis);
        axis.tickSize = jest.fn(() => axis);
        axis.tickFormat = jest.fn(() => axis);
        axis.tickSizeOuter = jest.fn(() => axis);
        axis.tickPadding = jest.fn(() => axis);
        return axis;
    };

    return {
        select: jest.fn(() => chain),
        max: jest.fn((values: number[]) => (values.length > 0 ? Math.max(...values) : undefined)),
        scaleLinear: jest.fn(() => createScale()),
        scaleBand: jest.fn(() => createScale()),
        axisBottom: jest.fn(() => createAxis()),
        axisLeft: jest.fn(() => createAxis()),
        format: jest.fn(() => (value: number) => String(value)),
        interpolateLab: jest.fn(),
    };
});

const mockFetch = jest.fn();

describe('RandomsSVG tier filters', () => {
    beforeEach(() => {
        mockFetch.mockReset();
        global.fetch = mockFetch as unknown as typeof fetch;
    });

    it('suppresses the empty-state text while the chart is still loading', async () => {
        mockFetch.mockImplementation(() => new Promise(() => { }));

        render(<RandomsSVG playerId={102} playerName="Tester" isLoading />);

        await waitFor(() => {
            expect(screen.getByText('Loading random battles...')).toBeInTheDocument();
        });

        expect(screen.queryByText('No ships match the selected filters.')).not.toBeInTheDocument();
    });

    it('keeps the Tier All button selected by default when low-tier rows exist in the payload', async () => {
        mockFetch.mockResolvedValue({
            ok: true,
            headers: {
                get: (name: string) => {
                    if (name.toLowerCase() === 'content-type') {
                        return 'application/json';
                    }
                    if (name === 'X-Randoms-Updated-At') {
                        return '2026-03-19T00:00:00Z';
                    }
                    return null;
                },
            },
            json: async () => ([
                { ship_id: 1, ship_name: 'Low Tier Ship', ship_chart_name: 'Low Tier Ship', ship_tier: 4, ship_type: 'Destroyer', pvp_battles: 15, wins: 8, win_ratio: 0.533 },
                { ship_id: 2, ship_name: 'Tier Six Ship', ship_chart_name: 'Tier Six Ship', ship_tier: 6, ship_type: 'Cruiser', pvp_battles: 40, wins: 23, win_ratio: 0.575 },
                { ship_id: 3, ship_name: 'Tier Five Ship', ship_chart_name: 'Tier Five Ship', ship_tier: 5, ship_type: 'Battleship', pvp_battles: 28, wins: 15, win_ratio: 0.536 },
            ]),
        });

        render(<RandomsSVG playerId={101} playerName="Tester" />);

        await waitFor(() => {
            expect(screen.getByRole('button', { name: 'T6' })).toBeInTheDocument();
        });

        expect(screen.getAllByRole('button', { name: 'All' })[1]).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: 'T6' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: 'T5' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.queryByRole('button', { name: 'T4' })).not.toBeInTheDocument();
    });

    it('re-applies a drill-down filter after tabbing away and back', async () => {
        // The wedge this guards. RandomsSVG re-seeds the pills to defaults on
        // every fetch resolve, and `ttlMs: 0` means a fetch resolves on every
        // mount. On the FIRST drill-down `allShips` starts empty, so the filter
        // happens to land after the defaults. On a RETURN visit the
        // module-cache seed makes `allShips` non-empty at mount, the filter
        // applies first, and the arriving payload used to wipe it — so the
        // first drill-down worked and every one after it arrived unfiltered.
        mockFetch.mockResolvedValue({
            ok: true,
            headers: {
                get: (name: string) => {
                    if (name.toLowerCase() === 'content-type') return 'application/json';
                    if (name === 'X-Randoms-Updated-At') return '2026-03-19T00:00:00Z';
                    return null;
                },
            },
            json: async () => ([
                { ship_id: 1, ship_name: 'Rodney', ship_chart_name: 'Rodney', ship_tier: 7, ship_type: 'Battleship', pvp_battles: 58, wins: 33, win_ratio: 0.57 },
                { ship_id: 2, ship_name: 'Nakhimov', ship_chart_name: 'Nakhimov', ship_tier: 10, ship_type: 'AirCarrier', pvp_battles: 267, wins: 134, win_ratio: 0.5 },
                { ship_id: 3, ship_name: 'Tier Six Ship', ship_chart_name: 'Tier Six Ship', ship_tier: 6, ship_type: 'Cruiser', pvp_battles: 40, wins: 23, win_ratio: 0.575 },
            ]),
        });

        // First drill-down. Also primes the module-scope repaint cache.
        const first = render(
            <RandomsSVG
                playerId={909}
                playerName="Tester"
                filterRequest={{ shipTypes: ['Battleship'], tiers: [7], nonce: 1 }}
            />,
        );
        await waitFor(() => {
            expect(screen.getByRole('button', { name: 'Battleship' })).toHaveAttribute('aria-pressed', 'true');
        });

        // Tab away to Profile: the Ships panel unmounts.
        first.unmount();

        // Click a different cell and come back. This mount is seeded, which is
        // what made the ordering flip.
        const callsBefore = mockFetch.mock.calls.length;
        render(
            <RandomsSVG
                playerId={909}
                playerName="Tester"
                filterRequest={{ shipTypes: ['Aircraft Carrier'], tiers: [10], nonce: 2 }}
            />,
        );

        // Wait for the remount's own refetch to RESOLVE before asserting. The
        // filter lands immediately off the seed, so asserting any earlier
        // passes even when the arriving payload then wipes it.
        await waitFor(() => {
            expect(mockFetch.mock.calls.length).toBeGreaterThan(callsBefore);
        });
        await act(async () => {
            await Promise.resolve();
            await Promise.resolve();
        });

        await waitFor(() => {
            // "Aircraft Carrier" (tier/type payload) must resolve to
            // "AirCarrier" (randoms payload).
            expect(screen.getByRole('button', { name: 'AirCarrier' })).toHaveAttribute('aria-pressed', 'true');
        });
        expect(screen.getByRole('button', { name: 'T10' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: 'Battleship' })).toHaveAttribute('aria-pressed', 'false');
    });

    it('repaints the prior result instantly on remount (tab-switch return), without waiting on a fetch', async () => {
        const okResponse = {
            ok: true,
            headers: {
                get: (name: string) => {
                    if (name.toLowerCase() === 'content-type') return 'application/json';
                    if (name === 'X-Randoms-Updated-At') return '2026-03-19T00:00:00Z';
                    return null;
                },
            },
            json: async () => ([
                { ship_id: 2, ship_name: 'Tier Six Ship', ship_chart_name: 'Tier Six Ship', ship_tier: 6, ship_type: 'Cruiser', pvp_battles: 40, wins: 23, win_ratio: 0.575 },
            ]),
        };

        // First mount resolves and populates the module-scope last-result cache.
        mockFetch.mockResolvedValue(okResponse);
        const { unmount } = render(<RandomsSVG playerId={909} playerName="Tester" />);
        await waitFor(() => {
            expect(screen.getByRole('button', { name: 'T6' })).toBeInTheDocument();
        });

        // Tab away → component unmounts.
        unmount();

        // Tab back: even if the network is now slow (pending), the prior result
        // must paint immediately from the seed — no loading flash, no stale ladder.
        mockFetch.mockImplementation(() => new Promise(() => { }));
        render(<RandomsSVG playerId={909} playerName="Tester" />);
        expect(screen.getByRole('button', { name: 'T6' })).toBeInTheDocument();
        expect(screen.queryByText('Loading random battles...')).not.toBeInTheDocument();
    });
});

// Build a Response-like stub whose JSON body + headers depend on which endpoint
// (randoms vs battle-history) the shared fetch layer is calling.
const buildUrlRoutedFetch = (
    randomsRows: unknown[],
    byShip: unknown[],
) => (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString();
    // sharedJsonFetch strips the trailing slash from /api/ paths, so the real
    // request is ".../battle-history?..." (no trailing slash).
    const isBattleHistory = url.includes('/battle-history');
    const body = isBattleHistory ? { by_ship: byShip } : randomsRows;
    return Promise.resolve({
        ok: true,
        headers: {
            get: (name: string) => {
                if (name.toLowerCase() === 'content-type') return 'application/json';
                if (name === 'X-Randoms-Updated-At') return '2026-03-19T00:00:00Z';
                return null;
            },
        },
        json: async () => body,
    });
};

describe('RandomsSVG min-battles slider + window filter', () => {
    beforeEach(() => {
        mockFetch.mockReset();
        global.fetch = mockFetch as unknown as typeof fetch;
    });

    // Two eligible ships. The min-battles cutoff always defaults to 0 (show
    // all), so the chart is non-empty by default; the tier/type filter buttons
    // are derived from the full ship set, so the observable proof of the new
    // filters is the empty-state text (chartData → 0) and the slider's clamped
    // value label.
    const RANDOMS_ROWS = [
        { ship_id: 1, ship_name: 'Grind Ship', ship_chart_name: 'Grind Ship', ship_tier: 8, ship_type: 'Cruiser', pvp_battles: 120, wins: 66, win_ratio: 0.55 },
        { ship_id: 2, ship_name: 'Dabble Ship', ship_chart_name: 'Dabble Ship', ship_tier: 7, ship_type: 'Destroyer', pvp_battles: 60, wins: 33, win_ratio: 0.55 },
    ];

    it('shows the cutoff value and clamps it to the grindiest ship', async () => {
        mockFetch.mockImplementation(buildUrlRoutedFetch(RANDOMS_ROWS, []));

        render(<RandomsSVG playerId={201} playerName="TesterA" />);

        // Wait for the randoms payload to load so the slider's ceiling reflects
        // the grindiest ship (120) rather than the pre-data floor.
        await screen.findByRole('button', { name: 'T8' });
        const slider = screen.getByLabelText('Minimum lifetime random battles to show a ship');
        // The cutoff always defaults to 0 (show all).
        expect(screen.getByText(/≥\s*0$/)).toBeInTheDocument();

        // A mid-range value below the ceiling passes through unchanged.
        fireEvent.change(slider, { target: { value: '50' } });
        expect(screen.getByText(/≥\s*50/)).toBeInTheDocument();

        // Beyond the grindiest ship (120) the cutoff clamps down so the chart
        // never silently empties.
        fireEvent.change(slider, { target: { value: '999' } });
        await waitFor(() => {
            expect(screen.getByText(/≥\s*120/)).toBeInTheDocument();
        });
        expect(screen.queryByText('No ships match the selected filters.')).not.toBeInTheDocument();
    });

    it('adds a Min WR slider that defaults to 0 and filters ships below the cutoff', async () => {
        // Best ship sits at 54.6% (its whole-% ceiling rounds up to 55); the
        // other at 42%. A 55% cutoff therefore clears both.
        mockFetch.mockImplementation(buildUrlRoutedFetch([
            { ship_id: 1, ship_name: 'Low WR', ship_chart_name: 'Low WR', ship_tier: 8, ship_type: 'Cruiser', pvp_battles: 200, wins: 84, win_ratio: 0.42 },
            { ship_id: 2, ship_name: 'Best WR', ship_chart_name: 'Best WR', ship_tier: 8, ship_type: 'Cruiser', pvp_battles: 200, wins: 109, win_ratio: 0.546 },
        ], []));

        render(<RandomsSVG playerId={556} playerName="TesterWR" />);

        // Wait for the payload so the slider's ceiling reflects the best ship WR.
        await screen.findByRole('button', { name: 'T8' });
        const wrSlider = screen.getByLabelText('Minimum win rate to show a ship');
        // Defaults to 0 (no WR filtering); ceiling is the best ship's rounded WR.
        expect(screen.getByText(/≥\s*0%/)).toBeInTheDocument();
        expect(wrSlider).toHaveAttribute('min', '0');
        expect(wrSlider).toHaveAttribute('max', '55');
        expect(screen.queryByText('No ships match the selected filters.')).not.toBeInTheDocument();

        // A 55% cutoff drops both ships (best is 54.6%) → chart empties.
        fireEvent.change(wrSlider, { target: { value: '55' } });
        await waitFor(() => {
            expect(screen.getByText('No ships match the selected filters.')).toBeInTheDocument();
        });
    });

    it('Clear returns every filter on the tab to its default', async () => {
        // Window payload is non-empty so Window Only is selectable — Clear has
        // to put the activity mode back too, not just the pills.
        mockFetch.mockImplementation(buildUrlRoutedFetch(RANDOMS_ROWS, [
            { ship_id: 1, ship_name: 'Grind Ship', battles: 12, wins: 7 },
        ]));

        render(<RandomsSVG playerId={777} playerName="TesterClear" />);
        await screen.findByRole('button', { name: 'T8' });

        // Narrow everything: one type, one tier, both cutoffs raised.
        fireEvent.click(screen.getByRole('button', { name: 'Cruiser' }));
        fireEvent.click(screen.getByRole('button', { name: 'T8' }));
        fireEvent.change(screen.getByLabelText('Minimum lifetime random battles to show a ship'), { target: { value: '50' } });
        fireEvent.change(screen.getByLabelText('Minimum win rate to show a ship'), { target: { value: '40' } });

        await waitFor(() => {
            expect(screen.getByRole('button', { name: 'Destroyer' })).toHaveAttribute('aria-pressed', 'false');
        });
        expect(screen.getByRole('button', { name: 'T7' })).toHaveAttribute('aria-pressed', 'false');
        expect(screen.getByText(/≥\s*50$/)).toBeInTheDocument();
        expect(screen.getByText(/≥\s*40%/)).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'Clear' }));

        // Every control is back where the tab opens: all types, all tiers,
        // both cutoffs at zero, activity mode All.
        await waitFor(() => {
            expect(screen.getByRole('button', { name: 'Destroyer' })).toHaveAttribute('aria-pressed', 'true');
        });
        expect(screen.getByRole('button', { name: 'Cruiser' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: 'T7' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: 'T8' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByText(/≥\s*0$/)).toBeInTheDocument();
        expect(screen.getByText(/≥\s*0%/)).toBeInTheDocument();
        expect(screen.getByRole('radio', { name: 'All' })).toHaveAttribute('aria-checked', 'true');
    });

    it('locks the Activity filter to All when no ships were played in the window', async () => {
        // Empty 30d window payload: neither ship was played recently.
        mockFetch.mockImplementation(buildUrlRoutedFetch(RANDOMS_ROWS, []));

        render(<RandomsSVG playerId={202} playerName="TesterB" />);

        // Default (Activity: All): both ships eligible → chart shown.
        await screen.findByLabelText('Minimum lifetime random battles to show a ship');
        expect(screen.queryByText('No ships match the selected filters.')).not.toBeInTheDocument();

        // No window activity → All stays selected; Window Only locks.
        expect(screen.getByRole('radio', { name: 'All' })).toHaveAttribute('aria-checked', 'true');
        expect(screen.queryByRole('radio', { name: 'Recent' })).not.toBeInTheDocument();
        const windowOnly = screen.getByRole('radio', { name: 'Window Only' });
        expect(windowOnly).toBeDisabled();

        // Clicking the locked Window Only is a no-op: the chart stays populated
        // (never routed to the empty Window-Only view).
        fireEvent.click(windowOnly);
        expect(screen.queryByText('No ships match the selected filters.')).not.toBeInTheDocument();
        expect(screen.getByRole('radio', { name: 'All' })).toHaveAttribute('aria-checked', 'true');
    });

    it('stays on the All default (no auto-Recent) when the window has battles', async () => {
        // Grind Ship is in the 30d window: the min-battles cutoff stays at its
        // 0 default (full recent mix visible) while Activity stays on the All
        // default. Recent mode was removed, so the window no longer auto-switches
        // the mode.
        mockFetch.mockImplementation(buildUrlRoutedFetch(
            RANDOMS_ROWS,
            [{ ship_id: 1, ship_name: 'Grind Ship', ship_tier: 8, ship_type: 'Cruiser', battles: 12, wins: 7, delta_win_rate: 2.1 }],
        ));

        render(<RandomsSVG playerId={203} playerName="TesterC" />);

        const all = await screen.findByRole('radio', { name: 'All' });
        await screen.findByRole('button', { name: 'T8' });
        expect(all).toBeChecked();
        expect(screen.queryByRole('radio', { name: 'Recent' })).not.toBeInTheDocument();
        expect(screen.getByText(/≥\s*0$/)).toBeInTheDocument();
        // The in-window ship keeps the chart populated.
        expect(screen.queryByText('No ships match the selected filters.')).not.toBeInTheDocument();
    });

    it('keeps the all-ships default (cutoff 0) when the window has no battles', async () => {
        // Empty window payload: the default filters stay put (Activity: All,
        // cutoff 0), so a player with no recent activity still sees all their
        // ships.
        mockFetch.mockImplementation(buildUrlRoutedFetch(RANDOMS_ROWS, []));

        render(<RandomsSVG playerId={204} playerName="TesterD" />);

        const all = await screen.findByRole('radio', { name: 'All' });
        await screen.findByRole('button', { name: 'T8' });
        expect(all).toBeChecked();
        expect(screen.getByText(/≥\s*0$/)).toBeInTheDocument();
        expect(screen.queryByText('No ships match the selected filters.')).not.toBeInTheDocument();
    });

    it('keeps the cutoff at 0 even for a large dormant roster', async () => {
        // 38 ships, descending battle counts, none played in the window. The old
        // dormant default (top-35 cutoff) is gone: the cutoff stays at 0 and
        // every ship is shown.
        const manyRows = Array.from({ length: 38 }, (_, i) => ({
            ship_id: i + 1,
            ship_name: `Ship ${i + 1}`,
            ship_chart_name: `Ship ${i + 1}`,
            ship_tier: 8,
            ship_type: 'Cruiser',
            pvp_battles: 380 - i * 10,
            wins: Math.round((380 - i * 10) * 0.5),
            win_ratio: 0.5,
        }));
        mockFetch.mockImplementation(buildUrlRoutedFetch(manyRows, []));

        render(<RandomsSVG playerId={205} playerName="TesterE" />);

        await screen.findByRole('button', { name: 'T8' });
        await waitFor(() => {
            expect(screen.getByText(/≥\s*0$/)).toBeInTheDocument();
        });
        expect(screen.queryByText('No ships match the selected filters.')).not.toBeInTheDocument();
    });
});
describe('RandomsSVG compact variant (Activity tab)', () => {
    // The compact chart's rows are never in the DOM (d3 is mocked here), so the
    // observable is what the draw binds: d3's shared chain `.data(rows)`. Each
    // draw binds TWICE — the full row set, then the delta-pill subset (ships
    // with a computable window delta) — so the row set is the widest bind.
    const boundShipNames = (): string[][] => {
        const chain = (d3.select as unknown as jest.Mock)(null) as unknown as {
            data: jest.Mock;
        };
        return chain.data.mock.calls
            .map(([rows]) => rows)
            .filter((rows: unknown): rows is Array<{ ship_name?: string }> => (
                Array.isArray(rows) && rows.every((row) => row && typeof row === 'object' && 'ship_name' in row)
            ))
            .map((rows) => rows.map((row) => row.ship_name as string));
    };
    const widestBind = (): string[] => boundShipNames()
        .reduce((widest, rows) => (rows.length >= widest.length ? rows : widest), [] as string[]);

    // A T4 ship played in the window, a T8 played in the window, and a T10 with
    // a big lifetime grind that was NOT played in the window.
    const RANDOMS_ROWS = [
        { ship_id: 1, ship_name: 'Dormant Ten', ship_chart_name: 'Dormant Ten', ship_tier: 10, ship_type: 'Battleship', pvp_battles: 900, wins: 500, win_ratio: 0.556 },
        { ship_id: 2, ship_name: 'Window Eight', ship_chart_name: 'Window Eight', ship_tier: 8, ship_type: 'Cruiser', pvp_battles: 120, wins: 66, win_ratio: 0.55 },
        { ship_id: 3, ship_name: 'Window Four', ship_chart_name: 'Window Four', ship_tier: 4, ship_type: 'Destroyer', pvp_battles: 30, wins: 16, win_ratio: 0.533 },
    ];
    const WINDOW_BY_SHIP = [
        { ship_id: 2, ship_name: 'Window Eight', battles: 12, delta_win_rate: 0.4 },
        { ship_id: 3, ship_name: 'Window Four', battles: 5, delta_win_rate: null },
    ];

    beforeEach(() => {
        mockFetch.mockReset();
        global.fetch = mockFetch as unknown as typeof fetch;
        const chain = (d3.select as unknown as jest.Mock)(null) as unknown as {
            data: jest.Mock; on: jest.Mock;
        };
        chain.data.mockClear();
        chain.on.mockClear();
    });

    it('draws only ships played in the window, tier floor and all, with no controls', async () => {
        mockFetch.mockImplementation(buildUrlRoutedFetch(RANDOMS_ROWS, WINDOW_BY_SHIP));

        render(<RandomsSVG compact playerId={301} playerName="TesterCompact" />);

        await waitFor(() => {
            expect(boundShipNames().length).toBeGreaterThan(0);
        });

        const rows = widestBind();
        // The dormant T10 is excluded despite dwarfing both others in lifetime
        // battles; the T4 is INCLUDED, because the tier-5 floor that the Ships
        // tab applies would act invisibly here (there are no pills to undo it).
        expect(rows).toEqual(['Window Eight', 'Window Four']);

        // None of the Ships-tab furniture comes over.
        expect(screen.queryByRole('button', { name: 'T8' })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Window Only' })).not.toBeInTheDocument();
        expect(screen.queryByLabelText('Minimum lifetime random battles to show a ship')).not.toBeInTheDocument();
        expect(screen.queryByText(/Randoms data last refreshed/)).not.toBeInTheDocument();
    });

    it('never paints the lifetime roster before the window join settles', async () => {
        // The trap: windowStats starts empty, and the full variant treats an
        // empty join as "no window data, show everything". The compact variant
        // must hold instead, or it flashes every lifetime ship and then culls.
        let releaseWindow: ((value: unknown) => void) | null = null;
        mockFetch.mockImplementation((input: RequestInfo | URL) => {
            const url = typeof input === 'string' ? input : input.toString();
            if (url.includes('/battle-history')) {
                return new Promise((resolve) => {
                    releaseWindow = () => resolve({
                        ok: true,
                        headers: { get: (n: string) => (n.toLowerCase() === 'content-type' ? 'application/json' : null) },
                        json: async () => ({ by_ship: WINDOW_BY_SHIP }),
                    });
                });
            }
            return buildUrlRoutedFetch(RANDOMS_ROWS, WINDOW_BY_SHIP)(input);
        });

        render(<RandomsSVG compact playerId={302} playerName="TesterCompactB" />);

        await waitFor(() => {
            expect(screen.getByText('Loading ships played in this window...')).toBeInTheDocument();
        });
        expect(boundShipNames()).toEqual([]);

        await act(async () => {
            releaseWindow?.(null);
            await Promise.resolve();
        });

        await waitFor(() => {
            expect(boundShipNames().length).toBeGreaterThan(0);
        });
        expect(widestBind()).toEqual(['Window Eight', 'Window Four']);
    });

    it('says the window is empty rather than falling back to the lifetime roster', async () => {
        mockFetch.mockImplementation(buildUrlRoutedFetch(RANDOMS_ROWS, []));

        render(<RandomsSVG compact playerId={303} playerName="TesterCompactC" />);

        await waitFor(() => {
            expect(screen.getByText('No ships played in this window.')).toBeInTheDocument();
        });
        expect(boundShipNames()).toEqual([]);
    });

    it('re-reads the join when the host card moves its window pill', async () => {
        mockFetch.mockImplementation(buildUrlRoutedFetch(RANDOMS_ROWS, WINDOW_BY_SHIP));

        const { rerender } = render(
            <RandomsSVG compact playerId={304} playerName="TesterCompactD" windowName="month" />,
        );

        await waitFor(() => {
            expect(boundShipNames().length).toBeGreaterThan(0);
        });
        const historyUrls = () => mockFetch.mock.calls
            .map(([input]) => (typeof input === 'string' ? input : String(input)))
            .filter((url) => url.includes('/battle-history'));
        expect(historyUrls().some((url) => url.includes('window=month'))).toBe(true);
        expect(historyUrls().some((url) => url.includes('window=seventyfive'))).toBe(false);

        rerender(
            <RandomsSVG compact playerId={304} playerName="TesterCompactD" windowName="seventyfive" />,
        );

        await waitFor(() => {
            expect(historyUrls().some((url) => url.includes('window=seventyfive'))).toBe(true);
        });
    });
});

describe('RandomsSVG hover readout — window record vs lifetime wins', () => {
    const RANDOMS_ROWS = [
        { ship_id: 1, ship_name: 'Window Eight', ship_chart_name: 'Window Eight', ship_tier: 8, ship_type: 'Cruiser', pvp_battles: 120, wins: 66, win_ratio: 0.55 },
    ];
    const WINDOW_BY_SHIP = [
        { ship_id: 1, ship_name: 'Window Eight', battles: 9, wins: 6, losses: 3, delta_win_rate: 0.4 },
    ];

    const hoverFirstRow = () => {
        const chain = (d3.select as unknown as jest.Mock)(null) as unknown as {
            on: jest.Mock; data: jest.Mock;
        };
        const handler = chain.on.mock.calls
            .filter(([event]) => event === 'mouseover')
            .map(([, fn]) => fn)[0];
        const datum = chain.data.mock.calls
            .map(([bound]) => bound)
            .filter((bound: unknown): bound is Array<{ ship_name?: string }> => Array.isArray(bound))
            .flat()
            .find((row) => row && row.ship_name === 'Window Eight');
        act(() => { handler.call({}, new MouseEvent('mouseover'), datum); });
    };

    beforeEach(() => {
        mockFetch.mockReset();
        global.fetch = mockFetch as unknown as typeof fetch;
        const chain = (d3.select as unknown as jest.Mock)(null) as unknown as {
            data: jest.Mock; on: jest.Mock;
        };
        chain.data.mockClear();
        chain.on.mockClear();
        mockFetch.mockImplementation(buildUrlRoutedFetch(RANDOMS_ROWS, WINDOW_BY_SHIP));
    });

    it('quotes the WINDOW record, not the lifetime win total, on the compact chart', async () => {
        render(<RandomsSVG compact playerId={401} playerName="TesterHover" />);
        await waitFor(() => {
            expect(document.querySelectorAll('svg').length >= 0).toBe(true);
        });
        await waitFor(() => {
            const chain = (d3.select as unknown as jest.Mock)(null) as unknown as { on: jest.Mock };
            expect(chain.on.mock.calls.some(([event]) => event === 'mouseover')).toBe(true);
        });

        hoverFirstRow();

        // The readout's text is split across spans (the W/L letters ride at
        // 0.75em), so read the whole line rather than matching a fragment.
        const line = () => (document.querySelector('.min-h-\\[1\\.5rem\\]')?.textContent ?? '')
            .replace(/\s+/g, ' ')
            .trim();

        // 6W 3L from the window, not the 66 lifetime wins.
        expect(line()).toContain('6W 3L this window');
        expect(line()).not.toContain('66 wins');
        // The lifetime battle count beside it is untouched.
        expect(line()).toContain('120 battles');
        // ...and the line stops there: the win-rate tail is the Ships tab's.
        expect(line()).not.toContain('win rate');
    });

    it('leaves the full variant\'s hover line on lifetime wins', async () => {
        render(<RandomsSVG playerId={402} playerName="TesterHoverFull" />);
        await waitFor(() => {
            const chain = (d3.select as unknown as jest.Mock)(null) as unknown as { on: jest.Mock };
            expect(chain.on.mock.calls.some(([event]) => event === 'mouseover')).toBe(true);
        });

        hoverFirstRow();

        const line = (document.querySelector('.min-h-\\[1\\.5rem\\]')?.textContent ?? '')
            .replace(/\s+/g, ' ')
            .trim();
        expect(line).toContain('66 wins');
        expect(line).not.toContain('this window');
        // The full variant is filterable by win rate, so its hover keeps the
        // figure the reader is steering by.
        expect(line).toContain('55.0% win rate');
    });
});
