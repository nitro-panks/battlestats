import { renderHook, act } from '@testing-library/react';
import { useClanMembers } from '../useClanMembers';
import { decrementChartFetches, getChartFetchesInFlight } from '../../lib/sharedJsonFetch';

jest.mock('../../context/RealmContext', () => ({ useRealm: () => ({ realm: 'na' }) }));
jest.mock('../../lib/featureFlags', () => ({ isPlayerDewaterfallEnabled: jest.fn(() => true) }));

// Response stub with a controllable X-Clan-Idle-Pending header. Runs the REAL
// fetchSharedJson underneath, so this exercises the hook's poll loop against
// the actual request layer.
const rosterResponse = (idlePending: boolean) => ({
    ok: true,
    status: 200,
    headers: {
        get: (name: string) => {
            const lower = name.toLowerCase();
            if (lower === 'content-type') return 'application/json';
            if (lower === 'x-clan-idle-pending') return idlePending ? 'true' : null;
            return null;
        },
    },
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
        await Promise.resolve();
    });
};

describe('useClanMembers idle-pending poll and error paths', () => {
    let fetchMock: jest.Mock;
    let consoleErrorSpy: jest.SpyInstance;

    beforeEach(() => {
        drainCharts();
        jest.useFakeTimers();
        fetchMock = jest.fn();
        global.fetch = fetchMock as never;
        consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    });

    afterEach(() => {
        drainCharts();
        jest.useRealTimers();
        consoleErrorSpy.mockRestore();
    });

    it('re-polls while X-Clan-Idle-Pending is set and stops once it clears', async () => {
        fetchMock
            .mockResolvedValueOnce(rosterResponse(true))
            .mockResolvedValue(rosterResponse(false));

        renderHook(() => useClanMembers(321, true));
        await flushMicrotasks();
        expect(fetchMock).toHaveBeenCalledTimes(1);

        // The pending header schedules exactly one follow-up poll.
        await act(async () => { jest.advanceTimersByTime(3_500); });
        await flushMicrotasks();
        expect(fetchMock).toHaveBeenCalledTimes(2);

        // The second response was not pending — no further polls fire.
        await act(async () => { jest.advanceTimersByTime(30_000); });
        await flushMicrotasks();
        expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it('surfaces an error and stops loading when the roster fetch fails', async () => {
        fetchMock.mockRejectedValue(new Error('network down'));

        const { result } = renderHook(() => useClanMembers(654, true));
        await flushMicrotasks();

        expect(result.current.error).toBe('Unable to load clan members right now.');
        expect(result.current.loading).toBe(false);
        expect(result.current.members).toEqual([]);
    });
});
