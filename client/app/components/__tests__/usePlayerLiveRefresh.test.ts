import { renderHook, act } from '@testing-library/react';
import {
    computeSecondsRemaining,
    parseNextRefreshHeader,
    parsePendingHeader,
    usePlayerLiveRefresh,
} from '../usePlayerLiveRefresh';
import { fetchSharedJson, invalidateSharedJsonByPrefix } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
    invalidateSharedJsonByPrefix: jest.fn(),
}));
const mockFetch = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;
const mockInvalidate = invalidateSharedJsonByPrefix as jest.MockedFunction<typeof invalidateSharedJsonByPrefix>;

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
        mockInvalidate.mockReset();
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

    it('polls the FAST window (6×2s) then settles to the 3s slow cadence', async () => {
        // Always still-pending: drives the poll loop indefinitely so we can assert
        // the spacing of successive polls. The header stays `true`, so the hook
        // keeps scheduling the next poll at pollDelayMs(attempt).
        mockFetch.mockResolvedValue({
            data: { player_id: 1 } as never,
            headers: { 'X-Player-Refresh-Pending': 'true' },
        });

        renderHook(() => usePlayerLiveRefresh({
            playerName: 'lil_boots',
            realm: 'na',
            initialPending: true,
            initialNextRefresh: null,
            onRehydrate: jest.fn(),
        }));

        // First poll fires at pollDelayMs(0) = 2s. Each `act` flushes the pending
        // fetch promise so the next setTimeout is scheduled before we advance.
        const flush = async () => { await act(async () => { await Promise.resolve(); }); };

        // Attempts 1–6 are the fast window: each scheduled 2s after the prior.
        for (let i = 0; i < 6; i += 1) {
            await act(async () => { jest.advanceTimersByTime(2_000); });
            await flush();
        }
        expect(mockFetch).toHaveBeenCalledTimes(6);

        // The 7th poll is the first SLOW one: 2s is NOT enough to fire it, 3s is.
        await act(async () => { jest.advanceTimersByTime(2_000); });
        await flush();
        expect(mockFetch).toHaveBeenCalledTimes(6);
        await act(async () => { jest.advanceTimersByTime(1_000); });
        await flush();
        expect(mockFetch).toHaveBeenCalledTimes(7);
    });

    it('purges the player battle-history cache when a refresh lands (so a remount re-fetches)', async () => {
        // First poll returns pending=false → the refresh has landed. The hook
        // must evict this player's battle-history entries (keyed on the canonical
        // payload `name`, not the URL slug) so navigating away and back re-fetches
        // the fresh chart instead of the stale nonce=0 cache entry.
        mockFetch.mockResolvedValue({
            data: { player_id: 1, name: 'Lil_Boots' } as never,
            headers: { 'X-Player-Refresh-Pending': 'false' },
        });

        renderHook(() => usePlayerLiveRefresh({
            playerName: 'lil_boots',
            realm: 'na',
            initialPending: true,
            initialNextRefresh: null,
            onRehydrate: jest.fn(),
        }));

        // Fire the first poll (pollDelayMs(0) = 2s) and flush its promise.
        await act(async () => { jest.advanceTimersByTime(2_000); });
        await act(async () => { await Promise.resolve(); });

        expect(mockInvalidate).toHaveBeenCalledWith('battle-history:Lil_Boots:na:');
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
