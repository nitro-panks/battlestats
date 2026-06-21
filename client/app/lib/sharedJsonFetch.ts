type SharedJsonFetchHeaders = Record<string, string | null>;

export interface SharedJsonFetchResult<T> {
    data: T;
    headers: SharedJsonFetchHeaders;
}

interface SharedJsonRetryOptions {
    // Number of EXTRA attempts after the first (so attempts:2 → up to 3 fetches).
    attempts: number;
    // Fixed delay between attempts, in ms (simple short backoff).
    backoffMs: number;
}

interface SharedJsonFetchOptions {
    init?: RequestInit;
    label: string;
    cacheKey?: string;
    responseHeaders?: string[];
    ttlMs?: number;
    // Opt-in retry on transient failures ONLY — a network error (fetch threw) or
    // a 5xx response. NEVER retried: 4xx (e.g. a real 404), or a non-JSON 2xx.
    // Omitted → no retry (default), so every existing caller is unaffected.
    retry?: SharedJsonRetryOptions;
    // Per-caller cancellation. When this signal aborts, THIS caller's promise
    // rejects with the signal's reason (an AbortError). The shared underlying
    // fetch is only aborted once EVERY caller subscribed to the same in-flight
    // request has abandoned it (ref-counted) — so a page navigating away cancels
    // its own work without poisoning an unrelated component that deduped onto the
    // same key.
    signal?: AbortSignal;
    // Hard per-request timeout in ms (applied per attempt, so retries each get a
    // fresh budget). A timeout is a retriable transport error. Default
    // DEFAULT_TIMEOUT_MS; pass 0 to disable (e.g. a deliberately long stream).
    timeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 15_000;

// Error thrown by readJsonOrThrow carrying the HTTP status (when there was a
// response) so callers can branch terminal 4xx (e.g. 404 "not found") from a
// transient 5xx that is worth retrying / surfacing as "temporarily unavailable".
// `status` is undefined for a non-HTTP failure (e.g. a malformed-JSON 2xx body).
export class SharedJsonFetchError extends Error {
    readonly status?: number;

    constructor(message: string, status?: number) {
        super(message);
        this.name = 'SharedJsonFetchError';
        this.status = status;
    }

