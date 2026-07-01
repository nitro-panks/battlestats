import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import RealmTopShipsTreemapSVG from '../RealmTopShipsTreemapSVG';
import type { ListShip } from '../ShipLeaderboard';

jest.mock('next/navigation', () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock('../../lib/umami', () => ({ trackEvent: jest.fn() }));

// The treemap sizes itself off a ResizeObserver; jest's default stub reports no
// width so d3 draws nothing. Give it a real width so tiles + labels render and
// the click handler is exercised.
class WidthReportingResizeObserver {
    private cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) { this.cb = cb; }
    observe() {
        this.cb(
            [{ contentRect: { width: 800 } } as ResizeObserverEntry],
            this as unknown as ResizeObserver,
        );
    }
    unobserve() {}
    disconnect() {}
}

const ship = (over: Partial<ListShip>): ListShip => ({
    ship_id: 1,
    ship_name: 'Moskva',
    ship_type: 'Cruiser',
    tier: 10,
    nation: 'ussr',
    is_premium: false,
    battles: 1000,
    win_rate: 55,
    avg_damage: 90000,
    kills_per_battle: 1.1,
    ...over,
});

describe('RealmTopShipsTreemapSVG (presentational)', () => {
    const realRO = globalThis.ResizeObserver;
    beforeAll(() => { globalThis.ResizeObserver = WidthReportingResizeObserver as unknown as typeof ResizeObserver; });
    afterAll(() => { globalThis.ResizeObserver = realRO; });

    it('renders a bucket-specific heading reflecting tier, type and WR filter', () => {
        render(
            <RealmTopShipsTreemapSVG
                ships={[ship({})]}
                tier={10}
                type="Cruiser"
                wrPct={50}
                windowStart="2026-06-01"
                windowEnd="2026-07-01"
            />,
        );
        const heading = screen.getByRole('heading');
        expect(heading.textContent).toContain('T10 Cruisers');
        expect(heading.textContent).toContain('top 50%');
    });

    it('draws a tile per ship and drills via onSelect on click', () => {
        const onSelect = jest.fn();
        const { container } = render(
            <RealmTopShipsTreemapSVG
                ships={[
                    ship({ ship_id: 1, ship_name: 'Moskva', battles: 5000, win_rate: 58 }),
                    ship({ ship_id: 2, ship_name: 'Petropavlovsk', battles: 3000, win_rate: 52 }),
                ]}
                tier={10}
                type="Cruiser"
                wrPct={null}
                onSelect={onSelect}
            />,
        );
        const rects = container.querySelectorAll('svg rect');
        expect(rects.length).toBe(2);
        // First tile is the most-played ship (largest area).
        fireEvent.click(rects[0]);
        expect(onSelect).toHaveBeenCalledWith(
            expect.objectContaining({ id: 1, name: 'Moskva', tier: 10, type: 'Cruiser' }),
        );
    });

    it('renders a plain empty box (no ship tiles) for a shipless easter-egg bucket', () => {
        const { container } = render(
            <RealmTopShipsTreemapSVG
                ships={[]}
                tier={9}
                type="Submarine"
                wrPct={null}
                empty
            />,
        );
        // The box is present (accessible label) but carries no ship tiles and no
        // caption text — a plain empty placeholder that holds the treemap's space.
        const box = screen.getByLabelText('No ships for this selection');
        expect(box).toBeInTheDocument();
        expect(box.textContent).toBe('');
        expect(container.querySelectorAll('svg rect').length).toBe(0);
    });

    it('shows a loading placeholder before the first bucket resolves', () => {
        render(
            <RealmTopShipsTreemapSVG
                ships={[]}
                tier={10}
                type="Battleship"
                wrPct={50}
                loading
            />,
        );
        expect(screen.getByText('Loading ships…')).toBeInTheDocument();
    });
});
