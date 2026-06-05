import React from 'react';
import { act, render, screen } from '@testing-library/react';

import { RealmProvider, useRealm } from '../RealmContext';

// Mutable pathname so we can simulate client-side navigation (Link clicks).
let mockPathname = '/';
jest.mock('next/navigation', () => ({
    usePathname: () => mockPathname,
}));

const RealmProbe: React.FC = () => {
    const { realm } = useRealm();
    return <div data-testid="realm">{realm}</div>;
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
});
