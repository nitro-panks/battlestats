import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import Footer from '../Footer';

const trackEventMock = jest.fn();
jest.mock('../../lib/umami', () => ({
    trackEvent: (...args: unknown[]) => trackEventMock(...args),
}));

describe('Footer creator-link tracking', () => {
    beforeEach(() => {
        trackEventMock.mockReset();
    });

    it('fires a footer-lil-boots umami event when the creator link is clicked', () => {
        render(<Footer />);

        const creatorLink = screen.getByRole('link', { name: 'lil_boots' });
        expect(creatorLink).toHaveAttribute('href', '/player/lil_boots?realm=na');

        fireEvent.click(creatorLink);
        expect(trackEventMock).toHaveBeenCalledWith('footer-lil-boots', { realm: 'na' });
    });

    it('fires outbound-link events with a stable target for each external link', () => {
        render(<Footer />);

        fireEvent.click(screen.getByRole('link', { name: 'Fork me on GitHub' }));
        expect(trackEventMock).toHaveBeenCalledWith('outbound-link', { target: 'github' });

        fireEvent.click(screen.getByRole('link', { name: 'Official World of Warships website' }));
        expect(trackEventMock).toHaveBeenCalledWith('outbound-link', { target: 'wows' });
    });

    it('fires streamer-open when the Add a streamer button is clicked', () => {
        render(<Footer />);

        fireEvent.click(screen.getByRole('button', { name: 'Add a streamer!' }));
        expect(trackEventMock).toHaveBeenCalledWith('streamer-open');
    });
});
