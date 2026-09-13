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
    beforeEach(() => {
        mockTrackEvent.mockClear();
        // Preferences persist per player/slider in localStorage; clear between
        // tests so one test's stored pick can't satisfy another's assertion.
        window.localStorage.clear();
    });

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
        // Ships-panel header: "battles ×" + the shared color-metric pill row,
        // WR% default; the type/tier titles echo the active metric.
        // 'Battles ×' since the label was wired to common.battles (2026-08-11)
        // — the source case changed, the RENDERED case did not: the container
        // carries `uppercase`, so English still paints "BATTLES ×". The CJK
        // typography rule neutralizes that class for ko/ja.
        expect(screen.getByText('Battles ×')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'WR%' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: 'dmg' })).toHaveAttribute('aria-pressed', 'false');
        expect(screen.getByRole('button', { name: 'Kills' })).toHaveAttribute('aria-pressed', 'false');
        expect(screen.getByText('Type × WR%')).toBeInTheDocument();
        expect(screen.getByText('Tier × WR%')).toBeInTheDocument();
        // Type panel aggregates to short class labels; ships panel draws one
        // tile per ship; tier panel groups both T10 ships into one tile.
        expect(screen.getByText('BB')).toBeInTheDocument();
        expect(screen.getByText('DD')).toBeInTheDocument();
        expect(screen.getByText('Vermont')).toBeInTheDocument();
        expect(screen.getByText('Shimakaze')).toBeInTheDocument();
        expect(screen.getByText('T10')).toBeInTheDocument();
    });

    it('colors damage tiles by the player-vs-population ratio and falls back to neutral without a baseline', () => {
        render(
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
        // WR% is the default metric — the diverging damage fills need dmg.
        fireEvent.click(screen.getByRole('button', { name: 'dmg' }));
        const shipsSvg = screen.getByRole('img', { name: /ships sized by battles/i });
        const fills = Array.from(shipsSvg.querySelectorAll('rect'))
            .map((r) => r.getAttribute('fill'));
        // The neutral no-baseline fill is present verbatim; the diverging fills
        // are computed rgb() strings, so just assert we did NOT paint all three
        // ship tiles the same color.
        expect(fills).toContain('#6f7683');
        expect(new Set(fills).size).toBeGreaterThan(1);
    });

    it('avg damage (not WR) is the damage tile sub-label, and tooltips carry the vs-average detail on hover', () => {
        render(<BattleHistoryTreemaps byShip={[row({})]} />);
        // WR% is the default metric — switch to dmg for this test.
        fireEvent.click(screen.getByRole('button', { name: 'dmg' }));
        // 140_000 → "140k" at 3 significant digits — the sub line is the value
        // alone (no battle counts on tiles). In dmg mode all three panels
        // carry an avg-dmg sub-line (single-ship roster → same value on each).
        expect(screen.getAllByText('140k').length).toBeGreaterThanOrEqual(1);
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
        // "140k" appears on each panel's sub-label (ships/type/tier, single
        // ship) plus the tooltip value.
        expect(screen.getAllByText('140k')).toHaveLength(4);
        expect(screen.getByText('avg dmg')).toBeInTheDocument();
        expect(screen.getByText('battles')).toBeInTheDocument();
        expect(screen.getByText('WR')).toBeInTheDocument();
        expect(screen.queryByText(/30d avg/)).not.toBeInTheDocument();
        expect(screen.queryByText(/total/)).not.toBeInTheDocument();
    });

    it('color-metric pills switch the ships-map fill, sub-line, and tooltip rows', () => {
        const { container } = render(<BattleHistoryTreemaps byShip={[row({})]} />);
        const shipsSvg = screen.getByRole('img', { name: /ships sized by battles/i });

        // WR%: sub-line becomes the WR, tile fill becomes the shared wrColor
        // band (60% → '#D042F3'), and the tooltip WR value is tinted.
        fireEvent.click(screen.getByRole('button', { name: 'WR%' }));
        expect(screen.getByRole('button', { name: 'WR%' })).toHaveAttribute('aria-pressed', 'true');
        // Each pill click emits one Umami event carrying the chosen metric.
        expect(mockTrackEvent).toHaveBeenCalledWith('battle-history-ships-color', { metric: 'wr' });
        expect(container.querySelector('svg rect')?.getAttribute('fill')).toBe('#D042F3');
        fireEvent.mouseMove(shipsSvg.querySelector('rect')!, { clientX: 10, clientY: 10 });
        const wrValues = screen.getAllByText('60.0%');
        expect(wrValues.some((el) => el.getAttribute('style')?.includes('color'))).toBe(true);

        // Kills: fixed-band fill (12 frags / 10 battles = 1.20 → '#3182bd'),
        // and the tooltip gains a colored kills row.
        fireEvent.click(screen.getByRole('button', { name: 'Kills' }));
        expect(container.querySelector('svg rect')?.getAttribute('fill')).toBe('#3182bd');
        fireEvent.mouseMove(shipsSvg.querySelector('rect')!, { clientX: 10, clientY: 10 });
        expect(screen.getByText('kills / battle')).toBeInTheDocument();
        // "1.20" appears twice: the tile sub-line and the tooltip value.
        expect(screen.getAllByText('1.20').length).toBeGreaterThanOrEqual(1);
    });

    it('remembers the color metric per player and restores it on a later visit', () => {
        const scope = 'na:vermontfan:random';
        window.localStorage.removeItem(`bs:bh-ships-color:${scope}`);
        const { unmount } = render(<BattleHistoryTreemaps byShip={[row({})]} prefScope={scope} />);

        // Default until the visitor picks something.
        expect(screen.getByRole('button', { name: 'WR%' })).toHaveAttribute('aria-pressed', 'true');
        fireEvent.click(screen.getByRole('button', { name: 'Kills' }));
        expect(window.localStorage.getItem(`bs:bh-ships-color:${scope}`)).toBe('kills');
        unmount();

        // Same player again: the stored pick is restored, not the default.
        render(<BattleHistoryTreemaps byShip={[row({})]} prefScope={scope} />);
        expect(screen.getByRole('button', { name: 'Kills' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: 'WR%' })).toHaveAttribute('aria-pressed', 'false');
    });

    it('scopes the stored pick per player: another player opens on the default', () => {
        window.localStorage.setItem('bs:bh-ships-color:na:vermontfan:random', 'dmg');
        window.localStorage.removeItem('bs:bh-ships-color:na:someoneelse:random');
        const { rerender } = render(
            <BattleHistoryTreemaps byShip={[row({})]} prefScope="na:vermontfan:random" />,
        );
        expect(screen.getByRole('button', { name: 'dmg' })).toHaveAttribute('aria-pressed', 'true');

        // A soft-nav to a player with no stored pick resets rather than
        // inheriting the previous player's metric.
        rerender(<BattleHistoryTreemaps byShip={[row({})]} prefScope="na:someoneelse:random" />);
        expect(screen.getByRole('button', { name: 'WR%' })).toHaveAttribute('aria-pressed', 'true');
        // ...and the realm is part of the key, so a same-name account elsewhere
        // is a distinct pick.
        rerender(<BattleHistoryTreemaps byShip={[row({})]} prefScope="eu:vermontfan:random" />);
        expect(screen.getByRole('button', { name: 'WR%' })).toHaveAttribute('aria-pressed', 'true');
    });

    it('without a prefScope the pick is session-only (nothing stored)', () => {
        render(<BattleHistoryTreemaps byShip={[row({})]} />);
        fireEvent.click(screen.getByRole('button', { name: 'Kills' }));
        // The pick still applies for this mount; it just leaves no trace.
        expect(screen.getByRole('button', { name: 'Kills' })).toHaveAttribute('aria-pressed', 'true');
        expect(window.localStorage.length).toBe(0);
        // An empty-string scope takes the same session-only path as an omitted
        // one, so a half-built key can never be written.
        const { unmount } = render(<BattleHistoryTreemaps byShip={[row({})]} prefScope="" />);
        fireEvent.click(screen.getAllByRole('button', { name: 'dmg' })[1]);
        expect(window.localStorage.length).toBe(0);
        unmount();
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

        // Default is min(15, roster) — 12 ships < 15 → all shown.
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

    it('large roster defaults to Top 15 on every load; a stored slider value is ignored', () => {
        const many = Array.from({ length: 30 }, (_, i) => row({
            ship_id: i + 1,
            ship_name: `Ship${i + 1}`,
            battles: 100 - i,
        }));
        const { unmount } = render(<BattleHistoryTreemaps byShip={many} />);
        expect(
            screen.getByRole('img', { name: /ships sized by battles/i }).querySelectorAll('rect'),
        ).toHaveLength(15);
        unmount();

        // A stale value from an older build must NOT be adopted — the slider is no
        // longer persisted, so every fresh mount resets to the default 15.
        window.localStorage.setItem('bs-bh-ships-slider', '8');
        render(<BattleHistoryTreemaps byShip={many} />);
        expect(
            screen.getByRole('img', { name: /ships sized by battles/i }).querySelectorAll('rect'),
        ).toHaveLength(15);
    });

    it('draws the games record on all three maps, summed from wins/losses, under every color metric', () => {
        render(
            <BattleHistoryTreemaps
                byShip={[
                    // 9 battles, 4W 5L — the Brennus case the line was added for.
                    row({ ship_name: 'Brennus', ship_type: 'Cruiser', battles: 9, wins: 4, losses: 5, win_rate: 44.4 }),
                    row({ ship_id: 2, ship_name: 'Shimakaze', ship_type: 'Destroyer', battles: 20, wins: 11, losses: 9, win_rate: 55 }),
                ]}
            />,
        );
        const shipsSvg = screen.getByRole('img', { name: /ships sized by battles/i });
        const typeSvg = screen.getByRole('img', { name: /battles by ship type/i });
        const tierSvg = screen.getByRole('img', { name: /battles by ship tier/i });
        const textOf = (svg: HTMLElement) => Array.from(svg.querySelectorAll('text')).map((t) => t.textContent);

        expect(textOf(shipsSvg)).toEqual(expect.arrayContaining(['4W 5L', '11W 9L']));
        // Type tiles carry their own group's record, not a ship's.
        expect(textOf(typeSvg)).toEqual(expect.arrayContaining(['4W 5L', '11W 9L']));
        // Both ships are T10, so the tier tile sums them: 15W 14L.
        expect(textOf(tierSvg)).toEqual(expect.arrayContaining(['15W 14L']));

        // The record is a property of the games played, so it survives a
        // color-metric switch that replaces the WR% line above it.
        fireEvent.click(screen.getByRole('button', { name: 'dmg' }));
        expect(textOf(shipsSvg)).toEqual(expect.arrayContaining(['4W 5L']));
        expect(textOf(shipsSvg)).not.toEqual(expect.arrayContaining(['44.4%']));
    });

    it('derives losses from the losses field, so a draw is neither a win nor a loss', () => {
        // 10 battles, 4 wins, 5 losses: one draw. battles − wins would say 6L.
        render(<BattleHistoryTreemaps byShip={[row({ battles: 10, wins: 4, losses: 5, win_rate: 40 })]} />);
        const shipsSvg = screen.getByRole('img', { name: /ships sized by battles/i });
        const texts = Array.from(shipsSvg.querySelectorAll('text')).map((t) => t.textContent);
        expect(texts).toContain('4W 5L');
        expect(texts).not.toContain('4W 6L');
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
