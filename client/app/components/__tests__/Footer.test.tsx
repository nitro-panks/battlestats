import React from 'react';
import { render, screen } from '@testing-library/react';
import Footer from '../Footer';

describe('Footer', () => {
    it('links the creator name to the canonical player route', () => {
        render(<Footer />);

        const creatorLink = screen.getByRole('link', { name: 'lil_boots' });
        expect(creatorLink).toHaveAttribute('href', '/player/lil_boots');
    });
});