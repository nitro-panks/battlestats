import { renderHook, act } from '@testing-library/react';
import { usePlayerLiveRefresh } from '../usePlayerLiveRefresh';
import { fetchSharedJson, invalidateSharedJsonByPrefix } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
    invalidateSharedJsonByPrefix: jest.fn(),
}));

const mockFetch = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;
const mockInvalidate = invalidateSharedJsonByPrefix as jest.MockedFunction<typeof invalidateSharedJsonByPrefix>;

let visibilityState: DocumentVisibilityState = 'visible';
const setVisibility = (state: DocumentVisibilityState) => {
    visibilityState = state;
    act(() => {
        document.dispatchEvent(new Event('visibilitychange'));
    });
};

beforeAll(() => {
    Object.defineProperty(document, 'visibilityState', {
        configurable: true,
        get: () => visibilityState,
    });
});

describe('usePlayerLiveRefresh visibility pause', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        mockFetch.mockReset();
        mockInvalidate.mockReset();
        visibilityState = 'visible';
        // A poll that reports the refresh has landed (clears pending).
        mockFetch.mockResolvedValue({
            data: { player_id: 1, name: 'p' } as never,
            headers: { 'X-Player-Refresh-Pending': 'false', 'X-Player-Next-Refresh': null },
        });
    });
    afterEach(() => {
        jest.useRealTimers();
    });

    it('does NOT poll while the tab is hidden, then polls on focus', () => {
        visibilityState = 'hidden';
        const { unmount } = renderHook(() => usePlayerLiveRefresh({
            playerName: 'lil_boots',
            realm: 'na',
            initialPending: true,
            initialNextRefresh: null,
            onRehydrate: () => {},
        }));

        // Past the first poll delay while hidden → no network.
        act(() => { jest.advanceTimersByTime(5_000); });
        expect(mockFetch).not.toHaveBeenCalled();

        // Focus → the poll effect re-runs and fires after the fast interval.
        setVisibility('visible');
        act(() => { jest.advanceTimersByTime(2_100); });
        expect(mockFetch).toHaveBeenCalledTimes(1);

        unmount();
    });

    it('polls normally when visible from the start', () => {
        const { unmount } = renderHook(() => usePlayerLiveRefresh({
            playerName: 'lil_boots',
            realm: 'na',
            initialPending: true,
            initialNextRefresh: null,
            onRehydrate: () => {},
        }));

        act(() => { jest.advanceTimersByTime(2_100); });
        expect(mockFetch).toHaveBeenCalledTimes(1);

        unmount();
    });
});
