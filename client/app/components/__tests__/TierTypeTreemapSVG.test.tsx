import React from 'react';
import { fireEvent, render } from '@testing-library/react';
import TierTypeTreemapSVG, { PANEL_CAPTION_H, treemapPanelHeight } from '../TierTypeTreemapSVG';
import { dietFigureHeight } from '../tierTypeDietModel';
import type { TierTypePayload, TierTypePlayerCell } from '../playerProfileChartData';

const SHIP_TYPES = ['Destroyer', 'Cruiser', 'Battleship', 'Aircraft Carrier', 'Submarine'];
const TIERS = [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1];

const cell = (ship_type: string, ship_tier: number, pvp_battles: number, wins: number): TierTypePlayerCell => ({
    ship_type,
    ship_tier,
    pvp_battles,
    wins,
    win_ratio: pvp_battles > 0 ? wins / pvp_battles : 0,
});

const payloadWith = (cells: TierTypePlayerCell[]): TierTypePayload => ({
    metric: 'tier_type',
    label: 'Tier vs Ship Type',
    x_label: 'Ship Type',
    y_label: 'Tier',
    x_labels: SHIP_TYPES,
    y_values: TIERS,
    player_cells: cells,
});

// Three cells, two tiers (10, 8), three classes. Tier 10 = 740 battles,
// tier 8 = 60; BB 420, CA 320, DD 60.
const DIET = payloadWith([
    cell('Battleship', 10, 420, 239),
    cell('Cruiser', 10, 320, 176),
    cell('Destroyer', 8, 60, 31),
]);

const titleOf = (tile: Element): string => (tile.querySelector('title')!.textContent ?? '').split(':')[0];
const areaOf = (tile: Element): number => {
    const rect = tile.querySelector('rect')!;
    return Number(rect.getAttribute('width')) * Number(rect.getAttribute('height'));
};

describe('TierTypeTreemapSVG', () => {
    it('draws two panels: one tile per played tier, one per played class, sized by battles', () => {
        const { container } = render(<TierTypeTreemapSVG data={DIET} svgWidth={400} />);
        const tierTiles = Array.from(container.querySelectorAll('g.tile[data-axis="tier"]'));
        const classTiles = Array.from(container.querySelectorAll('g.tile[data-axis="class"]'));
        expect(tierTiles.map(titleOf).sort()).toEqual(['T10', 'T8']);
        expect(classTiles.map(titleOf).sort()).toEqual(['BB', 'CA', 'DD']);

        // Additive areas within each panel: read the rects back rather than
        // trusting the sort.
        const tierArea = new Map(tierTiles.map((tile) => [titleOf(tile), areaOf(tile)]));
        expect(tierArea.get('T10')!).toBeGreaterThan(tierArea.get('T8')!);
        const classArea = new Map(classTiles.map((tile) => [titleOf(tile), areaOf(tile)]));
        expect(classArea.get('BB')!).toBeGreaterThan(classArea.get('CA')!);
        expect(classArea.get('CA')!).toBeGreaterThan(classArea.get('DD')!);

        const svgs = container.querySelectorAll('svg');
        expect(svgs).toHaveLength(2);
        // The whole Map block is exactly the bar figure's height for this
        // payload (3 tier rows: 10, 9, 8), so toggling views moves nothing
        // below it; each panel's SVG is that less its caption line.
        const block = container.firstElementChild as HTMLElement;
        expect(block.style.height).toBe(`${dietFigureHeight(3)}px`);
        expect(svgs[0].getAttribute('height')).toBe(String(treemapPanelHeight(3)));
        expect(treemapPanelHeight(3)).toBe(dietFigureHeight(3) - PANEL_CAPTION_H);
        expect(svgs[0].getAttribute('aria-label')).toContain('2 tiers over 800 random battles');
        expect(svgs[1].getAttribute('aria-label')).toContain('3 classes over 800 random battles');
    });

    it('captions the panels Tier and Type and labels tiles with room', () => {
        const { container, getByText } = render(<TierTypeTreemapSVG data={DIET} svgWidth={400} />);
        expect(getByText('Tier')).toBeInTheDocument();
        expect(getByText('Type')).toBeInTheDocument();
        const labels = Array.from(container.querySelectorAll('g.tile text')).map((node) => node.textContent);
        expect(labels).toContain('T10');
        expect(labels).toContain('740 battles');
        // Tier 10 WR = (239 + 176) / 740 over the whole group, not a mean of cell rates.
        expect(labels).toContain('56.1% WR');
        expect(labels).toContain('BB');
        expect(labels).toContain('420 battles');
    });

    it('drills into the Ships tab: a tier tile hands back the tier, a class tile the class', () => {
        const onTierSelect = jest.fn();
        const onClassSelect = jest.fn();
        const { container } = render(
            <TierTypeTreemapSVG data={DIET} svgWidth={400} onTierSelect={onTierSelect} onClassSelect={onClassSelect} />,
        );
        const tiles = Array.from(container.querySelectorAll('g.tile'));
        fireEvent.click(tiles.find((tile) => titleOf(tile) === 'T8')!.querySelector('rect')!);
        expect(onTierSelect).toHaveBeenCalledWith(8);
        fireEvent.click(tiles.find((tile) => titleOf(tile) === 'DD')!.querySelector('rect')!);
        expect(onClassSelect).toHaveBeenCalledWith('Destroyer');
    });

    it('shows the shared two-column tooltip on hover, per axis', () => {
        const { container, getByText } = render(
            <TierTypeTreemapSVG data={DIET} svgWidth={400} onTierSelect={jest.fn()} onClassSelect={jest.fn()} />,
        );
        const tiles = Array.from(container.querySelectorAll('g.tile'));
        fireEvent.mouseMove(tiles.find((tile) => titleOf(tile) === 'T10')!.querySelector('rect')!, { clientX: 40, clientY: 40 });
        expect(getByText('Tier 10')).toBeInTheDocument();
        expect(getByText('of your battles')).toBeInTheDocument();
        expect(getByText('Click to see these ships')).toBeInTheDocument();

        fireEvent.mouseMove(tiles.find((tile) => titleOf(tile) === 'CA')!.querySelector('rect')!, { clientX: 40, clientY: 40 });
        expect(getByText('All CA')).toBeInTheDocument();
    });

    it('falls back to the not-enough-variety notice below two cells, like the bar view', () => {
        const { container } = render(<TierTypeTreemapSVG data={payloadWith([cell('Cruiser', 10, 500, 250)])} svgWidth={400} />);
        expect(container.querySelectorAll('g.tile')).toHaveLength(0);
        expect(container.querySelector('svg')!.getAttribute('aria-label')).toBe('Not enough ship variety to chart');
    });
});
