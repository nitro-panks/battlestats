import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
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

    // Two eligible ships (both >= the default cutoff of 25) so the chart is
    // non-empty by default; the tier/type filter buttons are derived from the
    // full ship set, so the observable proof of the new filters is the
    // empty-state text (chartData → 0) and the slider's clamped value label.
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
        // Default cutoff (no-window-activity default).
        expect(screen.getByText(/≥\s*25/)).toBeInTheDocument();

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

    it('empties the chart when window-only is on and no ships were played in the window', async () => {
        // Empty 30d window payload: neither ship was played recently.
        mockFetch.mockImplementation(buildUrlRoutedFetch(RANDOMS_ROWS, []));

        render(<RandomsSVG playerId={202} playerName="TesterB" />);

        // Default (checkbox off): both ships eligible → chart shown.
        await screen.findByLabelText('Minimum lifetime random battles to show a ship');
        expect(screen.queryByText('No ships match the selected filters.')).not.toBeInTheDocument();

        // Window-only on → nothing in the window → chart empties.
        const checkbox = screen.getByRole('checkbox', { name: /played in the last 30 days/i });
        fireEvent.click(checkbox);

        await waitFor(() => {
            expect(screen.getByText('No ships match the selected filters.')).toBeInTheDocument();
        });
    });

    it('defaults to window-only with no min-battles floor when the window has battles', async () => {
        // Grind Ship is in the 30d window: the on-load default should flip the
        // "played this window" checkbox on and drop the min-battles cutoff to 0.
        mockFetch.mockImplementation(buildUrlRoutedFetch(
            RANDOMS_ROWS,
            [{ ship_id: 1, ship_name: 'Grind Ship', ship_tier: 8, ship_type: 'Cruiser', battles: 12, wins: 7, delta_win_rate: 2.1 }],
        ));

        render(<RandomsSVG playerId={203} playerName="TesterC" />);

        const checkbox = await screen.findByRole('checkbox', { name: /played in the last 30 days/i });
        // Window join lands → default applied: checkbox on, cutoff 0.
        await waitFor(() => expect(checkbox).toBeChecked());
        expect(screen.getByText(/≥\s*0/)).toBeInTheDocument();
        // The in-window ship keeps the chart populated.
        expect(screen.queryByText('No ships match the selected filters.')).not.toBeInTheDocument();
    });

    it('keeps the all-ships / min-25 default when the window has no battles', async () => {
        // Empty window payload: the default filters stay put (checkbox off,
        // cutoff 25) so a player with no recent activity still sees their ships.
        mockFetch.mockImplementation(buildUrlRoutedFetch(RANDOMS_ROWS, []));

        render(<RandomsSVG playerId={204} playerName="TesterD" />);

        const checkbox = await screen.findByRole('checkbox', { name: /played in the last 30 days/i });
        await screen.findByRole('button', { name: 'T8' });
        expect(checkbox).not.toBeChecked();
        expect(screen.getByText(/≥\s*25/)).toBeInTheDocument();
        expect(screen.queryByText('No ships match the selected filters.')).not.toBeInTheDocument();
    });
});