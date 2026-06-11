import { trackEntityDetailView } from '../visitAnalytics';

describe('visitAnalytics', () => {
    let fetchMock: jest.Mock;

    beforeEach(() => {
        fetchMock = jest.fn().mockResolvedValue({ ok: true });
        global.fetch = fetchMock;
        window.history.replaceState({}, '', '/player/player-one');
        Object.defineProperty(document, 'referrer', {
            value: 'http://localhost:3001/',
            configurable: true,
        });
        Object.defineProperty(document, 'cookie', {
            value: '',
            writable: true,
            configurable: true,
        });
        window.sessionStorage.clear();
    });

    it('posts the first-party analytics payload', async () => {
        await trackEntityDetailView({
            entityType: 'player',
            entityId: 77,
            entityName: 'Player One',
            entitySlug: 'player-one',
        });

        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(fetchMock).toHaveBeenCalledWith('/api/analytics/entity-view', expect.objectContaining({
            method: 'POST',
        }));
    });
});