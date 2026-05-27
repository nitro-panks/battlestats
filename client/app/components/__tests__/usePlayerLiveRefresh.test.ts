import {
    computeSecondsRemaining,
    parseNextRefreshHeader,
    parsePendingHeader,
} from '../usePlayerLiveRefresh';

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
