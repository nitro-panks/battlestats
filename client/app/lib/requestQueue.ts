// Global priority concurrency queue for outbound API requests.
//
// HTTP/2 removed the old 6-connection-per-origin limit, so this is about PACING,
// not connection multiplexing: cap how many requests fire at once and let the
// visible/critical ones jump ahead of background prefetch. This is what protects
// the app from "throttling from rapid requests" — a burst (rapid player
// switching, many charts) drains in priority order instead of all at once.
//
// The degradation monitor lowers the cap when the network looks unhealthy.

export type RequestPriority = 'critical' | 'high' | 'low';

const PRIORITY_RANK: Record<RequestPriority, number> = {
    critical: 0,
    high: 1,
    low: 2,
};

// Default simultaneous in-flight cap. ~6 keeps a player page's eager fetches
// (detail + ~4 charts + clan rail) moving without flooding a slow link.
export const DEFAULT_REQUEST_CONCURRENCY = 6;

interface Waiter {
    priority: RequestPriority;
    seq: number;
    resolve: (release: () => void) => void;
    reject: (reason: unknown) => void;
    signal?: AbortSignal;
    onAbort?: () => void;
}

const toAbortError = (reason: unknown): unknown =>
    reason instanceof Error ? reason : new DOMException('Aborted', 'AbortError');

export class RequestQueue {
    private active = 0;
    private cap: number;
    private waiters: Waiter[] = [];
    private seqCounter = 0;

    constructor(cap: number) {
        this.cap = Math.max(1, cap);
    }

    getCap(): number {
        return this.cap;
    }

    getActive(): number {
        return this.active;
    }

    getQueued(): number {
        return this.waiters.length;
    }

    // Lower/raise the concurrency cap at runtime (the degradation monitor uses
    // this). Raising it immediately drains any waiters that now fit.
    setCap(cap: number): void {
        this.cap = Math.max(1, cap);
        this.drain();
    }

    // Acquire a slot. Resolves with a release() fn once a slot is free (subject to
    // priority). If `signal` aborts while still queued, the waiter is removed and
    // the promise rejects with an AbortError — so a cancelled request that never
    // started simply never fires.
    acquire(priority: RequestPriority, signal?: AbortSignal): Promise<() => void> {
        if (signal?.aborted) {
            return Promise.reject(toAbortError(signal.reason));
        }

        if (this.active < this.cap) {
            this.active += 1;
            return Promise.resolve(this.makeRelease());
        }

        return new Promise<() => void>((resolve, reject) => {
            const waiter: Waiter = { priority, seq: this.seqCounter++, resolve, reject, signal };
            if (signal) {
                const onAbort = () => {
                    const index = this.waiters.indexOf(waiter);
                    if (index >= 0) {
                        this.waiters.splice(index, 1);
                    }
                    reject(toAbortError(signal.reason));
                };
                waiter.onAbort = onAbort;
                signal.addEventListener('abort', onAbort, { once: true });
            }
            this.waiters.push(waiter);
        });
    }

    private makeRelease(): () => void {
        let released = false;
        return () => {
            if (released) {
                return;
            }
            released = true;
            this.active -= 1;
            this.drain();
        };
    }

    private drain(): void {
        while (this.active < this.cap && this.waiters.length > 0) {
            // Highest priority first; FIFO within a priority (stable by seq).
            this.waiters.sort((a, b) => PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority] || a.seq - b.seq);
            const waiter = this.waiters.shift()!;
            if (waiter.signal && waiter.onAbort) {
                waiter.signal.removeEventListener('abort', waiter.onAbort);
            }
            this.active += 1;
            waiter.resolve(this.makeRelease());
        }
    }
}

// Process-wide singleton used by fetchSharedJson.
export const requestQueue = new RequestQueue(DEFAULT_REQUEST_CONCURRENCY);

export const setRequestConcurrency = (cap: number): void => requestQueue.setCap(cap);
