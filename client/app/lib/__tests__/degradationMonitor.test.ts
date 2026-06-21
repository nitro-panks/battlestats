import { degradationMonitor } from '../degradationMonitor';
import { requestQueue, DEFAULT_REQUEST_CONCURRENCY } from '../requestQueue';
import { emitFetchTelemetry } from '../fetchTelemetry';

describe('degradationMonitor', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        degradationMonitor.reset();
    });
    afterEach(() => {
        degradationMonitor.reset();
        jest.useRealTimers();
    });

    it('stays normal for a single isolated error', () => {
        degradationMonitor.record({ kind: 'error', durationMs: 10 });
        expect(degradationMonitor.getMode()).toBe('normal');
        expect(requestQueue.getCap()).toBe(DEFAULT_REQUEST_CONCURRENCY);
    });

    it('degrades on a 429 throttle and lowers the concurrency cap', () => {
        degradationMonitor.record({ kind: 'throttled', status: 429, durationMs: 5 });
        expect(degradationMonitor.getMode()).toBe('degraded');
        expect(requestQueue.getCap()).toBe(2);
    });

    it('degrades after repeated timeouts', () => {
        degradationMonitor.record({ kind: 'timeout', durationMs: 15000 });
        expect(degradationMonitor.getMode()).toBe('normal'); // one is not enough
        degradationMonitor.record({ kind: 'timeout', durationMs: 15000 });
        expect(degradationMonitor.getMode()).toBe('degraded');
    });

    it('recovers to normal (and restores the cap) after a quiet period', () => {
        degradationMonitor.record({ kind: 'throttled', status: 429, durationMs: 5 });
        expect(degradationMonitor.getMode()).toBe('degraded');

        // Advance past the rolling window + recovery quiet-period; the recovery
        // timer re-checks and flips back once no bad signal remains.
        jest.advanceTimersByTime(30_000);

        expect(degradationMonitor.getMode()).toBe('normal');
        expect(requestQueue.getCap()).toBe(DEFAULT_REQUEST_CONCURRENCY);
    });

    it('wires itself as the telemetry sink via start()', () => {
        degradationMonitor.start();
        emitFetchTelemetry({ kind: 'throttled', status: 429, durationMs: 5 });
        expect(degradationMonitor.getMode()).toBe('degraded');
    });

    it('reports a slower poll multiplier while degraded', () => {
        expect(degradationMonitor.getPollIntervalMultiplier()).toBe(1);
        degradationMonitor.record({ kind: 'throttled', status: 429, durationMs: 5 });
        expect(degradationMonitor.getPollIntervalMultiplier()).toBe(2);
    });
});
