import { expect, test } from '@playwright/test';

const PROD_URL = "https://battlestats.online";

// Pre-warmed clans with cached tier distribution data
const WARM_CLAN_IDS = [1000071346, 1000064482, 1000044123];

test.describe('Clan Page Loading Precedence', () => {
    test('verifies data loads in correct priority order: chart → tiers → battles → members', async ({ page }) => {
        test.setTimeout(60000);
        const clanId = WARM_CLAN_IDS[0];

        const requestOrder: string[] = [];

        // Intercept network requests to track ordering
        page.on('request', (req) => {
            const url = req.url();
            if (url.includes(`/api/fetch/clan_data/${clanId}`)) {
                requestOrder.push('clan_chart');
            } else if (url.includes(`/api/fetch/clan_tiers/${clanId}`)) {
                requestOrder.push('clan_tiers');
            } else if (url.includes(`/api/fetch/clan_battle_seasons/${clanId}`)) {
                requestOrder.push('clan_battles');
            } else if (url.includes(`/api/fetch/clan_members/${clanId}`)) {
                requestOrder.push('clan_members');
            }
        });

        await page.goto(`${PROD_URL}/clan/${clanId}`, { waitUntil: 'networkidle' });

        // Wait for tier bars to confirm charts loaded
        await expect(page.locator('rect.tier-bar').first()).toBeVisible({ timeout: 15000 });

        console.log('Request order:', requestOrder.join(' → '));

        // Verify clan chart fires first
        const chartIndex = requestOrder.indexOf('clan_chart');
        const tiersIndex = requestOrder.indexOf('clan_tiers');
        const membersIndex = requestOrder.indexOf('clan_members');

        expect(chartIndex).toBeGreaterThanOrEqual(0);
        expect(tiersIndex).toBeGreaterThanOrEqual(0);

        // Chart should fire before tiers
        expect(chartIndex).toBeLessThan(tiersIndex);

        // Members should fire after chart (gated by chartFetchesInFlight)
        if (membersIndex >= 0) {
            expect(chartIndex).toBeLessThan(membersIndex);
        }

        console.log(`  ✓ Chart (idx ${chartIndex}) → Tiers (idx ${tiersIndex}) → Members (idx ${membersIndex})`);
    });

    test('tier distribution bars render on warm clans', async ({ page }) => {
        test.setTimeout(60000);

        for (const clanId of WARM_CLAN_IDS) {
            await test.step(`Validate clan ${clanId}`, async () => {
                await page.goto(`${PROD_URL}/clan/${clanId}`, { waitUntil: 'networkidle' });
                await expect(page.locator('rect.tier-bar').first()).toBeVisible({ timeout: 15000 });
                const barCount = await page.locator('rect.tier-bar').count();
                expect(barCount).toBe(11);
                console.log(`  ✓ Clan ${clanId}: ${barCount} tier bars`);
            });
        }
    });
});
