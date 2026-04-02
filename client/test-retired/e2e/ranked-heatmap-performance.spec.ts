import { expect, test } from '@playwright/test';

const playerRoutePayload = {
    id: 1,
    name: 'Ranked Heatmap Player',
    player_id: 77,
    kill_ratio: 1.25,
    actual_kdr: 1.18,
    player_score: 2.4,
    total_battles: 6200,
    pvp_battles: 5400,
    pvp_wins: 3024,
    pvp_losses: 2376,
    pvp_ratio: 56,
    pvp_survival_rate: 38,
    wins_survival_rate: 52,
    creation_date: '2020-01-01',
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
    verdict: 'Warrior',
    ranked_json: [{ total_battles: 420, total_wins: 245, win_rate: 0.583, highest_league_name: 'Silver' }],
    randoms_json: [],
    efficiency_json: [],
};

const clanPlotPayload = [
    { player_name: 'Ranked Heatmap Player', pvp_battles: 5400, pvp_ratio: 56 },
];

const clanMembersPayload = [
    {
        name: 'Ranked Heatmap Player',
        is_hidden: false,
        pvp_ratio: 56,
        days_since_last_battle: 2,
        is_leader: false,
        is_pve_player: false,
        is_sleepy_player: false,
        is_ranked_player: true,
        is_clan_battle_player: true,
        clan_battle_win_rate: 57.3,
        efficiency_hydration_pending: false,
        highest_ranked_league: 'Silver',
        ranked_hydration_pending: false,
        ranked_updated_at: '2026-03-01T00:00:00Z',
        efficiency_rank_percentile: 0.95,
        efficiency_rank_tier: 'E',
        has_efficiency_rank_icon: true,
        efficiency_rank_population_size: 54000,
        efficiency_rank_updated_at: '2026-03-01T00:00:00Z',
        activity_bucket: 'active_7d',
    },
];

const rankedSeasonsPayload = [
    {
        season_id: 32,
        season_label: 'S32',
        start_date: '2025-11-01',
        total_battles: 48,
        total_wins: 28,
        win_rate: 58.3,
        highest_league_name: 'Silver',
        top_ship_name: 'St. Vincent',
    },
];

const buildRankedHeatmapPayload = () => {
    const xEdges = [50];
    const growthFactor = Math.sqrt(Math.sqrt(2));
    for (let index = 0; index < 37; index += 1) {
        xEdges.push(Math.round(xEdges[xEdges.length - 1] * growthFactor));
    }

    const tiles: Array<{ x_index: number; y_index: number; count: number }> = [];
    for (let xIndex = 0; xIndex < xEdges.length - 1 && tiles.length < 1491; xIndex += 1) {
        for (let yIndex = 0; yIndex < 53 && tiles.length < 1491; yIndex += 1) {
            tiles.push({
                x_index: xIndex,
                y_index: yIndex,
                count: ((xIndex + 1) * (yIndex + 3)) % 97 + 1,
            });
        }
    }

    const trend = Array.from({ length: 37 }, (_, xIndex) => ({
        x_index: xIndex,
        y: Number((48 + (xIndex * 0.32)).toFixed(2)),
        count: 80 + xIndex,
    }));

    return {
        metric: 'ranked_wr_battles',
        label: 'Ranked Games vs Win Rate',
        x_label: 'Total Ranked Games',
        y_label: 'Ranked Win Rate',
        tracked_population: 53895,
        correlation: 0.31,
        x_scale: 'log',
        y_scale: 'linear',
        x_ticks: [50, 100, 200, 400, 800, 1600],
        x_edges: xEdges,
        y_domain: { min: 35, max: 75, bin_width: 0.75 },
        tiles,
        trend,
        player_point: { x: 420, y: 58.3, label: 'Ranked Heatmap Player' },
    };
};

test('ranked heatmap draws quickly with the compact indexed payload', async ({ page }) => {
    const rankedHeatmapPayload = buildRankedHeatmapPayload();
    const payloadBytes = JSON.stringify(rankedHeatmapPayload).length;
    let rankedRequestStart = 0;
    let rankedResponseEnd = 0;

    await page.addInitScript(() => {
        Object.defineProperty(window, 'requestIdleCallback', {
            configurable: true,
            writable: true,
            value: () => 1,
        });
        Object.defineProperty(window, 'cancelIdleCallback', {
            configurable: true,
            writable: true,
            value: () => undefined,
        });
    });

    page.on('request', (request) => {
        if (request.url().includes('/api/fetch/player_correlation/ranked_wr_battles/77')) {
            rankedRequestStart = performance.now();
        }
    });

    page.on('response', async (response) => {
        if (response.url().includes('/api/fetch/player_correlation/ranked_wr_battles/77')) {
            await response.finished();
            rankedResponseEnd = performance.now();
        }
    });

    await page.route('**/api/**', async (route) => {
        const requestUrl = route.request().url();

        if (requestUrl.includes('/api/player/Ranked%20Heatmap%20Player') || requestUrl.includes('/api/player/Ranked%2520Heatmap%2520Player')) {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify(playerRoutePayload),
            });
            return;
        }

        if (requestUrl.includes('/api/fetch/clan_data/100:active')) {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(clanPlotPayload) });
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
                body: JSON.stringify(clanMembersPayload),
            });
            return;
        }

        if (requestUrl.includes('/api/fetch/player_correlation/ranked_wr_battles/77')) {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify(rankedHeatmapPayload),
            });
            return;
        }

        if (requestUrl.includes('/api/fetch/ranked_data/77')) {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(rankedSeasonsPayload) });
            return;
        }

        if (requestUrl.includes('/api/analytics/entity-view')) {
            await route.fulfill({ status: 204, body: '' });
            return;
        }

        if (requestUrl.includes('/api/fetch/')) {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
            return;
        }

        await route.fulfill({
            status: 404,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'Unhandled test route' }),
        });
    });

    await page.goto('/player/Ranked%20Heatmap%20Player');

    await expect(page.getByRole('heading', { name: 'Ranked Heatmap Player' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Profile' })).toHaveAttribute('aria-selected', 'true');

    const requestsBeforeClick = await page.evaluate(() => window.performance.getEntriesByType('resource').map((entry) => entry.name));
    expect(requestsBeforeClick.some((name) => name.includes('/api/fetch/player_correlation/ranked_wr_battles/77'))).toBeFalsy();

    const clickStart = performance.now();
    await page.getByRole('tab', { name: 'Ranked' }).click();

    await expect.poll(async () => page.locator('.ranked-heat-tile').count()).toBe(1491);
    await expect.poll(async () => page.locator('.ranked-heat-tile').count()).toBeGreaterThan(1000);

    const renderEnd = performance.now();
    const metrics = {
        payloadBytes,
        tileCount: rankedHeatmapPayload.tiles.length,
        trendCount: rankedHeatmapPayload.trend.length,
        clickToRequestMs: rankedRequestStart ? Number((rankedRequestStart - clickStart).toFixed(2)) : null,
        requestRoundTripMs: rankedRequestStart && rankedResponseEnd ? Number((rankedResponseEnd - rankedRequestStart).toFixed(2)) : null,
        clickToRenderMs: Number((renderEnd - clickStart).toFixed(2)),
        responseToRenderMs: rankedResponseEnd ? Number((renderEnd - rankedResponseEnd).toFixed(2)) : null,
    };

    console.log(`ranked-heatmap-perf ${JSON.stringify(metrics)}`);

    expect(metrics.requestRoundTripMs).not.toBeNull();
    expect(metrics.clickToRenderMs).toBeLessThan(2000);
});