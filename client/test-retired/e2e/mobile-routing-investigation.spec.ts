import { test, expect } from '@playwright/test';
import { APP_ORIGIN } from './liveBenchmarkSupport';

// Use iPhone viewport dimensions with Chromium (WebKit not installed locally)
test.use({
    baseURL: APP_ORIGIN,
    viewport: { width: 393, height: 852 },
    isMobile: true,
    hasTouch: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
});

test.describe('Mobile routing investigation', () => {
    test('landing page: tapping a player name button routes to /player/ not /clan/', async ({ page }) => {
        await page.goto('/', { waitUntil: 'networkidle' });

        const playerButton = page.locator('button[aria-label^="Show player "]').first();
        await playerButton.waitFor({ state: 'visible', timeout: 15000 });

        const playerLabel = await playerButton.getAttribute('aria-label');
        const playerName = playerLabel?.replace('Show player ', '') ?? '';
        expect(playerName.length).toBeGreaterThan(0);

        console.log(`Tapping player button: "${playerName}"`);
        await playerButton.tap();
        await page.waitForURL(/\/(player|clan)\//, { timeout: 10000 });

        const url = page.url();
        console.log(`Navigated to: ${url}`);
        expect(url).toContain('/player/');
        expect(url).not.toContain('/clan/');
    });

    test('landing page: tapping a clan tag button routes to /clan/', async ({ page }) => {
        await page.goto('/', { waitUntil: 'networkidle' });

        const clanButton = page.locator('button[aria-label^="Show clan "]').first();
        await clanButton.waitFor({ state: 'visible', timeout: 15000 });

        const clanLabel = await clanButton.getAttribute('aria-label');
        console.log(`Tapping clan button: "${clanLabel}"`);

        await clanButton.tap();
        await page.waitForURL(/\/(player|clan)\//, { timeout: 10000 });

        const url = page.url();
        console.log(`Navigated to: ${url}`);
        expect(url).toContain('/clan/');
    });

    test('landing page: D3 SVG circles handle touch events', async ({ page }) => {
        await page.goto('/', { waitUntil: 'networkidle' });

        // Wait for both charts to render
        await page.waitForTimeout(2000);

        // Count all SVG circles
        const allCircles = page.locator('svg circle[style*="cursor: pointer"]');
        const circleCount = await allCircles.count();
        console.log(`Total clickable SVG circles: ${circleCount}`);

        // Try tapping a circle using dispatchEvent to bypass Playwright's pointer interception check
        const clanChartCircle = page.locator('svg circle[style*="cursor: pointer"]').first();
        const box = await clanChartCircle.boundingBox();
        console.log(`First circle bounds: ${JSON.stringify(box)}`);

        if (box) {
            const centerX = box.x + box.width / 2;
            const centerY = box.y + box.height / 2;

            // Simulate real touch sequence
            await page.touchscreen.tap(centerX, centerY);

            // Wait briefly for any navigation
            const navigated = await page.waitForURL(/\/(player|clan)\//, { timeout: 5000 }).then(() => true).catch(() => false);
            if (navigated) {
                console.log(`Circle tap navigated to: ${page.url()}`);
            } else {
                console.log('Circle tap did NOT trigger navigation - D3 click handler not firing on touch');
            }
        }
    });

    test('landing page: layout overlap investigation on mobile viewport', async ({ page }) => {
        test.setTimeout(60000);
        await page.goto('/', { waitUntil: 'networkidle' });

        const clanButtons = page.locator('button[aria-label^="Show clan "]');
        const playerButtons = page.locator('button[aria-label^="Show player "]');

        await clanButtons.first().waitFor({ state: 'visible', timeout: 15000 });
        await playerButtons.first().waitFor({ state: 'visible', timeout: 15000 });

        // Measure section separation
        const allClanBoxes = await clanButtons.all();
        const allPlayerBoxes = await playerButtons.all();

        let clanMaxY = 0;
        for (const btn of allClanBoxes) {
            const box = await btn.boundingBox();
            if (box) clanMaxY = Math.max(clanMaxY, box.y + box.height);
        }

        let playerMinY = Infinity;
        for (const btn of allPlayerBoxes) {
            const box = await btn.boundingBox();
            if (box) playerMinY = Math.min(playerMinY, box.y);
        }

        console.log(`Clan buttons max bottom: ${clanMaxY}px`);
        console.log(`Player buttons min top: ${playerMinY}px`);
        console.log(`Section gap: ${playerMinY - clanMaxY}px`);

        // Check page width overflow
        const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
        const viewportWidth = 393;
        console.log(`Body scroll width: ${bodyWidth}px, viewport: ${viewportWidth}px`);
        if (bodyWidth > viewportWidth) {
            console.log(`WARNING: Horizontal overflow of ${bodyWidth - viewportWidth}px`);
        }

        // Player detail layout checks moved to dedicated tests:
        // - 'responsive layout on mobile viewport' (test 7)
        // - mobile-chart-overflow.spec.ts
    });

    test('player detail page: tapping clan member circle routes to /player/', async ({ page }) => {
        await page.goto('/player/lil_boots', { waitUntil: 'networkidle' });

        const memberCircle = page.locator('#clan_plot_container circle[style*="cursor: pointer"]').first();
        const hasMemberCircle = await memberCircle.waitFor({ state: 'visible', timeout: 20000 }).then(() => true).catch(() => false);

        if (hasMemberCircle) {
            const box = await memberCircle.boundingBox();
            console.log(`Clan member circle bounds: ${JSON.stringify(box)}`);

            if (box) {
                await page.touchscreen.tap(box.x + box.width / 2, box.y + box.height / 2);
                const navigated = await page.waitForURL(/\/(player|clan)\//, { timeout: 10000 }).then(() => true).catch(() => false);
                if (navigated) {
                    const url = page.url();
                    console.log(`Navigated to: ${url}`);
                    console.log(`Route type: ${url.includes('/player/') ? 'PLAYER (correct)' : 'CLAN (BUG)'}`);
                    expect(url).toContain('/player/');
                } else {
                    console.log('Clan member circle tap did NOT trigger navigation');
                }
            }
        } else {
            console.log('No clan member circles found on player detail page');
        }
    });

    test('player detail page: clan name link routes to /clan/', async ({ page }) => {
        await page.goto('/player/lil_boots', { waitUntil: 'networkidle' });

        const clanLink = page.locator('a[aria-label^="Open clan page"]').first();
        const hasClan = await clanLink.waitFor({ state: 'visible', timeout: 15000 }).then(() => true).catch(() => false);

        if (hasClan) {
            const label = await clanLink.getAttribute('aria-label');
            const href = await clanLink.getAttribute('href');
            console.log(`Clan name link: "${label}", href="${href}"`);

            // Verify the link is an <a> with a proper /clan/ href (the core fix for Bug 1)
            expect(href).toContain('/clan/');

            // Click (not tap) to trigger Next.js client-side navigation in Chromium emulation
            await clanLink.click();
            await page.waitForURL(/\/clan\//, { timeout: 10000 });

            const url = page.url();
            console.log(`Navigated to: ${url}`);
            expect(url).toContain('/clan/');
        } else {
            console.log('No clan link found on player detail page');
        }
    });

    test('player detail page: responsive layout on mobile viewport', async ({ page }) => {
        await page.goto('/player/lil_boots', { waitUntil: 'networkidle' });

        // The grid should be single-column on mobile (no fixed 350px column)
        const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
        const viewportWidth = 393;
        console.log(`Player detail body scroll width: ${bodyWidth}px`);

        // No horizontal overflow on mobile
        expect(bodyWidth).toBeLessThanOrEqual(viewportWidth + 5);
    });

    test('landing page: chart labels appear above charts', async ({ page }) => {
        await page.goto('/', { waitUntil: 'networkidle' });

        const clanLabel = page.locator('h3:text("Active Clans")').first();
        const playerLabel = page.locator('h3:text("Active Players")').first();

        await clanLabel.waitFor({ state: 'visible', timeout: 15000 });
        await playerLabel.waitFor({ state: 'visible', timeout: 15000 });

        // Clan SVG circles should appear AFTER the clan label
        const clanLabelBox = await clanLabel.boundingBox();
        const clanCircle = page.locator('svg .data-circle').first();
        const hasClanCircle = await clanCircle.waitFor({ state: 'visible', timeout: 5000 }).then(() => true).catch(() => false);
        if (hasClanCircle && clanLabelBox) {
            const circleBox = await clanCircle.boundingBox();
            if (circleBox) {
                console.log(`Clan label Y: ${clanLabelBox.y}, first clan circle Y: ${circleBox.y}`);
                expect(circleBox.y).toBeGreaterThan(clanLabelBox.y);
            }
        }
    });
});
