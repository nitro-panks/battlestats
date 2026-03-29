import React from 'react';
import { render, screen } from '@testing-library/react';
import Footer from '../Footer';

describe('Footer', () => {
    it('links the creator name to the canonical player route', () => {
        render(<Footer />);

        const creatorLink = screen.getByRole('link', { name: 'lil_boots' });
        expect(creatorLink).toHaveAttribute('href', '/player/lil_boots');
    });

    it('renders CC license, GitHub link, and Wargaming attribution', () => {
        render(<Footer />);

        expect(screen.getByRole('link', { name: 'CC BY-NC-SA 4.0' })).toHaveAttribute('href', 'https://creativecommons.org/licenses/by-nc-sa/4.0/');
        expect(screen.getByRole('link', { name: 'Fork me on GitHub' })).toHaveAttribute('href', 'https://github.com/august-schlubach/battlestats');
        expect(screen.getByText(/not affiliated with Wargaming/i)).toBeInTheDocument();

        expect(screen.getByRole('link', { name: 'Official World of Warships website' })).toHaveAttribute('href', 'https://worldofwarships.com/');
        expect(screen.getByRole('link', { name: 'Wargaming Player Support' })).toHaveAttribute('href', 'https://www.support.wargaming.net/');
    });
});