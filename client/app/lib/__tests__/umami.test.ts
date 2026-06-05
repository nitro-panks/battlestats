import { trackEvent } from '../umami';

describe('trackEvent (Umami wrapper)', () => {
    const originalUmami = window.umami;

    afterEach(() => {
        window.umami = originalUmami;
    });

    it('forwards the event name and data to window.umami.track', () => {
        const track = jest.fn();
        window.umami = { track };

        trackEvent('insights-tab', { tab: 'ranked' });

        expect(track).toHaveBeenCalledWith('insights-tab', { tab: 'ranked' });
    });

    it('no-ops when the tracker is absent (does not throw)', () => {
        window.umami = undefined;
        expect(() => trackEvent('battle-history-sort', { key: 'win_rate' })).not.toThrow();
    });

    it('swallows tracker errors so analytics never breaks the UI', () => {
        window.umami = { track: () => { throw new Error('tracker down'); } };
        expect(() => trackEvent('insights-tab', { tab: 'ships' })).not.toThrow();
    });
});
