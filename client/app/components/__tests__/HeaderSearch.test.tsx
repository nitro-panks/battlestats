import React from 'react';
import { fireEvent, render, screen, act } from '@testing-library/react';

const pushMock = jest.fn();
let mockRealm = 'na';

const stableSearchParams = new URLSearchParams();

jest.mock('next/navigation', () => ({
    useRouter: () => ({ push: pushMock }),
    usePathname: () => '/',
    useSearchParams: () => stableSearchParams,
}));

jest.mock('../../context/RealmContext', () => ({
    useRealm: () => ({ realm: mockRealm }),
}));

jest.mock('../../lib/realmParams', () => ({
    withRealm: (url: string, realm: string) => `${url}${url.includes('?') ? '&' : '?'}realm=${realm}`,
}));

import HeaderSearch from '../HeaderSearch';

const buildOkResponse = (payload: unknown) => ({
    ok: true,
    json: async () => payload,
});

let fetchMock: jest.Mock;

beforeEach(() => {
    pushMock.mockReset();
    mockRealm = 'na';
    fetchMock = jest.fn(() => Promise.resolve(buildOkResponse([])));
    global.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
    jest.restoreAllMocks();
});

/** Helper: type into the search input and wait for the debounce + fetch to complete */
async function typeAndWaitForFetch(input: HTMLElement, value: string) {
    await act(async () => {
        fireEvent.change(input, { target: { value } });
    });
    // Wait for the 180ms debounce timer to fire and the fetch promise to resolve
    await act(async () => {
        await new Promise((r) => setTimeout(r, 300));
    });
}

describe('HeaderSearch toggle', () => {
    it('renders with player mode by default', () => {
        render(<HeaderSearch />);
        const input = screen.getByPlaceholderText('Search Players');
        expect(input).toBeTruthy();
        const toggle = screen.getByRole('switch');
        expect(toggle).toHaveAttribute('aria-checked', 'false');
        expect(toggle).toHaveAttribute('title', 'Search Players');
    });

    it('switches placeholder text when toggled to clan mode', () => {
        render(<HeaderSearch />);
        const toggle = screen.getByRole('switch');
        fireEvent.click(toggle);
        expect(screen.getByPlaceholderText('Search Clans')).toBeTruthy();
        expect(toggle).toHaveAttribute('aria-checked', 'true');
        expect(toggle).toHaveAttribute('title', 'Search Clans');
    });

    it('fetches from clan-suggestions endpoint in clan mode', async () => {
        fetchMock.mockImplementation(() =>
            Promise.resolve(buildOkResponse([
                { clan_id: 100, tag: 'TST', name: 'Test Clan', members_count: 20 },
            ]))
        );

        render(<HeaderSearch />);

        await act(async () => {
            fireEvent.click(screen.getByRole('switch'));
        });

        const input = screen.getByPlaceholderText('Search Clans');
        await typeAndWaitForFetch(input, 'test');

        const clanCall = fetchMock.mock.calls.find(
            (c: [string, ...unknown[]]) => c[0]?.includes('clan-suggestions')
        );
        expect(clanCall).toBeTruthy();
    });

    it('navigates to clan page when selecting a clan suggestion', async () => {
        fetchMock.mockImplementation(() =>
            Promise.resolve(buildOkResponse([
                { clan_id: 42, tag: 'ABC', name: 'Alpha Bravo', members_count: 30 },
            ]))
        );

        render(<HeaderSearch />);

        await act(async () => {
            fireEvent.click(screen.getByRole('switch'));
        });

        const input = screen.getByPlaceholderText('Search Clans');
        await typeAndWaitForFetch(input, 'alpha');

        // Open suggestion list (typing sets isSuggestionListOpen via onChange)
        expect(screen.getByText('[ABC]')).toBeTruthy();

        fireEvent.mouseDown(screen.getByText('Alpha Bravo'));

        expect(pushMock).toHaveBeenCalledWith(
            expect.stringContaining('/clan/42-alpha-bravo')
        );
    });

    it('clears suggestions when mode switches', async () => {
        fetchMock.mockImplementation(() =>
            Promise.resolve(buildOkResponse([
                { name: 'Player1', pvp_ratio: 55, is_hidden: false },
            ]))
        );

        render(<HeaderSearch />);
        const input = screen.getByPlaceholderText('Search Players');
        await typeAndWaitForFetch(input, 'player');

        // Suggestions should be visible (the onChange sets isSuggestionListOpen)
        expect(screen.queryByRole('listbox')).toBeTruthy();

        await act(async () => {
            fireEvent.click(screen.getByRole('switch'));
        });
        expect(screen.queryByRole('listbox')).toBeNull();
    });
});
