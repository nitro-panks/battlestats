import { getCorrelationTileBounds, getCorrelationTrendX } from '../wrDistributionPayload';

describe('WRDistribution payload helpers', () => {
    const payload = {
        x_domain: {
            min: 35,
            max: 75,
            bin_width: 1,
        },
        y_domain: {
            min: 15,
            max: 75,
            bin_width: 1.5,
        },
    };

    it('reconstructs compact tile indexes into chart bounds', () => {
        expect(getCorrelationTileBounds(payload, {
            x_index: 23,
            y_index: 18,
            count: 1,
        })).toEqual({
            xMin: 58,
            xMax: 59,
            yMin: 42,
            yMax: 43.5,
        });
    });

    it('reconstructs trend x coordinates from the shared x domain', () => {
        expect(getCorrelationTrendX({
            x_index: 23,
            y: 42,
            count: 1,
        }, payload.x_domain)).toBe(58.5);
    });
});