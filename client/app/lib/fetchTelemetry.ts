// Lightweight telemetry the fetch client emits per request attempt. The
// degradation monitor (the only consumer) subscribes to derive a rolling health
// signal. Aborts are intentionally NOT emitted — a cancelled request is not a
// sign of a degraded network.

export type FetchTelemetryKind = 'success' | 'error' | 'timeout' | 'throttled';

export interface FetchTelemetryEvent {
    kind: FetchTelemetryKind;
    // HTTP status when there was a response (absent for a network error / timeout).
    status?: number;
    // Wall-clock time the fetch attempt took (queue wait excluded).
    durationMs: number;
}

type Sink = (event: FetchTelemetryEvent) => void;

let sink: Sink | null = null;

// The degradation monitor registers itself here. A single sink is enough — there
// is exactly one monitor.
export const setFetchTelemetrySink = (next: Sink | null): void => {
    sink = next;
};

export const emitFetchTelemetry = (event: FetchTelemetryEvent | null): void => {
    if (event && sink) {
        sink(event);
    }
};
