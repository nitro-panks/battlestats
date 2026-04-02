import { expect, test } from '@playwright/test';

const PROD_URL = "https://battlestats.online";

test.describe('Live Prod Clan Performance Testing', () => {
    test('should pull a dozen random clans and ensure they are performant', async ({ page, request }) => {
        test.setTimeout(120000); // Allow up to 2 minutes for cold caches

        // 1. Pull random clans from the best clans endpoint
        const response = await request.get(`${PROD_URL}/api/landing/clans/?mode=best&realm=na`);
        expect(response.ok()).toBeTruthy();

        const bestClans = await response.json();
        
        // Pick exactly 12 random clans
        const dozenClans = bestClans
            .sort(() => 0.5 - Math.random())
            .slice(0, 12)
            .map((c: any) => c.clan_id);

        expect(dozenClans.length).toBeGreaterThanOrEqual(1);

        console.log(`Testing 12 random clans: ${dozenClans.join(', ')}`);

        // 2. Iterate and ensure they are performant
        for (const clanId of dozenClans) {
            await test.step(`Test performance for clan ${clanId}`, async () => {
                await page.goto(`${PROD_URL}/clan/${clanId}`);
                
                // Using a higher timeout to accommodate initial cold-cache async jobs on prod.
                await expect(page.locator('rect.tier-bar').first()).toBeVisible({ timeout: 60000 });
                
                // Ensure no loading label error state string
                await expect(page.getByText('Tier data unavailable')).toHaveCount(0);
                
                // Ensure "Aggregating" message was successfully replaced by the graph
                await expect(page.getByText('Aggregating clan tier distributions...')).toHaveCount(0);

                // We expect exactly 11 tier bars (Tier I to XI) 
                await expect(page.locator('rect.tier-bar')).toHaveCount(11);
            });
        }
    });
});
