type SharedJsonFetchHeaders = Record<string, string | null>;

export interface SharedJsonFetchResult<T> {
    data: T;
    headers: SharedJsonFetchHeaders;
}

interface SharedJsonFetchOptions {
    init?: RequestInit;
    label: string;
    cacheKey?: string;
    responseHeaders?: string[];
    ttlMs?: number;
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
        throw new Error(`${label} failed with ${response.status}: ${body.slice(0, 120)}`);
    }

    if (!contentType.toLowerCase().includes('application/json')) {
        const body = await response.text();
        throw new Error(`${label} returned non-JSON content: ${body.slice(0, 120)}`);
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

export const fetchSharedJson = async <T,>(url: string, options: SharedJsonFetchOptions): Promise<SharedJsonFetchResult<T>> => {
    const { init, label, responseHeaders = [], ttlMs = 0 } = options;
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

    const request = (async () => {
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