import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';

import RealmSelector from '../RealmSelector';
import { RealmProvider } from '../../context/RealmContext';

const replaceMock = jest.fn();
const trackEventMock = jest.fn();

jest.mock('next/navigation', () => ({
    useRouter: () => ({
        replace: replaceMock,
    }),
    usePathname: () => '/player/Someone',
}));

jest.mock('../../lib/umami', () => ({
    trackEvent: (...args: unknown[]) => trackEventMock(...args),
}));

describe('RealmSelector', () => {
    beforeEach(() => {
        replaceMock.mockReset();
        trackEventMock.mockReset();
        window.localStorage.clear();
        window.history.replaceState({}, '', 'http://localhost/player/Someone?realm=na');
    });

    it('returns to landing when switching realms from a nested route', () => {
        render(
            <RealmProvider>
                <RealmSelector />
            </RealmProvider>
        );

        fireEvent.click(screen.getByRole('button', { name: /realm:/i }));
        fireEvent.click(screen.getByRole('option', { name: 'EU' }));

        expect(replaceMock).toHaveBeenCalledWith('/?realm=eu');
    });

    it('tracks a realm-change umami event with the chosen realm', () => {
        render(
            <RealmProvider>
                <RealmSelector />
            </RealmProvider>
        );

        fireEvent.click(screen.getByRole('button', { name: /realm:/i }));
        fireEvent.click(screen.getByRole('option', { name: 'EU' }));

        expect(trackEventMock).toHaveBeenCalledWith('realm-change', { realm: 'eu' });
    });
});