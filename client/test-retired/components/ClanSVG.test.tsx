import React from 'react';
import { act, render, waitFor } from '@testing-library/react';
import ClanSVG from '../ClanSVG';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
    getChartFetchesInFlight: jest.fn(() => 0),
    incrementChartFetches: jest.fn(),
    decrementChartFetches: jest.fn(),
}));

const mockText = jest.fn();

jest.mock('d3', () => {
    const chain: any = {
        append: jest.fn(() => chain),
        attr: jest.fn(() => chain),
        style: jest.fn(() => chain),
        text: jest.fn((value?: string) => {
            mockText(value);
            return chain;
        }),
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
        filter: jest.fn(() => chain),
        empty: jest.fn(() => true),
        raise: jest.fn(() => chain),
        insert: jest.fn(() => chain),
        node: jest.fn(() => ({
            getBBox: () => ({ x: 0, y: 0, width: 24, height: 12 }),
        })),
    };

    const createScale = () => {
        const scale = ((value: number | string) => (typeof value === 'number' ? value : 24)) as ((value: number | string) => number) & {
            domain: jest.Mock;
            range: jest.Mock;
            ticks: jest.Mock;
        };
        scale.domain = jest.fn(() => scale);
        scale.range = jest.fn(() => scale);
        scale.ticks = jest.fn(() => [0, 1, 2, 3, 4]);
        return scale;
    };

    const createAxis = () => {
        const axis = jest.fn(() => chain) as jest.Mock & {
            ticks: jest.Mock;
            tickSizeOuter: jest.Mock;
        };
        axis.ticks = jest.fn(() => axis);
        axis.tickSizeOuter = jest.fn(() => axis);
        return axis;
    };

    return {
        select: jest.fn(() => chain),
        max: jest.fn((values: number[]) => (values.length > 0 ? Math.max(...values) : undefined)),
        min: jest.fn((values: number[]) => (values.length > 0 ? Math.min(...values) : undefined)),
        scaleLinear: jest.fn(() => createScale()),
        axisBottom: jest.fn(() => createAxis()),
        axisLeft: jest.fn(() => createAxis()),
    };
});

const mockFetchSharedJson = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

describe('ClanSVG', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        mockText.mockReset();
        mockFetchSharedJson.mockReset();
    });

    afterEach(() => {
        jest.runOnlyPendingTimers();
        jest.useRealTimers();
    });

    it('retries once before settling the clan chart request', async () => {
        mockFetchSharedJson
            .mockRejectedValueOnce(new Error('temporary clan plot miss'))
            .mockResolvedValueOnce({
                data: [
                    { player_name: 'DeckBoss', pvp_battles: 120, pvp_ratio: 55.2 },
                ],
                headers: {},
            });

        render(<ClanSVG clanId={5555} membersData={[]} />);

        await waitFor(() => {
            expect(mockFetchSharedJson).toHaveBeenCalledTimes(1);
        });

        await act(async () => {
            jest.advanceTimersByTime(350);
        });

        await waitFor(() => {
            expect(mockFetchSharedJson).toHaveBeenCalledTimes(2);
        });

        expect(mockText).not.toHaveBeenCalledWith('Unable to load clan chart.');
    });

    it('shows the clan chart error only after the retry also fails', async () => {
        mockFetchSharedJson.mockRejectedValue(new Error('clan plot unavailable'));

        render(<ClanSVG clanId={7777} membersData={[]} />);

        await waitFor(() => {
            expect(mockFetchSharedJson).toHaveBeenCalledTimes(1);
        });

        await act(async () => {
            jest.advanceTimersByTime(350);
        });

        await waitFor(() => {
            expect(mockFetchSharedJson).toHaveBeenCalledTimes(2);
        });

        await waitFor(() => {
            expect(mockText).toHaveBeenCalledWith('Unable to load clan chart.');
        });
    });

    it('keeps showing a loading state while the clan plot response is pending', async () => {
        mockFetchSharedJson
            .mockResolvedValueOnce({
                data: [],
                headers: { 'X-Clan-Plot-Pending': 'true' },
            })
            .mockResolvedValueOnce({
                data: [
                    { player_name: 'DeckBoss', pvp_battles: 120, pvp_ratio: 55.2 },
                ],
                headers: {},
            });

        render(<ClanSVG clanId={9999} membersData={[]} />);

        await waitFor(() => {
            expect(mockFetchSharedJson).toHaveBeenCalledTimes(1);
        });

        await waitFor(() => {
            expect(mockText).toHaveBeenCalledWith('Loading clan chart data...');
        });

        expect(mockText).not.toHaveBeenCalledWith('No clan chart data available.');

        await act(async () => {
            jest.advanceTimersByTime(3000);
        });

        await waitFor(() => {
            expect(mockFetchSharedJson).toHaveBeenCalledTimes(2);
        });

        expect(mockText).not.toHaveBeenCalledWith('Unable to load clan chart.');
    });
});