import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import HeaderSearch from '../HeaderSearch';

const pushMock = jest.fn();
let mockPathname = '/';
let mockQueryParam = '';
const mockSearchParams = {
    get: (key: string) => (key === 'q' && mockQueryParam ? mockQueryParam : null),
};

jest.mock('next/navigation', () => ({
    useRouter: () => ({
        push: pushMock,
    }),
    usePathname: () => mockPathname,
    useSearchParams: () => mockSearchParams,
}));

describe('HeaderSearch', () => {
    beforeEach(() => {
        pushMock.mockReset();
        mockPathname = '/';
        mockQueryParam = '';
    });

    it('keeps the search bar clear on routed player detail views', () => {
        mockPathname = '/player/Player%20One?realm=na';

        render(<HeaderSearch />);

        expect(screen.getByLabelText('Search player')).toHaveValue('');
    });

    it('shows the active q parameter when the search bar is being used', () => {
        mockPathname = '/';
        mockQueryParam = 'Player One';

        render(<HeaderSearch />);

        expect(screen.getByLabelText('Search player')).toHaveValue('Player One');
    });

    it('routes to the selected player when submitted', async () => {
        render(<HeaderSearch />);

        fireEvent.change(screen.getByLabelText('Search player'), {
            target: { value: 'Player One' },
        });
        fireEvent.click(screen.getByRole('button', { name: 'Go' }));

        await waitFor(() => {
            expect(pushMock).toHaveBeenCalledWith('/player/Player%20One?realm=na');
        });
    });
});