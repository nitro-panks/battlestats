import { expect, test } from '@playwright/test';

const clanPayload = {
    clan_id: 1000094715,
    name: 'Hermanos de Reclutamiento Panamericano',
    tag: 'HRP',
    members_count: 2,
};

test('clan route keeps the chart in loading state while plot data is pending', async ({ page }) => {
    let clanPlotRequests = 0;

    await page.route('**/api/**', async (route) => {
        const requestUrl = route.request().url();

        if (requestUrl.includes('/api/clan/1000094715')) {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify(clanPayload),
            });
            return;
        }

        if (requestUrl.includes('/api/fetch/clan_data/1000094715:active')) {
            clanPlotRequests += 1;

            if (clanPlotRequests === 1) {
                await route.fulfill({
                    status: 200,
                    contentType: 'application/json',
                    headers: {
                        'X-Clan-Plot-Pending': 'true',
                    },
                    body: JSON.stringify([]),
                });
                return;
            }

            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify([
                    { player_name: 'DeckBoss', pvp_battles: 120, pvp_ratio: 55.2 },
                    { player_name: 'Anchor', pvp_battles: 87, pvp_ratio: 52.4 },
                ]),
            });
            return;
        }

        if (requestUrl.includes('/api/fetch/clan_members/1000094715')) {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                headers: {
                    'X-Ranked-Hydration-Queued': '0',
                    'X-Ranked-Hydration-Deferred': '0',
                    'X-Ranked-Hydration-Pending': '0',
                    'X-Efficiency-Hydration-Queued': '0',
                    'X-Efficiency-Hydration-Deferred': '0',
                    'X-Efficiency-Hydration-Pending': '0',
                },
                body: JSON.stringify([
                    { id: 1, account_id: 1, name: 'DeckBoss', days_since_last_battle: 3 },
                    { id: 2, account_id: 2, name: 'Anchor', days_since_last_battle: 9 },
                ]),
            });
            return;
        }

        if (requestUrl.includes('/api/fetch/clan_battle_seasons/1000094715')) {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify([]),
            });
            return;
        }

        if (requestUrl.includes('/api/analytics/entity-view')) {
            await route.fulfill({
                status: 204,
                body: '',
            });
            return;
        }

        if (requestUrl.includes('/api/fetch/')) {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify([]),
            });
            return;
        }

        await route.fulfill({
            status: 404,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'Unhandled test route' }),
        });
    });

    await page.goto('/clan/1000094715-hermanos-de-reclutamiento-panamericano');

    await expect(page.getByRole('heading', { name: '[HRP] Hermanos de Reclutamiento Panamericano' })).toBeVisible();
    await expect(page.getByText('Loading clan chart data...')).toBeVisible();
    await expect(page.getByText('No clan chart data available.')).toHaveCount(0);

    await page.waitForTimeout(500);
    await expect(page.getByText('Loading clan chart data...')).toBeVisible();
    await expect(page.getByText('No clan chart data available.')).toHaveCount(0);

    await expect.poll(() => clanPlotRequests).toBe(2);
    await expect(page.getByText('Loading clan chart data...')).toHaveCount(0);
    await expect(page.getByText('No clan chart data available.')).toHaveCount(0);
    await expect.poll(async () => page.locator('circle').count()).toBeGreaterThan(0);
});