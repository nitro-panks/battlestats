import { test, expect } from '@playwright/test';
import { APP_ORIGIN } from './liveBenchmarkSupport';

test.use({
    baseURL: APP_ORIGIN,
    viewport: { width: 393, height: 852 },
    isMobile: true,
    hasTouch: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
});

const VIEWPORT_WIDTH = 393;
const OVERFLOW_TOLERANCE = 5;

test.describe('Mobile chart overflow', () => {
    test('player detail: profile tab has no horizontal overflow', async ({ page }) => {
        await page.goto('/player/lil_boots', { waitUntil: 'networkidle' });

        // Click Profile tab
        const profileTab = page.locator('button[role="tab"]:text("Profile")');
        await profileTab.waitFor({ state: 'visible', timeout: 15000 });
        await profileTab.click();
        await page.waitForTimeout(3000);

        const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
        console.log(`Profile tab body scroll width: ${bodyWidth}px`);
        expect(bodyWidth).toBeLessThanOrEqual(VIEWPORT_WIDTH + OVERFLOW_TOLERANCE);

        // Check all SVGs fit within container
        const svgWidths = await page.evaluate(() =>
            Array.from(document.querySelectorAll('svg')).map((svg) => svg.getBoundingClientRect().width)
        );
        console.log(`Profile tab SVG widths: ${JSON.stringify(svgWidths)}`);
        for (const w of svgWidths) {
            expect(w).toBeLessThanOrEqual(VIEWPORT_WIDTH + OVERFLOW_TOLERANCE);
        }
    });

    test('player detail: population tab has no horizontal overflow', async ({ page }) => {
        await page.goto('/player/lil_boots', { waitUntil: 'networkidle' });

        const populationTab = page.locator('button[role="tab"]:text("Population")');
        await populationTab.waitFor({ state: 'visible', timeout: 15000 });
        await populationTab.click();
        await page.waitForTimeout(3000);

        const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
        console.log(`Population tab body scroll width: ${bodyWidth}px`);
        expect(bodyWidth).toBeLessThanOrEqual(VIEWPORT_WIDTH + OVERFLOW_TOLERANCE);

        const svgWidths = await page.evaluate(() =>
            Array.from(document.querySelectorAll('svg')).map((svg) => svg.getBoundingClientRect().width)
        );
        console.log(`Population tab SVG widths: ${JSON.stringify(svgWidths)}`);
        for (const w of svgWidths) {
            expect(w).toBeLessThanOrEqual(VIEWPORT_WIDTH + OVERFLOW_TOLERANCE);
        }
    });

    test('player detail: ships tab has no horizontal overflow', async ({ page }) => {
        await page.goto('/player/lil_boots', { waitUntil: 'networkidle' });

        const shipsTab = page.locator('button[role="tab"]:text("Ships")');
        await shipsTab.waitFor({ state: 'visible', timeout: 15000 });
        await shipsTab.click();
        await page.waitForTimeout(3000);

        const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
        console.log(`Ships tab body scroll width: ${bodyWidth}px`);
        expect(bodyWidth).toBeLessThanOrEqual(VIEWPORT_WIDTH + OVERFLOW_TOLERANCE);

        const svgWidths = await page.evaluate(() =>
            Array.from(document.querySelectorAll('svg')).map((svg) => svg.getBoundingClientRect().width)
        );
        console.log(`Ships tab SVG widths: ${JSON.stringify(svgWidths)}`);
        for (const w of svgWidths) {
            expect(w).toBeLessThanOrEqual(VIEWPORT_WIDTH + OVERFLOW_TOLERANCE);
        }
    });

    test('player detail: ranked tab has no horizontal overflow', async ({ page }) => {
        await page.goto('/player/lil_boots', { waitUntil: 'networkidle' });

        const rankedTab = page.locator('button[role="tab"]:text("Ranked")');
        const hasRanked = await rankedTab.waitFor({ state: 'visible', timeout: 15000 }).then(() => true).catch(() => false);
        if (!hasRanked) {
            console.log('Ranked tab not visible — player may not have ranked data, skipping');
            return;
        }
        await rankedTab.click();
        await page.waitForTimeout(3000);

        const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
        console.log(`Ranked tab body scroll width: ${bodyWidth}px`);
        expect(bodyWidth).toBeLessThanOrEqual(VIEWPORT_WIDTH + OVERFLOW_TOLERANCE);

        const svgWidths = await page.evaluate(() =>
            Array.from(document.querySelectorAll('svg')).map((svg) => svg.getBoundingClientRect().width)
        );
        console.log(`Ranked tab SVG widths: ${JSON.stringify(svgWidths)}`);
        for (const w of svgWidths) {
            expect(w).toBeLessThanOrEqual(VIEWPORT_WIDTH + OVERFLOW_TOLERANCE);
        }
    });

    test('clan detail: chart has no horizontal overflow', async ({ page }) => {
        // Use a known clan page
        await page.goto('/clan/1000060069-friday-night-fights', { waitUntil: 'networkidle' });
        await page.waitForTimeout(3000);

        const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
        console.log(`Clan page body scroll width: ${bodyWidth}px`);
        expect(bodyWidth).toBeLessThanOrEqual(VIEWPORT_WIDTH + OVERFLOW_TOLERANCE);

        const svgWidths = await page.evaluate(() =>
            Array.from(document.querySelectorAll('svg')).map((svg) => svg.getBoundingClientRect().width)
        );
        console.log(`Clan page SVG widths: ${JSON.stringify(svgWidths)}`);
        for (const w of svgWidths) {
            expect(w).toBeLessThanOrEqual(VIEWPORT_WIDTH + OVERFLOW_TOLERANCE);
        }
    });
});
