import {
    alignedChartRightMargin,
    barChartDataRightX,
    resolveChartWidth,
    resolveContainerChartWidth,
} from '../chartTheme';

describe('resolveChartWidth', () => {
    it('caps at svgWidth when the container is wider', () => {
        expect(resolveChartWidth(830, 600)).toBe(600);
    });

    it('tracks the container when narrower than svgWidth', () => {
        expect(resolveChartWidth(400, 600)).toBe(400);
    });

    it('falls back to svgWidth before layout', () => {
        expect(resolveChartWidth(0, 600)).toBe(600);
        expect(resolveChartWidth(null, 600)).toBe(600);
        expect(resolveChartWidth(undefined, 600)).toBe(600);
    });

    it('enforces the minimum width floor', () => {
        expect(resolveChartWidth(100, 600)).toBe(280);
    });
});

describe('resolveContainerChartWidth', () => {
    it('tracks the container past the fallback width (no cap)', () => {
        expect(resolveContainerChartWidth(830, 600)).toBe(830);
    });

    it('tracks the container when narrower than the fallback', () => {
        expect(resolveContainerChartWidth(400, 600)).toBe(400);
    });

    it('falls back to fallbackWidth before layout', () => {
        expect(resolveContainerChartWidth(0, 600)).toBe(600);
        expect(resolveContainerChartWidth(null, 600)).toBe(600);
        expect(resolveContainerChartWidth(undefined, 600)).toBe(600);
    });

    it('enforces the minimum width floor', () => {
        expect(resolveContainerChartWidth(100, 600)).toBe(280);
    });
});

describe('barChartDataRightX', () => {
    it('matches shipBarPlot data edge at full-width panel sizes', () => {
        // 68 + (svgWidth - 68 - 96) / 1.08 — the 8% x-scale headroom means the
        // longest bar always ends here.
        expect(barChartDataRightX(788)).toBeCloseTo(645.78, 1);
        expect(barChartDataRightX(586)).toBeCloseTo(458.7, 1);
    });

    it('uses shipBarPlot compact margins below its 420px threshold', () => {
        expect(barChartDataRightX(380)).toBeCloseTo(62 + (380 - 62 - 14) / 1.08, 5);
    });

    it('uses full margins between 420 and 480 (population compact, bars not)', () => {
        expect(barChartDataRightX(440)).toBeCloseTo(68 + (440 - 68 - 96) / 1.08, 5);
    });
});

describe('alignedChartRightMargin', () => {
    it('lands the plot edge on barChartDataRightX', () => {
        const svgWidth = 788;
        expect(svgWidth - alignedChartRightMargin(svgWidth, 18)).toBeCloseTo(barChartDataRightX(svgWidth), 5);
    });

    it('never shrinks below the annotation floor', () => {
        expect(alignedChartRightMargin(600, 200)).toBe(200);
    });
});
