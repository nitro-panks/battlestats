import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';

import RealmSelector from '../RealmSelector';
import ThemeToggle from '../ThemeToggle';
import { RealmProvider } from '../../context/RealmContext';
import { ThemeProvider } from '../../context/ThemeContext';

const pushMock = jest.fn();

jest.mock('next/navigation', () => ({
    useRouter: () => ({
        push: pushMock,
    }),
    usePathname: () => '/',
}));

describe('Landing dropdown styling', () => {
    beforeEach(() => {
        pushMock.mockReset();
        window.localStorage.clear();
        Object.defineProperty(window, 'matchMedia', {
            writable: true,
            value: jest.fn().mockImplementation(() => ({
                matches: false,
                media: '(prefers-color-scheme: dark)',
                onchange: null,
                addListener: jest.fn(),
                removeListener: jest.fn(),
                addEventListener: jest.fn(),
                removeEventListener: jest.fn(),
                dispatchEvent: jest.fn(),
            })),
        });
    });

    it('keeps the inactive theme option readable', () => {
        render(
            <ThemeProvider>
                <ThemeToggle />
            </ThemeProvider>
        );

        fireEvent.click(screen.getByRole('button', { name: /theme:/i }));

        const darkOption = screen.getByRole('option', { name: /dark/i });
        expect(darkOption).toHaveStyle({ color: 'var(--text-secondary)' });
    });

    it('keeps the inactive realm option readable', () => {
        render(
            <RealmProvider>
                <RealmSelector />
            </RealmProvider>
        );

        fireEvent.click(screen.getByRole('button', { name: /realm:/i }));

        const euOption = screen.getByRole('option', { name: 'EU' });
        expect(euOption).toHaveStyle({ color: 'var(--text-secondary)' });
    });
});