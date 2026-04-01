import { expect, test } from '@playwright/test';

const clanIds = [
    1000056021, // The specific clan user had issues with
    1000064495,
    1000096605,
    1000067942,
    1000063382,
    1000058376,
    1000081780,
    1000097449,
    1000049131,
    1000055899,
    1000103217,
    1000056005,
    1000043953
];

const PROD_URL = "https://battlestats.online";

test.describe('Live Prod Clan Performance Testing', () => {
    test.describe.configure({ mode: 'parallel' });

    for (const clanId of clanIds) {
        test(`should load clan tier distribution for ${clanId} without timing out`, async ({ page }) => {
            // Goto URL
            await page.goto(`${PROD_URL}/clan/${clanId}`);
            
            // The client chart fetches data with X-Clan-Tiers-Pending header,
            // meaning it will display "Aggregating clan tier distributions..." while polling.
            // Using a higher timeout to accommodate initial cold-cache async jobs on prod.
            await expect(page.locator('rect.tier-bar').first()).toBeVisible({ timeout: 30000 });
            
            // Ensure no loading label error state string
            await expect(page.getByText('Tier data unavailable')).toHaveCount(0);
            
            // Ensure "Aggregating" message was successfully replaced by the graph
            await expect(page.getByText('Aggregating clan tier distributions...')).toHaveCount(0);

            // We expect exactly 11 tier bars (Tier I to XI) 
            await expect(page.locator('rect.tier-bar')).toHaveCount(11);
        });
    }
});
