import { expect, test } from '@playwright/test';

const playerRoutePayload = {
    id: 1,
    name: 'Player One',
    player_id: 77,
    kill_ratio: null,
    actual_kdr: null,
    player_score: null,
    total_battles: 100,
    pvp_battles: 80,
    pvp_wins: 44,
    pvp_losses: 36,
    pvp_ratio: 55,
    pvp_survival_rate: 30,
    wins_survival_rate: null,
    creation_date: '2024-01-01',
    days_since_last_battle: 2,
    last_battle_date: '2026-03-01',
    recent_games: {},
    is_hidden: false,
    stats_updated_at: '2026-03-01T00:00:00Z',
    last_fetch: '2026-03-01T00:00:00Z',
    last_lookup: '2026-03-01T00:00:00Z',
    clan: 100,
    clan_name: 'Test Clan',
    clan_tag: 'TEST',
    clan_id: 100,
    verdict: null,
    efficiency_json: [],
    ranked_json: [],
    randoms_json: [],
};

test('player route warms tab data only after the detail payload resolves', async ({ page }) => {
    const requests: string[] = [];
    let releasePlayerRoute: (() => void) | null = null;
    const playerRouteGate = new Promise<void>((resolve) => {
        releasePlayerRoute = resolve;
    });

    await page.route('**/api/**', async (route) => {
        const requestUrl = route.request().url();
        requests.push(requestUrl);

        if (requestUrl.includes('/api/player/Player%20One') || requestUrl.includes('/api/player/Player%2520One')) {
            await playerRouteGate;
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify(playerRoutePayload),
            });
            return;
        }

        if (requestUrl.includes('/api/fetch/clan_data/100:active')) {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify([]),
            });
            return;
        }

        if (requestUrl.includes('/api/fetch/clan_members/100')) {
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

    await page.goto('/player/Player%20One');

    await expect(page.getByText('Loading player profile...')).toBeVisible();
    await page.waitForTimeout(300);

    const warmupRequestsBeforeLoad = requests.filter((url) => (
        url.includes('/api/fetch/randoms_data/')
        || url.includes('/api/fetch/player_correlation/ranked_wr_battles/')
        || url.includes('/api/fetch/ranked_data/')
        || url.includes('/api/fetch/player_correlation/tier_type/')
        || url.includes('/api/fetch/type_data/')
        || url.includes('/api/fetch/tier_data/')
        || url.includes('/api/fetch/player_clan_battle_seasons/')
    ));
    expect(warmupRequestsBeforeLoad).toHaveLength(0);

    releasePlayerRoute?.();

    await expect(page.getByRole('heading', { name: 'Player One' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Profile' })).toHaveAttribute('aria-selected', 'true');

    await page.waitForFunction(() => window.location.pathname === '/player/Player%20One' || window.location.pathname === '/player/Player One');
    await page.waitForTimeout(1800);

    expect(requests.some((url) => url.includes('/api/fetch/randoms_data/77?all=true'))).toBeFalsy();
    expect(requests.some((url) => url.includes('/api/fetch/player_correlation/ranked_wr_battles/77'))).toBeTruthy();
    expect(requests.some((url) => url.includes('/api/fetch/ranked_data/77'))).toBeTruthy();
    expect(requests.some((url) => url.includes('/api/fetch/player_correlation/tier_type/77'))).toBeTruthy();
    expect(requests.some((url) => url.includes('/api/fetch/type_data/77'))).toBeTruthy();
    expect(requests.some((url) => url.includes('/api/fetch/tier_data/77'))).toBeTruthy();
    expect(requests.some((url) => url.includes('/api/fetch/player_clan_battle_seasons/77'))).toBeTruthy();
});