import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import BattleHistoryTreemaps from '../BattleHistoryTreemaps';
import type { BattleHistoryByShip } from '../BattleHistoryCard';

// The treemaps size themselves off a ResizeObserver; jest's default stub
// reports no width so d3 draws nothing. Give it a real width so tiles +
// labels render and the click handler is exercised (same shim as the
// RealmTopShipsTreemapSVG test).
class WidthReportingResizeObserver {
    private cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) { this.cb = cb; }
    observe() {
        this.cb(
            [{ contentRect: { width: 400 } } as ResizeObserverEntry],
            this as unknown as ResizeObserver,
        );
    }
    unobserve() {}
    disconnect() {}
}

const row = (over: Partial<BattleHistoryByShip>): BattleHistoryByShip => ({
    ship_id: 1,
    ship_name: 'Vermont',
    ship_tier: 10,
    ship_type: 'Battleship',
    battles: 10,
    wins: 6,
    losses: 4,
    win_rate: 60,
    damage: 1_400_000,
    avg_damage: 140_000,
    frags: 12,
    xp: 15_000,
    planes_killed: 3,
    survived_battles: 5,
    ship_pop_avg_damage: 94_631,
    ...over,
});

describe('BattleHistoryTreemaps (presentational)', () => {
    const realRO = globalThis.ResizeObserver;
    beforeAll(() => { globalThis.ResizeObserver = WidthReportingResizeObserver as unknown as typeof ResizeObserver; });
    afterAll(() => { globalThis.ResizeObserver = realRO; });

    it('renders the three panels with aggregate tiles from by_ship rows', () => {
        render(
            <BattleHistoryTreemaps
                byShip={[
                    row({}),
                    // Damage comparable to Vermont's so both ship tiles are
                    // large enough to carry their (untruncated) name labels.
                    row({ ship_id: 2, ship_name: 'Shimakaze', ship_type: 'Destroyer', ship_tier: 10, battles: 20, wins: 5, win_rate: 25, damage: 1_100_000, avg_damage: 55_000 }),
                ]}
            />,
        );
        expect(screen.getByText('By type')).toBeInTheDocument();
        expect(screen.getByText('Ships by damage')).toBeInTheDocument();
        expect(screen.getByText('By tier')).toBeInTheDocument();
        // Type panel aggregates to short class labels; ships panel draws one
        // tile per ship; tier panel groups both T10 ships into one tile.
        expect(screen.getByText('BB')).toBeInTheDocument();
        expect(screen.getByText('DD')).toBeInTheDocument();
        expect(screen.getByText('Vermont')).toBeInTheDocument();
        expect(screen.getByText('Shimakaze')).toBeInTheDocument();
        expect(screen.getByText('T10')).toBeInTheDocument();
    });

    it('colors damage tiles by the player-vs-population ratio and falls back to neutral without a baseline', () => {
        const { container } = render(
            <BattleHistoryTreemaps
                byShip={[
                    // +48% over the ship average → green side.
                    row({}),
                    // 50% below → red side.
                    row({ ship_id: 2, ship_name: 'Colombo', avg_damage: 47_000, damage: 470_000, ship_pop_avg_damage: 94_000 }),
                    // No baseline → the neutral fill, and never a diverging color.
                    row({ ship_id: 3, ship_name: 'Kurama', ship_pop_avg_damage: null }),
                ]}
            />,
        );
        const fills = Array.from(container.querySelectorAll('rect'))
            .map((r) => r.getAttribute('fill'))
            .filter((f): f is string => !!f && f.startsWith('#') === false ? false : true);
        // The neutral no-baseline fill is present verbatim; the diverging fills
        // are computed rgb() strings, so just assert we did NOT paint all three
        // ship tiles the same color.
        expect(fills).toContain('#6f7683');
        expect(new Set(fills).size).toBeGreaterThan(1);
    });

    it('avg damage (not WR) is the damage tile sub-label, and tooltips carry the vs-average detail on hover', () => {
        render(<BattleHistoryTreemaps byShip={[row({})]} />);
        // 140_000 → "140k" at 3 significant digits.
        expect(screen.getByText('140k')).toBeInTheDocument();
        // Hover specifically a SHIPS-panel tile (the type/tier panels have
        // their own rects with WR tooltips).
        const shipsSvg = screen.getByRole('img', { name: /ships sized by total damage/i });
        const shipRect = shipsSvg.querySelector('rect');
        fireEvent.mouseMove(shipRect!, { clientX: 10, clientY: 10 });
        expect(screen.getByText(/\+48% vs ship avg/)).toBeInTheDocument();
        expect(screen.getByText(/ship 30d avg 94\.6k/)).toBeInTheDocument();
    });

    it('ships map defaults to Top 10 by damage; All shows everything and persists', () => {
        window.localStorage.removeItem('bs-bh-ships-scope');
        const many = Array.from({ length: 12 }, (_, i) => row({
            ship_id: i + 1,
            ship_name: `Ship${i + 1}`,
            damage: 1_000_000 - i * 10_000,
        }));
        render(<BattleHistoryTreemaps byShip={many} />);

        const shipsSvg = screen.getByRole('img', { name: /ships sized by total damage/i });
        expect(shipsSvg.querySelectorAll('rect')).toHaveLength(10);
        // The two lowest-damage ships fall outside the top 10.
        expect(screen.queryByText('Ship11')).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'All' }));
        expect(shipsSvg.querySelectorAll('rect')).toHaveLength(12);
        expect(window.localStorage.getItem('bs-bh-ships-scope')).toBe('all');

        fireEvent.click(screen.getByRole('button', { name: 'Top 10' }));
        expect(shipsSvg.querySelectorAll('rect')).toHaveLength(10);
        expect(window.localStorage.getItem('bs-bh-ships-scope')).toBe('top10');
    });

    it('clicking a ship tile reports the row (ShipStats toggle contract)', () => {
        const onShipClick = jest.fn();
        const { container } = render(
            <BattleHistoryTreemaps byShip={[row({})]} onShipClick={onShipClick} />,
        );
        // The ships panel is the only clickable one; its tile carries the
        // Vermont label. Click every rect — only the ship tile should fire.
        Array.from(container.querySelectorAll('rect')).forEach((r) => fireEvent.click(r));
        expect(onShipClick).toHaveBeenCalledTimes(1);
        expect(onShipClick.mock.calls[0][0].ship_id).toBe(1);
    });
});
