import React from 'react';
import { act, render, screen } from '@testing-library/react';

import { RealmProvider, useRealm, useDisplayRealm } from '../RealmContext';

// Mutable pathname so we can simulate client-side navigation (Link clicks).
let mockPathname = '/';
jest.mock('next/navigation', () => ({
    usePathname: () => mockPathname,
}));

const RealmProbe: React.FC = () => {
    const { realm } = useRealm();
    return <div data-testid="realm">{realm}</div>;
};

const RealmSetterProbe: React.FC = () => {
    const { realm, setRealm } = useRealm();
    return (
        <button data-testid="realm" type="button" onClick={() => setRealm('eu')}>
            {realm}
        </button>
    );
};

describe('RealmProvider URL realm sync', () => {
    beforeEach(() => {
        window.localStorage.clear();
        mockPathname = '/';
    });

    it('adopts an explicit ?realm= on client-side navigation, not just on full load', () => {
        // Start on an ASIA page (as if the user selected ASIA / loaded an asia URL).
        window.history.replaceState({}, '', 'http://localhost/?realm=asia');
        const { rerender } = render(
            <RealmProvider>
                <RealmProbe />
            </RealmProvider>,
        );
        expect(screen.getByTestId('realm').textContent).toBe('asia');

        // Simulate clicking the footer's na-only link: URL + pathname change, no reload.
        act(() => {
            window.history.replaceState({}, '', 'http://localhost/player/lil_boots?realm=na');
            mockPathname = '/player/lil_boots';
        });
        rerender(
            <RealmProvider>
                <RealmProbe />
            </RealmProvider>,
        );

        // The bug: this stayed 'asia' (404) until a refresh. Now it follows the URL.
        expect(screen.getByTestId('realm').textContent).toBe('na');
    });

    it('keeps the stored realm when navigating to a URL without ?realm=', () => {
        window.localStorage.setItem('bs-realm', 'eu');
        window.history.replaceState({}, '', 'http://localhost/');
        const { rerender } = render(
            <RealmProvider>
                <RealmProbe />
            </RealmProvider>,
        );
        expect(screen.getByTestId('realm').textContent).toBe('eu');

        act(() => {
            window.history.replaceState({}, '', 'http://localhost/player/SomeEuPlayer');
            mockPathname = '/player/SomeEuPlayer';
        });
        rerender(
            <RealmProvider>
                <RealmProbe />
            </RealmProvider>,
        );

        expect(screen.getByTestId('realm').textContent).toBe('eu');
    });

    it('persists the realm to localStorage when the user selects one', () => {
        // The selection itself must become the stored browser preference, so a
        // later visit (no ?realm=) restores it. This locks the write half of
        // "the realm selection stays in the browser".
        window.history.replaceState({}, '', 'http://localhost/');
        render(
            <RealmProvider>
                <RealmSetterProbe />
            </RealmProvider>,
        );
        expect(window.localStorage.getItem('bs-realm')).not.toBe('eu');

        act(() => {
            screen.getByTestId('realm').click();
        });

        expect(screen.getByTestId('realm').textContent).toBe('eu');
        expect(window.localStorage.getItem('bs-realm')).toBe('eu');
    });

    it('resolves the stored realm synchronously so fetches use it on first render', () => {
        // The fetch-facing realm (useRealm) must be the stored value from the
        // very first render — not 'na' corrected later — so a bare ?realm=-less
        // entity link fetches the right realm on its first request.
        window.localStorage.setItem('bs-realm', 'asia');
        window.history.replaceState({}, '', 'http://localhost/player/SomeAsiaPlayer');
        mockPathname = '/player/SomeAsiaPlayer';

        let firstRealm: string | undefined;
        const CaptureFirstRender: React.FC = () => {
            const { realm } = useRealm();
            if (firstRealm === undefined) {
                firstRealm = realm;
            }
            return <div>{realm}</div>;
        };
        render(
            <RealmProvider>
                <CaptureFirstRender />
            </RealmProvider>,
        );
        expect(firstRealm).toBe('asia');
    });

    it('useDisplayRealm settles on the resolved realm after mount', () => {
        window.localStorage.setItem('bs-realm', 'eu');
        window.history.replaceState({}, '', 'http://localhost/');
        const DisplayProbe: React.FC = () => (
            <div data-testid="display">{useDisplayRealm()}</div>
        );
        render(
            <RealmProvider>
                <DisplayProbe />
            </RealmProvider>,
        );
        // After mount (effects flushed by render), the display realm matches.
        expect(screen.getByTestId('display').textContent).toBe('eu');
    });
});
