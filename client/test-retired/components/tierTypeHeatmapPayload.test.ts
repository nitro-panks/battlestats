import {
    getTierTypeShipTypes,
    getTierTypeTileKey,
    getTierTypeTiers,
    resolveTierTypeTiles,
    resolveTierTypeTrend,
} from '../tierTypeHeatmapPayload';

const payload = {
    metric: 'tier_type' as const,
    label: 'Tier vs Ship Type',
    x_label: 'Ship Type',
    y_label: 'Tier',
    tracked_population: 4,
    x_labels: ['Destroyer', 'Cruiser', 'Battleship', 'Aircraft Carrier', 'Submarine'],
    y_values: [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
    tiles: [
        { x_index: 0, y_index: 1, count: 40 },
        { x_index: 2, y_index: 2, count: 18 },
    ],
    trend: [
        { x_index: 0, avg_tier: 9.5, count: 40 },
        { x_index: 2, avg_tier: 8.9, count: 18 },
    ],
    player_cells: [],
};

describe('tierTypeHeatmapPayload', () => {
    it('resolves indexed tiles and trend points back to chart labels', () => {
        expect(getTierTypeShipTypes(payload)).toEqual(payload.x_labels);
        expect(getTierTypeTiers(payload)).toEqual(payload.y_values);

        expect(resolveTierTypeTiles(payload)).toEqual([
            { x_index: 0, y_index: 1, count: 40, ship_type: 'Destroyer', ship_tier: 10 },
            { x_index: 2, y_index: 2, count: 18, ship_type: 'Battleship', ship_tier: 9 },
        ]);

        expect(resolveTierTypeTrend(payload)).toEqual([
            { x_index: 0, avg_tier: 9.5, count: 40, ship_type: 'Destroyer' },
            { x_index: 2, avg_tier: 8.9, count: 18, ship_type: 'Battleship' },
        ]);

        expect(getTierTypeTileKey('Destroyer', 10)).toBe('Destroyer:10');
    });
});