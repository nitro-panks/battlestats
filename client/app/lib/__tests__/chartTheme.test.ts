import {
    barChartDataRightX,
    drawSvgMessage,
    resolveChartWidth,
    resolveContainerChartWidth,
} from '../chartTheme';

describe('drawSvgMessage', () => {
    it('clears the container and renders the message into a sized svg', () => {
        const container = document.createElement('div');
        container.innerHTML = '<svg><circle /></svg>';

        drawSvgMessage(container, 'No data available.', { color: '#8b949e', width: 700, height: 112 });

        const svgs = container.querySelectorAll('svg');
        expect(svgs).toHaveLength(1);
        expect(svgs[0].getAttribute('width')).toBe('700');
        expect(svgs[0].getAttribute('height')).toBe('112');
        const text = container.querySelector('text');
        expect(text?.textContent).toBe('No data available.');
        // jsdom normalizes hex inline styles to rgb().
        expect(text?.getAttribute('style')).toContain('fill: rgb(139, 148, 158)');
        expect(text?.getAttribute('style')).toContain('font-size: 12px');
    });

    it('applies default dimensions and a custom font size', () => {
        const container = document.createElement('div');

        drawSvgMessage(container, 'Loading…', { color: '#e6edf3', fontSize: '14px' });

        const svg = container.querySelector('svg');
        expect(svg?.getAttribute('width')).toBe('600');
        expect(svg?.getAttribute('height')).toBe('120');
        expect(container.querySelector('text')?.style.fontSize).toBe('14px');
    });
});

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
        // 68 + (svgWidth - 68 - 46) - 148 — bars are scaled to end a fixed 148px
        // label gutter short of the plot edge, so the longest bar ends here.
        expect(barChartDataRightX(788)).toBeCloseTo(68 + (788 - 68 - 46) - 148, 5);
        expect(barChartDataRightX(586)).toBeCloseTo(68 + (586 - 68 - 46) - 148, 5);
    });

    it('uses shipBarPlot compact margins + gutter below its 420px threshold', () => {
        expect(barChartDataRightX(380)).toBeCloseTo(62 + (380 - 62 - 14) - 100, 5);
    });

    it('uses full margins between 420 and 480 (population compact, bars not)', () => {
        expect(barChartDataRightX(440)).toBeCloseTo(68 + (440 - 68 - 46) - 148, 5);
    });
});
