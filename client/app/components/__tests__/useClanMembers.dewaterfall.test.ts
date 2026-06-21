import { renderHook, act } from '@testing-library/react';
import { useClanMembers } from '../useClanMembers';
import {
    incrementChartFetches,
    decrementChartFetches,
    getChartFetchesInFlight,
} from '../../lib/sharedJsonFetch';
import { isPlayerDewaterfallEnabled } from '../../lib/featureFlags';

jest.mock('../../context/RealmContext', () => ({ useRealm: () => ({ realm: 'na' }) }));
jest.mock('../../lib/featureFlags', () => ({ isPlayerDewaterfallEnabled: jest.fn() }));

const mockFlag = isPlayerDewaterfallEnabled as jest.MockedFunction<typeof isPlayerDewaterfallEnabled>;

const okResponse = () => ({
    ok: true,
    status: 200,
    headers: { get: (name: string) => (name.toLowerCase() === 'content-type' ? 'application/json' : null) },
    json: async () => [],
    text: async () => '',
});

const drainCharts = () => {
    while (getChartFetchesInFlight() > 0) {
        decrementChartFetches();
    }
};

const flushMicrotasks = async () => {
    await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
    });
};

describe('useClanMembers de-waterfall gate', () => {
    let fetchMock: jest.Mock;

    beforeEach(() => {
        drainCharts();
        fetchMock = jest.fn().mockResolvedValue(okResponse());
        global.fetch = fetchMock as never;
    });

    afterEach(() => {
        drainCharts();
        jest.useRealTimers();
    });

    it('fetches the roster immediately when de-waterfalled, even while charts are in flight', async () => {
        mockFlag.mockReturnValue(true);
        incrementChartFetches(); // pretend the chart warmup is mid-flight

        renderHook(() => useClanMembers(123, true));
        await flushMicrotasks();

        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it('gates the roster behind chart fetches when de-waterfall is off (legacy)', async () => {
        jest.useFakeTimers();
        mockFlag.mockReturnValue(false);
        incrementChartFetches();

        renderHook(() => useClanMembers(456, true));
        await act(async () => { await Promise.resolve(); });

        // Still gated: charts are in flight, so no fetch yet.
        expect(fetchMock).not.toHaveBeenCalled();

        // Charts drain → the 500ms gate interval releases the fetch.
        decrementChartFetches();
        await act(async () => {
            jest.advanceTimersByTime(600);
            await Promise.resolve();
        });

        expect(fetchMock).toHaveBeenCalledTimes(1);
    });
});
