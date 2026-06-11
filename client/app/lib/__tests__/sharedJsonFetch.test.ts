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

        expect(fetchMock).toHaveBeenCalledWith('/api/fetch/clan_members/123?foo=bar', undefined);
    });

    it('leaves non-api urls unchanged', async () => {
        await fetchSharedJson('/player/example/', {
            label: 'player page',
        });

        expect(fetchMock).toHaveBeenCalledWith('/player/example/', undefined);
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
});