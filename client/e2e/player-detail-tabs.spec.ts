import { expect, test } from '@playwright/test';

const playerRoutePayload = {
    id: 1,
    name: 'Player One',
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
    ranked_json: [{ total_battles: 48, total_wins: 28, win_rate: 0.583, highest_league_name: 'Silver' }],
    randoms_json: [
        {
            ship_name: 'Montana',
            ship_chart_name: 'Montana',
            ship_type: 'Battleship',
            ship_tier: 10,
            pvp_battles: 420,
            wins: 239,
            win_ratio: 0.569,
        },
    ],
    efficiency_json: [
        {
            ship_id: 1001,
            top_grade_class: 2,
            top_grade_label: 'Grade I',
            badge_label: 'Grade I',
            ship_name: 'Shimakaze',
            ship_chart_name: 'Shimakaze',
            ship_type: 'Destroyer',
            ship_tier: 10,
            nation: 'japan',
        },
    ],
};

const clanPlotPayload = [
    { player_name: 'Player One', pvp_battles: 5400, pvp_ratio: 56 },
    { player_name: 'Clan Mate', pvp_battles: 4600, pvp_ratio: 53.4 },
];

const clanMembersPayload = [
    {
        name: 'Player One',
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

const winRateSurvivalPayload = {
    metric: 'win_rate_survival',
    label: 'Win Rate vs Survival',
    x_label: 'Win Rate',
    y_label: 'Survival',
    tracked_population: 2000,
    correlation: 0.42,
    x_domain: { min: 40, max: 70, bin_width: 2 },
    y_domain: { min: 10, max: 60, bin_width: 5 },
    tiles: [
        { x_index: 7, y_index: 5, count: 220 },
        { x_index: 8, y_index: 5, count: 180 },
    ],
    trend: [
        { x_index: 5, y: 28, count: 140 },
        { x_index: 7, y: 36, count: 220 },
        { x_index: 10, y: 42, count: 170 },
    ],
};

const battlesDistributionPayload = {
    metric: 'battles_played',
    label: 'Battles Played',
    x_label: 'Battles',
    scale: 'log',
    value_format: 'integer',
    tracked_population: 2000,
    bins: [
        { bin_min: 10, bin_max: 100, count: 110 },
        { bin_min: 100, bin_max: 1000, count: 420 },
        { bin_min: 1000, bin_max: 10000, count: 880 },
    ],
};

const randomsPayload = [
    { pvp_battles: 420, ship_name: 'Montana', ship_chart_name: 'Montana', ship_type: 'Battleship', ship_tier: 10, win_ratio: 0.569, wins: 239 },
    { pvp_battles: 320, ship_name: 'Des Moines', ship_chart_name: 'Des Moines', ship_type: 'Cruiser', ship_tier: 10, win_ratio: 0.551, wins: 176 },
];

const rankedHeatmapPayload = {
    metric: 'ranked_wr_battles',
    label: 'Ranked Games vs Win Rate',
    x_label: 'Total Ranked Games',
    y_label: 'Ranked Win Rate',
    tracked_population: 800,
    correlation: 0.31,
    x_scale: 'log',
    y_scale: 'linear',
    x_ticks: [50, 100, 200, 400],
    x_edges: [50, 59, 71, 84, 100, 119, 141, 168, 200, 238, 283, 336, 400],
    y_domain: { min: 35, max: 70, bin_width: 0.75 },
    tiles: [
        { x_index: 1, y_index: 20, count: 80 },
        { x_index: 4, y_index: 28, count: 70 },
    ],
    trend: [
        { x_index: 1, y: 50, count: 70 },
        { x_index: 4, y: 56, count: 60 },
    ],
    player_point: { x: 48, y: 58.3, label: 'Player One' },
};

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

const tierTypePayload = {
    metric: 'tier_type',
    label: 'Tier vs Ship Type',
    x_label: 'Ship Type',
    y_label: 'Tier',
    tracked_population: 739,
    x_labels: ['Destroyer', 'Cruiser', 'Battleship', 'Aircraft Carrier', 'Submarine'],
    y_values: [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
    tiles: [
        { x_index: 2, y_index: 1, count: 320000 },
        { x_index: 1, y_index: 1, count: 410000 },
    ],
    trend: [
        { x_index: 2, avg_tier: 9.3, count: 320000 },
        { x_index: 1, avg_tier: 9.1, count: 410000 },
    ],
    player_cells: [
        { ship_type: 'Battleship', ship_tier: 10, pvp_battles: 420, wins: 239, win_ratio: 0.569 },
        { ship_type: 'Cruiser', ship_tier: 10, pvp_battles: 320, wins: 176, win_ratio: 0.551 },
    ],
};

const typePayload = [
    { ship_type: 'Battleship', pvp_battles: 900, wins: 504, win_ratio: 0.56 },
    { ship_type: 'Cruiser', pvp_battles: 720, wins: 400, win_ratio: 0.556 },
];

const tierPayload = [
    { ship_tier: 10, pvp_battles: 1100, wins: 620, win_ratio: 0.564 },
    { ship_tier: 8, pvp_battles: 620, wins: 340, win_ratio: 0.548 },
];

const playerClanBattleSeasonsPayload = [
    {
        season_id: 31,
        season_name: 'Northern Waters',
        season_label: 'S31',
        start_date: '2025-09-01',
        end_date: '2025-10-15',
        ship_tier_min: 10,
        ship_tier_max: 10,
        battles: 36,
        wins: 21,
        losses: 15,
        win_rate: 58.3,
    },
];

const FAILURE_TEXTS = [
    'Player not found.',
    'Unable to load clan chart.',
    'Unable to load win rate and survival chart.',
    'Unable to load distribution chart.',
    'Unable to load tier and ship-type heatmap.',
    'Unable to load ranked heatmap.',
    'Unable to load ranked data right now.',
    'Unable to load clan battle seasons right now.',
];

test('player detail tabs settle without error across all insights panels', async ({ page }) => {
    let tierTypeRequestCount = 0;

    await page.route('**/api/**', async (route) => {
        const requestUrl = route.request().url();

        if (requestUrl.includes('/api/player/Player%20One') || requestUrl.includes('/api/player/Player%2520One')) {
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

        if (requestUrl.includes('/api/fetch/player_correlation/win_rate_survival/')) {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(winRateSurvivalPayload) });
            return;
        }

        if (requestUrl.includes('/api/fetch/player_distribution/battles_played/')) {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(battlesDistributionPayload) });
            return;
        }

        if (requestUrl.includes('/api/fetch/randoms_data/77')) {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(randomsPayload) });
            return;
        }

        if (requestUrl.includes('/api/fetch/player_correlation/ranked_wr_battles/77')) {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(rankedHeatmapPayload) });
            return;
        }

        if (requestUrl.includes('/api/fetch/ranked_data/77')) {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(rankedSeasonsPayload) });
            return;
        }

        if (requestUrl.includes('/api/fetch/player_correlation/tier_type/77')) {
            tierTypeRequestCount += 1;

            if (tierTypeRequestCount < 3) {
                await route.fulfill({
                    status: 200,
                    contentType: 'application/json',
                    headers: {
                        'X-Tier-Type-Pending': 'true',
                    },
                    body: JSON.stringify({
                        ...tierTypePayload,
                        player_cells: [],
                    }),
                });
                return;
            }

            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(tierTypePayload) });
            return;
        }

        if (requestUrl.includes('/api/fetch/type_data/77')) {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(typePayload) });
            return;
        }

        if (requestUrl.includes('/api/fetch/tier_data/77')) {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(tierPayload) });
            return;
        }

        if (requestUrl.includes('/api/fetch/player_clan_battle_seasons/77')) {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(playerClanBattleSeasonsPayload) });
            return;
        }

        if (requestUrl.includes('/api/analytics/entity-view')) {
            await route.fulfill({ status: 204, body: '' });
            return;
        }

        await route.fulfill({
            status: 404,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'Unhandled test route' }),
        });
    });

    await page.goto('/player/Player%20One');

    await expect(page.getByRole('heading', { name: 'Player One' })).toBeVisible();
    await expect.poll(async () => page.locator('#clan_plot_container svg').count()).toBeGreaterThan(0);
    await expect(page.getByText('Tier vs Type Profile', { exact: true })).toBeVisible();
    await expect(page.getByText('Battleship T10', { exact: true })).toBeVisible();
    await expect(page.getByText('This captain does not have enough tier and ship-type variety yet to draw a useful heatmap.', { exact: true })).toHaveCount(0);

    for (const text of FAILURE_TEXTS) {
        await expect(page.getByText(text, { exact: true })).toHaveCount(0);
    }

    const tabChecks = [
        { tab: 'Profile', title: 'Tier vs Type Profile', dataText: 'Battleship T10', minSvgCount: 3 },
        { tab: 'Population', title: 'Win Rate vs Survival', dataText: 'Battles Played Distribution', minSvgCount: 2 },
        { tab: 'Ships', title: 'Top Ships (Random Battles)', dataText: 'Montana', minSvgCount: 1 },
        { tab: 'Ranked', title: 'Ranked Seasons', dataText: 'S32', minSvgCount: 1 },
        { tab: 'Efficiency', title: 'Efficiency Badges', dataText: 'Shimakaze', minSvgCount: 0 },
        { tab: 'Clan Battles', title: 'Clan Battle Seasons', dataText: 'S31', minSvgCount: 0 },
    ];

    for (const check of tabChecks) {
        await page.getByRole('tab', { name: check.tab, exact: true }).click();
        const panel = page.locator('[role="tabpanel"]');

        await expect(panel.getByText(check.title, { exact: true })).toBeVisible();
        await expect(panel.getByText(check.dataText, { exact: true }).first()).toBeVisible();

        if (check.minSvgCount > 0) {
            await expect.poll(async () => panel.locator('svg').count()).toBeGreaterThanOrEqual(check.minSvgCount);
        }

        for (const text of FAILURE_TEXTS) {
            await expect(panel.getByText(text, { exact: true })).toHaveCount(0);
        }
    }
});