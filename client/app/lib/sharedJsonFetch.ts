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
}

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

let chartFetchesInFlight = 0;

export const getChartFetchesInFlight = (): number => chartFetchesInFlight;
export const incrementChartFetches = (): void => { chartFetchesInFlight += 1; };
export const decrementChartFetches = (): void => { chartFetchesInFlight = Math.max(0, chartFetchesInFlight - 1); };

const inFlightRequests = new Map<string, Promise<SharedJsonFetchResult<unknown>>>();
const settledRequests = new Map<string, SettledCacheEntry>();
const resolvedCacheEnabled = process.env.NODE_ENV !== 'test';

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
// which surfaces as a non-SharedJsonFetchError) or a 5xx response. A 4xx (real
// 404) and a non-JSON 2xx are terminal — they carry no retriable status.
const isRetriable = (error: unknown): boolean => {
    if (error instanceof SharedJsonFetchError) {
        return error.isServerError;
    }
    return true; // fetch threw → network/transport error
};

export const fetchSharedJson = async <T,>(url: string, options: SharedJsonFetchOptions): Promise<SharedJsonFetchResult<T>> => {
    const { init, label, responseHeaders = [], ttlMs = 0, retry } = options;
    const normalizedUrl = normalizeApiUrl(url);
    const cacheKey = buildCacheKey(normalizedUrl, init, options.cacheKey);

    if (resolvedCacheEnabled && ttlMs > 0) {
        const settledValue = getSettledValue(cacheKey);
        if (settledValue) {
            return settledValue as SharedJsonFetchResult<T>;
        }
    }

    const existingRequest = inFlightRequests.get(cacheKey);
    if (existingRequest) {
        return existingRequest as Promise<SharedJsonFetchResult<T>>;
    }

    // The whole retry sequence lives INSIDE the deduped IIFE, so concurrent
    // callers share one in-flight promise across all attempts (no thundering
    // herd) and the settled-cache stores the final success.
    const request = (async () => {
        const maxAttempts = retry ? retry.attempts + 1 : 1;
        for (let attempt = 1; ; attempt += 1) {
            try {
                const response = await fetch(normalizedUrl, init);
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
                // Stop on the last attempt, or on a non-retriable (4xx / non-JSON 2xx) failure.
                if (attempt >= maxAttempts || !retry || !isRetriable(error)) {
                    throw error;
                }
                await delay(retry.backoffMs);
            }
        }
    })();

    inFlightRequests.set(cacheKey, request);

    try {
        const result = await request;
        if (resolvedCacheEnabled && ttlMs > 0) {
            settledRequests.set(cacheKey, {
                expiresAt: Date.now() + ttlMs,
                value: result,
            });
        }
        return result;
    } finally {
        if (inFlightRequests.get(cacheKey) === request) {
            inFlightRequests.delete(cacheKey);
        }
    }
};