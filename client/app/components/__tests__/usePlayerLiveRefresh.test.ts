import { renderHook, act } from '@testing-library/react';
import {
    computeSecondsRemaining,
    parseNextRefreshHeader,
    parsePendingHeader,
    usePlayerLiveRefresh,
} from '../usePlayerLiveRefresh';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({ fetchSharedJson: jest.fn() }));
const mockFetch = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

describe('usePlayerLiveRefresh helpers', () => {
    it('parses the refresh-pending header', () => {
        expect(parsePendingHeader('true')).toBe(true);
        expect(parsePendingHeader('false')).toBe(false);
        expect(parsePendingHeader(null)).toBe(false);
        expect(parsePendingHeader(undefined)).toBe(false);
    });

    it('parses the next-refresh epoch header', () => {
        expect(parseNextRefreshHeader('1716800000')).toBe(1716800000);
        expect(parseNextRefreshHeader(null)).toBeNull();
        expect(parseNextRefreshHeader('')).toBeNull();
        expect(parseNextRefreshHeader('not-a-number')).toBeNull();
    });

    it('computes seconds remaining, clamped at zero', () => {
        const nowEpoch = Math.floor(Date.now() / 1000);
        const remaining = computeSecondsRemaining(nowEpoch + 600);
        expect(remaining).toBeGreaterThan(595);
        expect(remaining).toBeLessThanOrEqual(600);

        // Past target / missing anchor → no negative countdown.
        expect(computeSecondsRemaining(nowEpoch - 100)).toBe(0);
        expect(computeSecondsRemaining(null)).toBe(0);
    });
});

describe('usePlayerLiveRefresh auto-refresh on cooldown expiry', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        mockFetch.mockReset();
        // Poll target for the auto-fired refresh (never reached in the
        // anchor-less test, harmless there).
        mockFetch.mockResolvedValue({ data: { player_id: 1 } as never, headers: {} });
    });
    afterEach(() => {
        jest.useRealTimers();
    });

    it('auto-triggers an in-place refresh (phase → loading) when the cooldown reaches zero', () => {
        const nowEpoch = Math.floor(Date.now() / 1000);
        const { result, unmount } = renderHook(() => usePlayerLiveRefresh({
            playerName: 'lil_boots',
            realm: 'na',
            initialPending: false,
            initialNextRefresh: nowEpoch + 2, // cooldown expires in ~2s
            onRehydrate: jest.fn(),
        }));

        expect(result.current.phase).toBe('cooldown');
        expect(result.current.secondsRemaining).toBeGreaterThan(0);

        // Cross the cooldown boundary → the hook re-enters the loading poll
        // in place (no reload); phase flips to loading.
        act(() => { jest.advanceTimersByTime(3000); });
        expect(result.current.phase).toBe('loading');

        unmount();
    });

    it('does NOT auto-fire when there is no cooldown anchor (avoids spurious refresh)', () => {
        const { result, unmount } = renderHook(() => usePlayerLiveRefresh({
            playerName: 'lil_boots',
            realm: 'na',
            initialPending: false,
            initialNextRefresh: null, // no anchor → secondsRemaining is 0 from the start
            onRehydrate: jest.fn(),
        }));

        expect(result.current.phase).toBe('cooldown');
        act(() => { jest.advanceTimersByTime(5000); });
        // Stays in cooldown — never auto-kicks a refresh without a real target.
        expect(result.current.phase).toBe('cooldown');

        unmount();
    });
});
