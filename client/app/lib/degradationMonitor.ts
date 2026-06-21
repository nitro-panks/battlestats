import { setRequestConcurrency, DEFAULT_REQUEST_CONCURRENCY } from './requestQueue';
import { setFetchTelemetrySink, type FetchTelemetryEvent, type FetchTelemetryKind } from './fetchTelemetry';

// Watches fetch telemetry and decides whether the network is healthy. When it
// looks degraded it (a) lowers the request-queue concurrency cap so we stop
// hammering a struggling link, and (b) flips a mode the UI surfaces as a subtle
// "connection is slow" hint. Hysteresis (a recovery quiet-period) keeps the mode
// from flapping on a single slow request.

export type DegradationMode = 'normal' | 'degraded';

// Rolling window over which failures are counted.
const WINDOW_MS = 20_000;
// Once degraded, require this long with NO bad signal before recovering.
const RECOVERY_MS = 12_000;
// How often (while degraded) to re-check whether we can recover.
const RECOVERY_CHECK_MS = 3_000;
// Concurrency cap while degraded (vs DEFAULT_REQUEST_CONCURRENCY when healthy).
const DEGRADED_CONCURRENCY = 2;

// Triggers (any one, within the window, flips to degraded):
const TIMEOUT_TRIGGER = 2; // ≥2 timeouts
const THROTTLE_TRIGGER = 1; // any 429
const MIN_SAMPLES_FOR_RATE = 5;
const ERROR_RATE_TRIGGER = 0.5; // ≥50% failures across ≥5 samples

interface WindowEvent {
    t: number;
    kind: FetchTelemetryKind;
}

type Listener = (mode: DegradationMode) => void;

class DegradationMonitor {
    private events: WindowEvent[] = [];
    private mode: DegradationMode = 'normal';
    private lastBadAt = 0;
    private recoveryTimer: ReturnType<typeof setInterval> | null = null;
    private listeners = new Set<Listener>();
    private started = false;

    // Register as the fetch telemetry sink. Idempotent — safe to call from every
    // provider mount.
    start(): void {
        if (this.started) {
            return;
        }
        this.started = true;
        setFetchTelemetrySink((event) => this.record(event));
    }

    getMode(): DegradationMode {
        return this.mode;
    }

    // Multiplier the pollers apply to their interval while degraded (slower
    // polling eases load on a struggling network).
    getPollIntervalMultiplier(): number {
        return this.mode === 'degraded' ? 2 : 1;
    }

    subscribe(listener: Listener): () => void {
        this.listeners.add(listener);
        return () => {
            this.listeners.delete(listener);
        };
    }

    record(event: FetchTelemetryEvent): void {
        const now = Date.now();
        this.events.push({ t: now, kind: event.kind });
        if (this.isBad(now)) {
            this.lastBadAt = now;
            if (this.mode === 'normal') {
                this.enterDegraded();
            }
        }
    }

    private prune(now: number): void {
        const cutoff = now - WINDOW_MS;
        while (this.events.length > 0 && this.events[0].t < cutoff) {
            this.events.shift();
        }
    }

    private connectionIsSlow(): boolean {
        if (typeof navigator === 'undefined') {
            return false;
        }
        const effectiveType = (navigator as unknown as { connection?: { effectiveType?: string } }).connection?.effectiveType;
        return effectiveType === 'slow-2g' || effectiveType === '2g';
    }

    private isBad(now: number): boolean {
        this.prune(now);
        if (this.connectionIsSlow()) {
            return true;
        }
        let timeouts = 0;
        let throttled = 0;
        let errors = 0;
        for (const event of this.events) {
            if (event.kind === 'timeout') timeouts += 1;
            else if (event.kind === 'throttled') throttled += 1;
            else if (event.kind === 'error') errors += 1;
        }
        if (throttled >= THROTTLE_TRIGGER) return true;
        if (timeouts >= TIMEOUT_TRIGGER) return true;
        const samples = this.events.length;
        if (samples >= MIN_SAMPLES_FOR_RATE && (errors + timeouts + throttled) / samples >= ERROR_RATE_TRIGGER) {
            return true;
        }
        return false;
    }

    private enterDegraded(): void {
        this.mode = 'degraded';
        setRequestConcurrency(DEGRADED_CONCURRENCY);
        this.notify();
        if (!this.recoveryTimer && typeof setInterval !== 'undefined') {
            this.recoveryTimer = setInterval(() => this.checkRecovery(), RECOVERY_CHECK_MS);
        }
    }

    private checkRecovery(): void {
        const now = Date.now();
        if (this.mode === 'degraded' && !this.isBad(now) && now - this.lastBadAt >= RECOVERY_MS) {
            this.exitDegraded();
        }
    }

    private exitDegraded(): void {
        this.mode = 'normal';
        setRequestConcurrency(DEFAULT_REQUEST_CONCURRENCY);
        this.notify();
        if (this.recoveryTimer) {
            clearInterval(this.recoveryTimer);
            this.recoveryTimer = null;
        }
    }

    private notify(): void {
        for (const listener of this.listeners) {
            listener(this.mode);
        }
    }

    // Test-only: restore a clean baseline.
    reset(): void {
        this.events = [];
        this.mode = 'normal';
        this.lastBadAt = 0;
        if (this.recoveryTimer) {
            clearInterval(this.recoveryTimer);
            this.recoveryTimer = null;
        }
        setRequestConcurrency(DEFAULT_REQUEST_CONCURRENCY);
    }
}

export const degradationMonitor = new DegradationMonitor();
