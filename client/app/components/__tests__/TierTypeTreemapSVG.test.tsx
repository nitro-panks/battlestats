import React from 'react';
import { fireEvent, render } from '@testing-library/react';
import TierTypeTreemapSVG, { treemapHeightForWidth } from '../TierTypeTreemapSVG';
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

const DIET = payloadWith([
    cell('Battleship', 10, 420, 239),
    cell('Cruiser', 10, 320, 176),
    cell('Destroyer', 8, 60, 31),
]);

describe('TierTypeTreemapSVG', () => {
    it('draws one tile per played tier x class cell, sized by battles', () => {
        const { container } = render(<TierTypeTreemapSVG data={DIET} svgWidth={600} />);
        const tiles = container.querySelectorAll('g.tile');
        expect(tiles).toHaveLength(3);

        // Additive areas: the 420-battle tile is the largest, the 60-battle
        // tile the smallest. Read the rects back rather than trusting the sort.
        const areaByLabel = new Map<string, number>();
        tiles.forEach((tile) => {
            const rect = tile.querySelector('rect')!;
            const title = tile.querySelector('title')!.textContent ?? '';
            areaByLabel.set(title.split(':')[0], Number(rect.getAttribute('width')) * Number(rect.getAttribute('height')));
        });
        expect(areaByLabel.get('T10 BB')!).toBeGreaterThan(areaByLabel.get('T10 CA')!);
        expect(areaByLabel.get('T10 CA')!).toBeGreaterThan(areaByLabel.get('T8 DD')!);

        const svg = container.querySelector('svg')!;
        expect(svg.getAttribute('height')).toBe(String(treemapHeightForWidth(600)));
        expect(svg.getAttribute('aria-label')).toContain('3 tier and class combinations over 800 random battles');
    });

    it('labels tiles with room: tier + class, battles, win rate', () => {
        const { container } = render(<TierTypeTreemapSVG data={DIET} svgWidth={600} />);
        const labels = Array.from(container.querySelectorAll('g.tile text')).map((node) => node.textContent);
        expect(labels).toContain('T10 BB');
        expect(labels).toContain('420 battles');
        expect(labels).toContain('56.9% WR');
    });

    it('drills into the Ships tab on tile click with the tier and class', () => {
        const onCellSelect = jest.fn();
        const { container } = render(<TierTypeTreemapSVG data={DIET} svgWidth={600} onCellSelect={onCellSelect} />);
        const rects = Array.from(container.querySelectorAll('g.tile rect'));
        const ddRect = rects.find((rect) => rect.querySelector('title')?.textContent?.startsWith('T8 DD'))!;
        fireEvent.click(ddRect);
        expect(onCellSelect).toHaveBeenCalledWith('Destroyer', 8);
    });

    it('shows the same shared tooltip lines as the bar view on hover', () => {
        const { container, getByText } = render(<TierTypeTreemapSVG data={DIET} svgWidth={600} onCellSelect={jest.fn()} />);
        const rects = Array.from(container.querySelectorAll('g.tile rect'));
        const bbRect = rects.find((rect) => rect.querySelector('title')?.textContent?.startsWith('T10 BB'))!;
        fireEvent.mouseMove(bbRect, { clientX: 40, clientY: 40 });
        expect(getByText('Tier 10 BB')).toBeInTheDocument();
        expect(getByText('of your battles')).toBeInTheDocument();
        expect(getByText('Click to see these ships')).toBeInTheDocument();
    });

    it('falls back to the not-enough-variety notice below two cells, like the bar view', () => {
        const { container } = render(<TierTypeTreemapSVG data={payloadWith([cell('Cruiser', 10, 500, 250)])} svgWidth={600} />);
        expect(container.querySelectorAll('g.tile')).toHaveLength(0);
        expect(container.querySelector('svg')!.getAttribute('aria-label')).toBe('Not enough ship variety to chart');
    });
});
