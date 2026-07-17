import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import BattleHistoryTreemaps from '../BattleHistoryTreemaps';
import type { BattleHistoryByShip } from '../BattleHistoryCard';

const mockTrackEvent = jest.fn();
jest.mock('../../lib/umami', () => ({
    trackEvent: (...args: unknown[]) => mockTrackEvent(...args),
}));

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
    lifetime_battles: 510,
    lifetime_win_rate: 50,
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
        expect(screen.getByText('battles × dmg')).toBeInTheDocument();
        expect(screen.getByText('Type × WR')).toBeInTheDocument();
        expect(screen.getByText('Tier × WR')).toBeInTheDocument();
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
        // 140_000 → "140k" at 3 significant digits — the sub line is the value
        // alone (no battle counts on tiles).
        expect(screen.getByText('140k')).toBeInTheDocument();
        // Hover specifically a SHIPS-panel tile (the type/tier panels have
        // their own rects with WR tooltips).
        const shipsSvg = screen.getByRole('img', { name: /ships sized by battles/i });
        const shipRect = shipsSvg.querySelector('rect');
        fireEvent.mouseMove(shipRect!, { clientX: 10, clientY: 10 });
        // The tooltip is value/label pairs on a shared grid: avg dmg, the
        // colored "+48%" delta, then battles and WR each on their own row;
        // the old "ship 30d avg … total" line is gone.
        const delta = screen.getByText('+48%');
        expect(delta).toBeInTheDocument();
        // Tinted by the same diverging scale as the tile (interpolated, so
        // assert presence of an inline color rather than an exact endpoint).
        expect(delta.getAttribute('style')).toMatch(/color/);
        expect(screen.getByText('vs avg')).toBeInTheDocument();
        // "140k" now appears twice: the tile sub-label and the tooltip value.
        expect(screen.getAllByText('140k')).toHaveLength(2);
        expect(screen.getByText('avg dmg')).toBeInTheDocument();
        expect(screen.getByText('battles')).toBeInTheDocument();
        expect(screen.getByText('WR')).toBeInTheDocument();
        expect(screen.queryByText(/30d avg/)).not.toBeInTheDocument();
        expect(screen.queryByText(/total/)).not.toBeInTheDocument();
    });

    it('small roster shows everything by default; the slider zooms but does not persist', () => {
        window.localStorage.removeItem('bs-bh-ships-slider');
        const many = Array.from({ length: 12 }, (_, i) => row({
            ship_id: i + 1,
            ship_name: `Ship${i + 1}`,
            battles: 100 - i * 5,
            damage: 1_000_000 - i * 10_000,
            avg_damage: 100_000 - i * 1_000,
        }));
        render(<BattleHistoryTreemaps byShip={many} />);

        // Default is min(25, roster) — 12 ships < 25 → all shown.
        const shipsSvg = screen.getByRole('img', { name: /ships sized by battles/i });
        expect(shipsSvg.querySelectorAll('rect')).toHaveLength(12);
        const slider = screen.getByRole('slider', { name: /most-played ships shown/i });
        expect(slider).toHaveAttribute('max', '12');
        // Legend is bare numbers: the fixed lower bound and the current N.
        expect(screen.getByText('1')).toBeInTheDocument();
        expect(screen.getByText('12')).toBeInTheDocument();

        fireEvent.change(slider, { target: { value: '5' } });
        expect(shipsSvg.querySelectorAll('rect')).toHaveLength(5);
        // Ships past the play-volume cutoff fall outside the top 5.
        expect(screen.queryByText('Ship6')).not.toBeInTheDocument();
        expect(screen.getByText('5')).toBeInTheDocument();
        // The choice is NOT persisted — no browser storage is written.
        expect(window.localStorage.getItem('bs-bh-ships-slider')).toBeNull();
        // Analytics fire once, on release — not on every drag tick.
        expect(mockTrackEvent).not.toHaveBeenCalled();
        fireEvent.pointerUp(slider);
        expect(mockTrackEvent).toHaveBeenCalledWith('battle-history-ships-scope', { scope: 'slider', count: 5 });

        // Keyboard-driven changes track too (no pointer event fires for them),
        // but only for value-changing keys — Tabbing away emits nothing.
        mockTrackEvent.mockClear();
        fireEvent.change(slider, { target: { value: '4' } });
        fireEvent.keyUp(slider, { key: 'ArrowLeft' });
        expect(mockTrackEvent).toHaveBeenCalledWith('battle-history-ships-scope', { scope: 'slider', count: 4 });
        mockTrackEvent.mockClear();
        fireEvent.keyUp(slider, { key: 'Tab' });
        expect(mockTrackEvent).not.toHaveBeenCalled();

        // Dragging back to max shows everything again.
        fireEvent.change(slider, { target: { value: '12' } });
        expect(shipsSvg.querySelectorAll('rect')).toHaveLength(12);
        expect(screen.getByText('12')).toBeInTheDocument();
    });

    it('large roster defaults to Top 25 on every load; a stored slider value is ignored', () => {
        const many = Array.from({ length: 30 }, (_, i) => row({
            ship_id: i + 1,
            ship_name: `Ship${i + 1}`,
            battles: 100 - i,
        }));
        const { unmount } = render(<BattleHistoryTreemaps byShip={many} />);
        expect(
            screen.getByRole('img', { name: /ships sized by battles/i }).querySelectorAll('rect'),
        ).toHaveLength(25);
        unmount();

        // A stale value from an older build must NOT be adopted — the slider is no
        // longer persisted, so every fresh mount resets to the default 25.
        window.localStorage.setItem('bs-bh-ships-slider', '8');
        render(<BattleHistoryTreemaps byShip={many} />);
        expect(
            screen.getByRole('img', { name: /ships sized by battles/i }).querySelectorAll('rect'),
        ).toHaveLength(25);
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
