import { fetchSharedJson, SharedJsonFetchError } from '../sharedJsonFetch';

// Build a Response-like stub for the global fetch mock.
const jsonResponse = (body: unknown) => ({
    ok: true,
    status: 200,
    headers: { get: (name: string) => (name.toLowerCase() === 'content-type' ? 'application/json' : null) },
    json: jest.fn().mockResolvedValue(body),
    text: jest.fn(),
});

const errorResponse = (status: number) => ({
    ok: false,
    status,
    headers: { get: () => 'text/html' },
    json: jest.fn(),
    text: jest.fn().mockResolvedValue('<html>error</html>'),
});

describe('sharedJsonFetch', () => {
    let fetchMock: jest.Mock;

    beforeEach(() => {
        fetchMock = jest.fn().mockResolvedValue({
            ok: true,
            headers: {
                get: jest.fn().mockImplementation((name: string) => {
                    if (name.toLowerCase() === 'content-type') {
                        return 'application/json';
                    }
                    return null;
                }),
            },
            json: jest.fn().mockResolvedValue({ ok: true }),
            text: jest.fn(),
        });
        global.fetch = fetchMock;
    });

    it('normalizes same-origin api urls before fetching', async () => {
        await fetchSharedJson('/api/fetch/clan_members/123/?foo=bar', {
            label: 'clan members',
        });

        // fetch always receives a combined abort signal now (for the per-request timeout).
        expect(fetchMock).toHaveBeenCalledWith(
            '/api/fetch/clan_members/123?foo=bar',
            expect.objectContaining({ signal: expect.anything() }),
        );
    });

    it('leaves non-api urls unchanged', async () => {
        await fetchSharedJson('/player/example/', {
            label: 'player page',
        });

        expect(fetchMock).toHaveBeenCalledWith(
            '/player/example/',
            expect.objectContaining({ signal: expect.anything() }),
        );
    });

    describe('retry option', () => {
        it('does NOT retry by default (opt-in only)', async () => {
            fetchMock.mockReset();
            fetchMock.mockResolvedValue(errorResponse(503));

            await expect(fetchSharedJson('/api/player/a', { label: 'p' }))
                .rejects.toBeInstanceOf(SharedJsonFetchError);
            expect(fetchMock).toHaveBeenCalledTimes(1);
        });

        it('retries a 5xx then succeeds', async () => {
            fetchMock.mockReset();
            fetchMock
                .mockResolvedValueOnce(errorResponse(502))
                .mockResolvedValueOnce(jsonResponse({ ok: true }));

            const result = await fetchSharedJson<{ ok: boolean }>('/api/player/b', {
                label: 'p',
                cacheKey: 'retry-5xx-succeeds',
                retry: { attempts: 2, backoffMs: 1 },
            });

            expect(result.data).toEqual({ ok: true });
            expect(fetchMock).toHaveBeenCalledTimes(2);
        });

        it('exhausts retries on persistent 5xx and throws a server error', async () => {
            fetchMock.mockReset();
            fetchMock.mockResolvedValue(errorResponse(500));

            await expect(fetchSharedJson('/api/player/c', {
                label: 'p',
                cacheKey: 'retry-5xx-exhaust',
                retry: { attempts: 2, backoffMs: 1 },
            })).rejects.toMatchObject({ status: 500, isServerError: true });
            // 1 initial + 2 retries.
            expect(fetchMock).toHaveBeenCalledTimes(3);
        });

        it('retries a network error (fetch threw) then succeeds', async () => {
            fetchMock.mockReset();
            fetchMock
                .mockRejectedValueOnce(new TypeError('Failed to fetch'))
                .mockResolvedValueOnce(jsonResponse({ ok: true }));

            const result = await fetchSharedJson<{ ok: boolean }>('/api/player/d', {
                label: 'p',
                cacheKey: 'retry-network',
                retry: { attempts: 2, backoffMs: 1 },
            });

            expect(result.data).toEqual({ ok: true });
            expect(fetchMock).toHaveBeenCalledTimes(2);
        });

        it('retries a 429 throttle (when retry enabled) then succeeds', async () => {
            fetchMock.mockReset();
            fetchMock
                .mockResolvedValueOnce(errorResponse(429))
                .mockResolvedValueOnce(jsonResponse({ ok: true }));

            const result = await fetchSharedJson<{ ok: boolean }>('/api/player/throttled', {
                label: 'p',
                cacheKey: 'retry-429',
                retry: { attempts: 2, backoffMs: 1 },
            });

            expect(result.data).toEqual({ ok: true });
            expect(fetchMock).toHaveBeenCalledTimes(2);
        });

        it('does NOT retry a 429 without retry opt-in', async () => {
            fetchMock.mockReset();
            fetchMock.mockResolvedValue(errorResponse(429));

            await expect(fetchSharedJson('/api/player/throttled-noretry', { label: 'p', cacheKey: 'no-retry-429' }))
                .rejects.toMatchObject({ status: 429, isThrottled: true });
            expect(fetchMock).toHaveBeenCalledTimes(1);
        });

        it('does NOT retry a 404 even when retry is enabled', async () => {
            fetchMock.mockReset();
            fetchMock.mockResolvedValue(errorResponse(404));

            await expect(fetchSharedJson('/api/player/e', {
                label: 'p',
                cacheKey: 'retry-404',
                retry: { attempts: 2, backoffMs: 1 },
            })).rejects.toMatchObject({ status: 404, isServerError: false });
            // Terminal client error → exactly one fetch, no retry.
            expect(fetchMock).toHaveBeenCalledTimes(1);
        });
    });

    describe('cancellation', () => {
        it('rejects immediately with an AbortError if the caller signal is already aborted', async () => {
            fetchMock.mockReset();
            const controller = new AbortController();
            controller.abort();

            await expect(fetchSharedJson('/api/player/abort-pre', {
                label: 'p',
                cacheKey: 'abort-pre',
                signal: controller.signal,
            })).rejects.toHaveProperty('name', 'AbortError');
            // Never even hit the network.
            expect(fetchMock).not.toHaveBeenCalled();
        });

        it('rejects the caller when its signal aborts mid-flight', async () => {
            fetchMock.mockReset();
            // A fetch that never settles on its own.
            fetchMock.mockImplementation(() => new Promise(() => {}));
            const controller = new AbortController();

            const pending = fetchSharedJson('/api/player/abort-mid', {
                label: 'p',
                cacheKey: 'abort-mid',
                signal: controller.signal,
            });

            controller.abort();

            await expect(pending).rejects.toHaveProperty('name', 'AbortError');
        });

        it('does NOT abort the shared fetch while another subscriber still awaits it', async () => {
            fetchMock.mockReset();
            let abortedDuringFlight = false;
            fetchMock.mockImplementation((_url: string, init?: RequestInit) => new Promise((resolve) => {
                init?.signal?.addEventListener('abort', () => { abortedDuringFlight = true; });
                // resolve shortly after, so the surviving subscriber gets data.
                setTimeout(() => resolve(jsonResponse({ ok: true })), 5);
            }));

            const aborter = new AbortController();
            const keeper = new AbortController();

            const leaving = fetchSharedJson('/api/player/shared', {
                label: 'p', cacheKey: 'shared-dedup', signal: aborter.signal,
            });
            const staying = fetchSharedJson<{ ok: boolean }>('/api/player/shared', {
                label: 'p', cacheKey: 'shared-dedup', signal: keeper.signal,
            });

            // Let the queue grant a slot and dispatch the (single, deduped) fetch.
            await new Promise((resolve) => setTimeout(resolve, 0));
            expect(fetchMock).toHaveBeenCalledTimes(1);

            // Abort one subscriber while the shared fetch is still in flight.
            aborter.abort();
            await expect(leaving).rejects.toHaveProperty('name', 'AbortError');

            // The surviving subscriber still resolves; the shared fetch was never aborted.
            await expect(staying).resolves.toMatchObject({ data: { ok: true } });
            expect(abortedDuringFlight).toBe(false);
            expect(fetchMock).toHaveBeenCalledTimes(1);
        });
    });
});