    // A 5xx response — the only HTTP class worth retrying.
    get isServerError(): boolean {
        return this.status !== undefined && this.status >= 500;
    }
}

interface SettledCacheEntry {
    expiresAt: number;
    value: SharedJsonFetchResult<unknown>;
}

// One shared underlying request, with the controller that aborts its fetch and a
// live count of callers still awaiting it. The underlying fetch is aborted only
// when `subscribers` falls to 0 (every caller abandoned it), so deduped callers
// don't cancel each other's work.
interface InFlightEntry {
    promise: Promise<SharedJsonFetchResult<unknown>>;
    controller: AbortController;
    subscribers: number;
    // Flipped synchronously the moment the underlying request settles, so a
    // subscriber releasing during the resolution microtask never aborts an
    // already-finished fetch.
    settled: boolean;
}

let chartFetchesInFlight = 0;

export const getChartFetchesInFlight = (): number => chartFetchesInFlight;
export const incrementChartFetches = (): void => { chartFetchesInFlight += 1; };
export const decrementChartFetches = (): void => { chartFetchesInFlight = Math.max(0, chartFetchesInFlight - 1); };

const inFlightRequests = new Map<string, InFlightEntry>();
const settledRequests = new Map<string, SettledCacheEntry>();
const resolvedCacheEnabled = process.env.NODE_ENV !== 'test';

// Combine an abort controller's signal with a fresh per-attempt timeout into a
// single signal handed to fetch(). Prefers the native AbortSignal.any when
// present, with a small manual fallback for older runtimes.
const buildAttemptSignal = (controllerSignal: AbortSignal, timeoutMs: number): AbortSignal => {
    const signals: AbortSignal[] = [controllerSignal];
    if (timeoutMs > 0 && typeof AbortSignal.timeout === 'function') {
        signals.push(AbortSignal.timeout(timeoutMs));
    }
    if (signals.length === 1) {
        return controllerSignal;
    }
    const anyFn = (AbortSignal as unknown as { any?: (s: AbortSignal[]) => AbortSignal }).any;
    if (typeof anyFn === 'function') {
        return anyFn(signals);
    }
    const combined = new AbortController();
    for (const signal of signals) {
        if (signal.aborted) {
            combined.abort(signal.reason);
            break;
        }
        signal.addEventListener('abort', () => combined.abort(signal.reason), { once: true });
    }
    return combined.signal;
};

// A caller-initiated cancellation (navigation, visibility-pause, realm switch).
// Call sites MUST treat this as benign: swallow it, change no state, show no
// error. A realm switch on the same player does NOT remount the page, so the
// usual isMounted/cancelled guards won't catch it — branch on this instead.
export const isAbortError = (error: unknown): boolean =>
    error instanceof DOMException && error.name === 'AbortError';

// A per-request timeout — a REAL transient failure, distinct from an abort.
// Treat like any network error (retry next cycle / keep stale data), NOT as "we
// navigated away".
export const isTimeoutError = (error: unknown): boolean =>
    error instanceof DOMException && error.name === 'TimeoutError';

const toAbortError = (reason: unknown): unknown =>
    reason instanceof Error ? reason : new DOMException('Aborted', 'AbortError');

const normalizeApiUrl = (url: string): string => {
    if (!url.startsWith('/api/')) {
        return url;
    }

    const queryIndex = url.indexOf('?');
    const hashIndex = url.indexOf('#');
    const splitIndex = [queryIndex, hashIndex]
        .filter((index) => index >= 0)
        .sort((left, right) => left - right)[0] ?? -1;

    const path = splitIndex >= 0 ? url.slice(0, splitIndex) : url;
    const suffix = splitIndex >= 0 ? url.slice(splitIndex) : '';

    if (path.length > '/api/'.length && path.endsWith('/')) {
        return `${path.slice(0, -1)}${suffix}`;
    }

    return url;
};

const buildCacheKey = (url: string, init?: RequestInit, cacheKey?: string): string => {
    if (cacheKey) {
        return cacheKey;
    }

    const method = (init?.method || 'GET').toUpperCase();
    return `${method}:${url}`;
};

const readJsonOrThrow = async <T,>(response: Response, label: string): Promise<T> => {
    const contentType = response.headers.get('content-type') || '';

    if (!response.ok) {
        const body = await response.text();
        throw new SharedJsonFetchError(`${label} failed with ${response.status}: ${body.slice(0, 120)}`, response.status);
    }

    if (!contentType.toLowerCase().includes('application/json')) {
        const body = await response.text();
        // 2xx but non-JSON: NOT a server error, carries no retriable status.
        throw new SharedJsonFetchError(`${label} returned non-JSON content: ${body.slice(0, 120)}`);
    }

    return response.json() as Promise<T>;
};

// Evict every settled (and in-flight) cache entry whose key begins with
// `prefix`, returning the number of settled entries dropped. Used to turn a
// `refreshNonce` bump into a true invalidation: bumping the nonce rotates the
// cacheKey so the *mounted* component re-fetches, but the prior-nonce entries
// linger in the module-level cache for up to their TTL. A client-side remount
// (navigate away → back) resets the nonce to 0 and would otherwise re-read that
// stale entry until the TTL lapses or the page is hard-reloaded. Purging the
// per-entity keys when fresh data lands keeps a remount from serving the
// pre-refresh payload.
export const invalidateSharedJsonByPrefix = (prefix: string): number => {
    let removed = 0;
    for (const key of Array.from(settledRequests.keys())) {
        if (key.startsWith(prefix)) {
            settledRequests.delete(key);
            removed += 1;
        }
    }
    for (const key of Array.from(inFlightRequests.keys())) {
        if (key.startsWith(prefix)) {
            inFlightRequests.get(key)?.controller.abort();
            inFlightRequests.delete(key);
        }
    }
    return removed;
};

const getSettledValue = (cacheKey: string): SharedJsonFetchResult<unknown> | null => {
    const cached = settledRequests.get(cacheKey);
    if (!cached) {
        return null;
    }

    if (cached.expiresAt <= Date.now()) {
        settledRequests.delete(cacheKey);
        return null;
    }

    return cached.value;
};

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

// True only for failures worth retrying: a network error (fetch itself threw,
// which surfaces as a non-SharedJsonFetchError), a 5xx response, or a per-attempt
// timeout. Terminal: a 4xx (real 404), a non-JSON 2xx, or a controller abort
// (every caller left — there is no one to retry for).
const isRetriable = (error: unknown): boolean => {
    if (error instanceof SharedJsonFetchError) {
        return error.isServerError;
    }
    if (error instanceof DOMException) {
        // AbortSignal.timeout aborts with a TimeoutError — that is transient.
        // A plain AbortError means the controller was aborted (subscribers gone).
        return error.name === 'TimeoutError';
    }
    return true; // fetch threw → network/transport error
};

export const fetchSharedJson = <T,>(url: string, options: SharedJsonFetchOptions): Promise<SharedJsonFetchResult<T>> => {
    const { init, label, responseHeaders = [], ttlMs = 0, retry, signal, timeoutMs = DEFAULT_TIMEOUT_MS } = options;
    const normalizedUrl = normalizeApiUrl(url);
    const cacheKey = buildCacheKey(normalizedUrl, init, options.cacheKey);

    // An already-aborted caller fails fast and touches nothing else.
    if (signal?.aborted) {
        return Promise.reject(toAbortError(signal.reason));
    }

    if (resolvedCacheEnabled && ttlMs > 0) {
        const settledValue = getSettledValue(cacheKey);
        if (settledValue) {
            return Promise.resolve(settledValue as SharedJsonFetchResult<T>);
        }
    }

    // Reuse an in-flight shared request for this key, or start one. The retry
    // sequence lives INSIDE the deduped promise, so concurrent callers share one
    // request across all attempts (no thundering herd). Its own AbortController
    // is fired only when the last subscriber leaves (see release() below).
    let entry = inFlightRequests.get(cacheKey);
    if (!entry) {
        const controller = new AbortController();
        const promise = (async () => {
            const maxAttempts = retry ? retry.attempts + 1 : 1;
            for (let attempt = 1; ; attempt += 1) {
                try {
                    const attemptSignal = buildAttemptSignal(controller.signal, timeoutMs);
                    const response = await fetch(normalizedUrl, { ...init, signal: attemptSignal });
                    const data = await readJsonOrThrow<T>(response, label);
                    const headers = responseHeaders.reduce<SharedJsonFetchHeaders>((accumulator, headerName) => {
                        accumulator[headerName] = response.headers.get(headerName);
                        return accumulator;
                    }, {});

                    return {
                        data,
                        headers,
                    };
                } catch (error) {
                    // Stop on the last attempt, or on a non-retriable failure
                    // (4xx / non-JSON 2xx / controller abort).
                    if (attempt >= maxAttempts || !retry || !isRetriable(error)) {
                        throw error;
                    }
                    await delay(retry.backoffMs);
                }
            }
        })();

        const newEntry: InFlightEntry = { promise, controller, subscribers: 0, settled: false };
        inFlightRequests.set(cacheKey, newEntry);
        entry = newEntry;

        // Mark settled + store the settled cache on success, and drop the in-flight
        // entry once it resolves/rejects. This handler is attached first, so it runs
        // before any subscriber's continuation — `settled` is true by the time a
        // subscriber releases, which prevents aborting an already-finished fetch.
        // The rejection handler swallows so the shared promise never surfaces an
        // unhandled rejection; each subscriber attaches its own handler below.
        promise
            .then(
                (result) => {
                    newEntry.settled = true;
                    if (resolvedCacheEnabled && ttlMs > 0) {
                        settledRequests.set(cacheKey, {
                            expiresAt: Date.now() + ttlMs,
                            value: result,
                        });
                    }
                },
                () => {
                    newEntry.settled = true;
                },
            )
            .finally(() => {
                if (inFlightRequests.get(cacheKey) === newEntry) {
                    inFlightRequests.delete(cacheKey);
                }
            });
    }

    const activeEntry = entry;
    activeEntry.subscribers += 1;

    // Per-caller view: resolves/rejects with the shared request, but the caller's
    // own signal can reject *just this caller*. When the last subscriber leaves,
    // the shared fetch is aborted.
    return new Promise<SharedJsonFetchResult<T>>((resolve, reject) => {
        let done = false;

        const release = () => {
            activeEntry.subscribers -= 1;
            if (
                activeEntry.subscribers <= 0
                && !activeEntry.settled
                && inFlightRequests.get(cacheKey) === activeEntry
            ) {
                activeEntry.controller.abort();
                inFlightRequests.delete(cacheKey);
            }
        };

        const settle = (fn: () => void) => {
            if (done) {
                return;
            }
            done = true;
            if (signal) {
                signal.removeEventListener('abort', onAbort);
            }
            release();
            fn();
        };

        const onAbort = () => settle(() => reject(toAbortError(signal?.reason)));

        if (signal) {
            signal.addEventListener('abort', onAbort, { once: true });
        }

        activeEntry.promise.then(
            (value) => settle(() => resolve(value as SharedJsonFetchResult<T>)),
            (error) => settle(() => reject(error)),
        );
    });
};