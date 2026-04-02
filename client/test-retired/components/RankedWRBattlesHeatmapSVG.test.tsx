import { getRankedHeatmapTileBounds, getRankedHeatmapTrendX } from '../rankedHeatmapPayload';

describe('RankedWRBattlesHeatmapSVG payload helpers', () => {
    const payload = {
        metric: 'ranked_wr_battles' as const,
        label: 'Ranked Games vs Win Rate',
        x_label: 'Total Ranked Games',
        y_label: 'Ranked Win Rate',
        tracked_population: 2,
        correlation: 0.4,
        x_scale: 'log' as const,
        y_scale: 'linear' as const,
        x_ticks: [50, 100],
        x_edges: [50, 59, 71, 84, 100, 119, 141],
        y_domain: {
            min: 35,
            max: 75,
            bin_width: 0.75,
        },
        tiles: [],
        trend: [],
        player_point: {
            x: 60,
            y: 56.67,
            label: 'Fixture',
        },
    };

    it('reconstructs compact tile indexes into chart bounds', () => {
        expect(getRankedHeatmapTileBounds(payload, {
            x_index: 1,
            y_index: 28,
            count: 1,
        })).toEqual({
            xMin: 59,
            xMax: 71,
            yMin: 56,
            yMax: 56.75,
        });
    });

    it('reconstructs the trend x coordinate from shared bin edges', () => {
        expect(getRankedHeatmapTrendX(payload, {
            x_index: 5,
            y: 60,
            count: 1,
        })).toBeCloseTo(Math.sqrt(119 * 141), 6);
    });
});