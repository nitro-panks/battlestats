import React from 'react';
import { render, screen } from '@testing-library/react';
import Footer from '../Footer';

describe('Footer', () => {
    it('links the creator name to the canonical player route', () => {
        render(<Footer />);

        const creatorLink = screen.getByRole('link', { name: 'lil_boots' });
        expect(creatorLink).toHaveAttribute('href', '/player/lil_boots');
    });

    it('renders the required Wargaming attribution, source, and support links', () => {
        render(<Footer />);

        expect(screen.getByText('© Wargaming.net. All rights reserved.')).toBeInTheDocument();
        expect(screen.getByText(/World of Warships data is sourced from the official Wargaming API/i)).toBeInTheDocument();
        expect(screen.getByText(/not affiliated with, endorsed by, or sponsored by Wargaming/i)).toBeInTheDocument();

        expect(screen.getByRole('link', { name: 'Official World of Warships website' })).toHaveAttribute('href', 'https://worldofwarships.com/');
        expect(screen.getByRole('link', { name: 'Wargaming Player Support' })).toHaveAttribute('href', 'https://www.support.wargaming.net/');
    });
});