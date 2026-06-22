import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import ShipToolLink, { shiptoolUrl } from '../ShipToolLink';

const trackEventMock = jest.fn();
jest.mock('../../lib/umami', () => ({
    trackEvent: (...args: unknown[]) => trackEventMock(...args),
}));

describe('ShipToolLink', () => {
    beforeEach(() => trackEventMock.mockClear());

    it('builds the shiptool.st params URL from a code', () => {
        expect(shiptoolUrl('RC110')).toBe('https://shiptool.st/params?S=RC110');
    });

    it('renders an external link to the ship on shiptool.st', () => {
        render(<ShipToolLink code="RC110" shipName="Moskva" realm="na" shipId={42} />);
        const link = screen.getByRole('link', { name: /Moskva on Ship Tool/i });
        expect(link).toHaveAttribute('href', 'https://shiptool.st/params?S=RC110');
        expect(link).toHaveAttribute('target', '_blank');
        expect(link).toHaveAttribute('rel', 'noreferrer');
    });

    it('tracks a shiptool-click event with realm and ship id', () => {
        render(<ShipToolLink code="RC110" shipName="Moskva" realm="na" shipId={42} />);
        fireEvent.click(screen.getByRole('link', { name: /Moskva on Ship Tool/i }));
        expect(trackEventMock).toHaveBeenCalledWith('shiptool-click', {
            realm: 'na',
            ship_id: 42,
        });
    });

    it('renders nothing when no code is available', () => {
        const { container } = render(
            <ShipToolLink code={null} shipName="Moskva" />,
        );
        expect(container).toBeEmptyDOMElement();
    });
});
