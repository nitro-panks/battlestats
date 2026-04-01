import React from 'react';
import { render, waitFor } from '@testing-library/react';
import TierSVG from '../TierSVG';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
    getChartFetchesInFlight: jest.fn(() => 0),
    incrementChartFetches: jest.fn(),
    decrementChartFetches: jest.fn(),
}));

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
        const scale = ((value: number | string) => (typeof value === 'number' ? value : 24)) as ((value: number | string) => number) & {
            domain: jest.Mock;
            range: jest.Mock;
            padding: jest.Mock;
            bandwidth: jest.Mock;
        };
        scale.domain = jest.fn(() => scale);
        scale.range = jest.fn(() => scale);
        scale.padding = jest.fn(() => scale);
        scale.bandwidth = jest.fn(() => 24);
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

const mockFetchSharedJson = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

describe('TierSVG', () => {
    afterEach(() => {
        jest.clearAllMocks();
    });

    it('requests tier data for the active player', async () => {
        mockFetchSharedJson.mockResolvedValueOnce({
            data: [
                { ship_tier: 10, pvp_battles: 50, wins: 28, win_ratio: 0.56 },
                { ship_tier: 8, pvp_battles: 30, wins: 16, win_ratio: 0.533 },
            ],
            headers: {},
        });

        render(<TierSVG playerId={303} />);

        await waitFor(() => {
            expect(mockFetchSharedJson).toHaveBeenCalledWith('/api/fetch/tier_data/303/?realm=na', {
                label: 'Tier data 303',
                ttlMs: 30000,
            });
        });
    });
});