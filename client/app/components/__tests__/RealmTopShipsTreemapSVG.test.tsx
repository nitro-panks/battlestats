import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import RealmTopShipsTreemapSVG from '../RealmTopShipsTreemapSVG';
import type { ListShip } from '../ShipLeaderboard';
import { trackEvent } from '../../lib/umami';

jest.mock('next/navigation', () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock('../../lib/umami', () => ({ trackEvent: jest.fn() }));

const mockTrackEvent = trackEvent as jest.Mock;

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
    afterEach(() => { window.localStorage.clear(); mockTrackEvent.mockClear(); });

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

    it('tile sub-line is WR alone, and hover shows the value/label pair tooltip', () => {
        const { container } = render(
            <RealmTopShipsTreemapSVG
                ships={[ship({ ship_id: 1, ship_name: 'Moskva', battles: 5000, win_rate: 58 })]}
                tier={10}
                type="Cruiser"
                wrPct={null}
            />,
        );
        // Tile sub-line: the WR% alone (the battles count was removed 2026-07-17).
        const texts = Array.from(container.querySelectorAll('svg text')).map((t) => t.textContent);
        expect(texts).toContain('58.0%');
        expect(texts.some((t) => t?.includes('5,000'))).toBe(false);
        // Tooltip: value/label pairs — battles, wrColor-tinted WR, bold class + tier.
        fireEvent.mouseMove(container.querySelector('svg rect')!, { clientX: 10, clientY: 10 });
        expect(screen.getByText('5,000')).toBeInTheDocument();
        expect(screen.getByText('battles')).toBeInTheDocument();
        const wr = screen.getByText('58.0%', { selector: 'span' });
        expect(wr.getAttribute('style')).toMatch(/color/);
        expect(screen.getByText('WR')).toBeInTheDocument();
        expect(screen.getByText('CA')).toBeInTheDocument();
        expect(screen.getByText('T10')).toHaveClass('font-semibold');
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

    it('defaults to the Map view and offers a Map/Plot toggle', () => {
        render(
            <RealmTopShipsTreemapSVG ships={[ship({})]} tier={10} type="Cruiser" wrPct={null} />,
        );
        expect(screen.getByRole('button', { name: 'Map' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: 'Plot' })).toHaveAttribute('aria-pressed', 'false');
    });

    it('toggling to Plot swaps tiles for a battles-vs-WR scatter, plots the full bucket, and persists the choice', () => {
        const onSelect = jest.fn();
        const { container } = render(
            <RealmTopShipsTreemapSVG
                ships={[
                    ship({ ship_id: 1, ship_name: 'Moskva', battles: 5000, win_rate: 58 }),
                    ship({ ship_id: 2, ship_name: 'Petropavlovsk', battles: 3000, win_rate: 52 }),
                    ship({ ship_id: 3, ship_name: 'Stalingrad', battles: 1500, win_rate: 61 }),
                ]}
                tier={10}
                type="Cruiser"
                wrPct={null}
                onSelect={onSelect}
            />,
        );
        // Map view first: one rect per ship, no scatter points.
        expect(container.querySelectorAll('svg rect').length).toBe(3);
        expect(container.querySelectorAll('svg circle.pt').length).toBe(0);

        fireEvent.click(screen.getByRole('button', { name: 'Plot' }));

        // Scatter: one dot per ship (no legibility cap), no treemap tiles.
        const dots = container.querySelectorAll('svg circle.pt');
        expect(dots.length).toBe(3);
        expect(container.querySelectorAll('svg rect').length).toBe(0);
        expect(screen.getByRole('button', { name: 'Plot' })).toHaveAttribute('aria-pressed', 'true');
        // Choice persisted for subsequent loads.
        expect(window.localStorage.getItem('bs-landing-ship-view')).toBe('plot');
        // The view change is tracked in Umami.
        expect(mockTrackEvent).toHaveBeenCalledWith(
            'landing-chart-view',
            expect.objectContaining({ view: 'plot' }),
        );

        // Re-clicking the already-active view is a no-op and fires nothing more.
        mockTrackEvent.mockClear();
        fireEvent.click(screen.getByRole('button', { name: 'Plot' }));
        expect(mockTrackEvent).not.toHaveBeenCalled();

        // A dot click drills the same way a tile does.
        fireEvent.click(dots[0]);
        expect(onSelect).toHaveBeenCalledWith(
            expect.objectContaining({ tier: 10, type: 'Cruiser' }),
        );
    });

    it('restores the persisted Plot preference on mount', () => {
        window.localStorage.setItem('bs-landing-ship-view', 'plot');
        const { container } = render(
            <RealmTopShipsTreemapSVG ships={[ship({})]} tier={10} type="Cruiser" wrPct={null} />,
        );
        expect(screen.getByRole('button', { name: 'Plot' })).toHaveAttribute('aria-pressed', 'true');
        expect(container.querySelectorAll('svg circle.pt').length).toBe(1);
        expect(container.querySelectorAll('svg rect').length).toBe(0);
    });
});
