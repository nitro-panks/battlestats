import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import RandomsSVG from '../RandomsSVG';

jest.mock('d3', () => {
    const chain = {
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
        };
        scale.domain = jest.fn(() => scale);
        scale.range = jest.fn(() => scale);
        scale.padding = jest.fn(() => scale);
        scale.bandwidth = jest.fn(() => 24);
        scale.step = jest.fn(() => 25);
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

        render(<RandomsSVG playerId={102} isLoading />);

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

        render(<RandomsSVG playerId={101} />);

        await waitFor(() => {
            expect(screen.getByRole('button', { name: 'T6' })).toBeInTheDocument();
        });

        expect(screen.getAllByRole('button', { name: 'All' })[1]).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: 'T6' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: 'T5' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.queryByRole('button', { name: 'T4' })).not.toBeInTheDocument();
    });
});