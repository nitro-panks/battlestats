import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';

import RealmSelector from '../RealmSelector';
import { RealmProvider } from '../../context/RealmContext';

const replaceMock = jest.fn();

jest.mock('next/navigation', () => ({
    useRouter: () => ({
        replace: replaceMock,
    }),
}));

describe('RealmSelector', () => {
    beforeEach(() => {
        replaceMock.mockReset();
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
});