import { fetchSharedJson } from '../sharedJsonFetch';

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
});