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
    ranked_json: [],
    randoms_json: [],
    efficiency_json: [],
};

const clanPlotPayload = [
    { player_name: 'Player One', pvp_battles: 5400, pvp_ratio: 56 },
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
        is_ranked_player: false,
        is_clan_battle_player: false,
        clan_battle_win_rate: null,
        efficiency_hydration_pending: false,
        highest_ranked_league: null,
        ranked_hydration_pending: false,
        ranked_updated_at: null,
        efficiency_rank_percentile: null,
        efficiency_rank_tier: null,
        has_efficiency_rank_icon: false,
        efficiency_rank_population_size: null,
        efficiency_rank_updated_at: null,
        activity_bucket: 'active_7d',
    },
];

// --- Tier-type payloads ---

const tierTypePayload = {
    metric: 'tier_type',
    label: 'Tier vs Ship Type',
    x_label: 'Ship Type',
    y_label: 'Tier',
    tracked_population: 2000,
    x_labels: ['Destroyer', 'Cruiser', 'Battleship', 'Aircraft Carrier', 'Submarine'],
    y_values: [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
    tiles: [
        { x_index: 0, y_index: 1, count: 180000 },
        { x_index: 1, y_index: 1, count: 410000 },
        { x_index: 2, y_index: 1, count: 320000 },
        { x_index: 3, y_index: 1, count: 90000 },
        { x_index: 0, y_index: 2, count: 150000 },
        { x_index: 1, y_index: 2, count: 280000 },
        { x_index: 2, y_index: 2, count: 240000 },
    ],
    trend: [
        { x_index: 0, avg_tier: 8.4, count: 330000 },
        { x_index: 1, avg_tier: 9.1, count: 690000 },
        { x_index: 2, avg_tier: 9.3, count: 560000 },
        { x_index: 3, avg_tier: 9.8, count: 90000 },
    ],
    player_cells: [
        { ship_type: 'Destroyer', ship_tier: 10, pvp_battles: 1200, wins: 720, win_ratio: 0.6 },
        { ship_type: 'Cruiser', ship_tier: 10, pvp_battles: 900, wins: 504, win_ratio: 0.56 },
        { ship_type: 'Battleship', ship_tier: 10, pvp_battles: 420, wins: 239, win_ratio: 0.569 },
        { ship_type: 'Battleship', ship_tier: 9, pvp_battles: 320, wins: 176, win_ratio: 0.55 },
        { ship_type: 'Aircraft Carrier', ship_tier: 10, pvp_battles: 80, wins: 42, win_ratio: 0.525 },
    ],
};

// Payload with no AirCarrier duplication — all should map to "Aircraft Carrier" column
const tierTypePayloadWithAirCarrier = {
    ...tierTypePayload,
    x_labels: ['Destroyer', 'Cruiser', 'Battleship', 'Aircraft Carrier', 'Submarine'],
    player_cells: [
        { ship_type: 'Destroyer', ship_tier: 10, pvp_battles: 1200, wins: 720, win_ratio: 0.6 },
        { ship_type: 'Cruiser', ship_tier: 10, pvp_battles: 900, wins: 504, win_ratio: 0.56 },
        { ship_type: 'Aircraft Carrier', ship_tier: 10, pvp_battles: 200, wins: 110, win_ratio: 0.55 },
    ],
};

// Payload with fewer than 2 player cells — should show insufficient variety message
const tierTypePayloadInsufficient = {
    ...tierTypePayload,
    player_cells: [
        { ship_type: 'Destroyer', ship_tier: 10, pvp_battles: 420, wins: 239, win_ratio: 0.569 },
    ],
};

// Payload with no tiles — should show no data message
const tierTypePayloadEmpty = {
    ...tierTypePayload,
    tiles: [],
    trend: [],
    player_cells: [],
};

function setupRoutes(page: import('@playwright/test').Page, tierTypeResponse: object) {
    return page.route('**/api/**', async (route) => {
        const url = route.request().url();

        if (url.includes('/api/player/Player%20One') || url.includes('/api/player/Player%2520One')) {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(playerRoutePayload) });
            return;
        }
        if (url.includes('/api/fetch/clan_data/100')) {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(clanPlotPayload) });
            return;
        }
        if (url.includes('/api/fetch/clan_members/100')) {
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
        if (url.includes('/api/fetch/player_correlation/tier_type/77')) {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(tierTypeResponse) });
            return;
        }
        if (url.includes('/api/analytics/entity-view')) {
            await route.fulfill({ status: 204, body: '' });
            return;
        }

        // Default: return empty JSON for any other API call
        await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    });
}

test.describe('Tier vs Type Heatmap', () => {
    test('renders population tiles, player cells, and trend markers', async ({ page }) => {
        await setupRoutes(page, tierTypePayload);
        await page.goto('/player/Player%20One');

        // Wait for the heatmap to appear in the Profile tab (default tab)
        await expect(page.getByText('Tier vs Type Profile')).toBeVisible({ timeout: 10000 });

        const panel = page.locator('[role="tabpanel"]');

        // Population heatmap tiles
        const gridTiles = panel.locator('.tier-type-grid rect');
        await expect(gridTiles).toHaveCount(7);

        // Player performance circles — one per player_cell entry
        const playerCells = panel.locator('.player-cell');
        await expect(playerCells).toHaveCount(5);

        // Trend line markers — one per trend data point
        const trendMarkers = panel.locator('.trend-marker');
        await expect(trendMarkers).toHaveCount(4);
    });

    test('renders exactly 5 ship type columns on x-axis (DD CA BB CV Sub)', async ({ page }) => {
        await setupRoutes(page, tierTypePayload);
        await page.goto('/player/Player%20One');

        await expect(page.getByText('Tier vs Type Profile')).toBeVisible({ timeout: 10000 });

        // Wait for player cells to render
        await expect(page.locator('.player-cell')).toHaveCount(5, { timeout: 10000 });

        // X-axis should show abbreviated ship types — no duplicates, no extra columns
        const axisTexts = page.locator('.tier-type-grid').locator('..').locator('..').locator('text');
        const allTexts = await axisTexts.allTextContents();
        const shipTypeLabels = allTexts.filter(t => ['DD', 'CA', 'BB', 'CV', 'Sub'].includes(t));
        expect(shipTypeLabels).toEqual(['DD', 'CA', 'BB', 'CV', 'Sub']);
    });

    test('does not render a duplicate AirCarrier column', async ({ page }) => {
        await setupRoutes(page, tierTypePayloadWithAirCarrier);
        await page.goto('/player/Player%20One');

        await expect(page.getByText('Tier vs Type Profile')).toBeVisible({ timeout: 10000 });
        await expect(page.locator('.player-cell')).toHaveCount(3, { timeout: 10000 });

        // Collect all text in the heatmap SVG
        const allTexts = await page.locator('svg text').allTextContents();

        // "CV" should appear exactly once
        const cvCount = allTexts.filter(t => t === 'CV').length;
        expect(cvCount).toBe(1);

        // "AirCarrier" should NOT appear as a raw label
        const airCarrierCount = allTexts.filter(t => t === 'AirCarrier').length;
        expect(airCarrierCount).toBe(0);
    });

    test('shows default summary card with top player cell on load', async ({ page }) => {
        await setupRoutes(page, tierTypePayload);
        await page.goto('/player/Player%20One');

        await expect(page.getByText('Tier vs Type Profile')).toBeVisible({ timeout: 10000 });

        const panel = page.locator('[role="tabpanel"]');

        // Default summary card shows the first player_cell (Destroyer T10)
        await expect(panel.getByText('DD T10')).toBeVisible();
    });

    test('shows insufficient variety message when fewer than 2 player cells', async ({ page }) => {
        await setupRoutes(page, tierTypePayloadInsufficient);
        await page.goto('/player/Player%20One');

        await expect(page.getByText('Tier vs Type Profile')).toBeVisible({ timeout: 10000 });

        await expect(page.getByText('This captain does not have enough tier and ship-type variety yet to draw a useful heatmap.')).toBeVisible();

        // No player cells should be rendered
        await expect(page.locator('.player-cell')).toHaveCount(0);
    });

    test('shows no data message when tiles are empty', async ({ page }) => {
        await setupRoutes(page, tierTypePayloadEmpty);
        await page.goto('/player/Player%20One');

        await expect(page.getByText('Tier vs Type Profile')).toBeVisible({ timeout: 10000 });

        await expect(page.getByText('No tier and ship-type population data available.')).toBeVisible();

        await expect(page.locator('.player-cell')).toHaveCount(0);
        await expect(page.locator('.tier-type-grid rect')).toHaveCount(0);
    });

    test('retries when server signals pending data', async ({ page }) => {
        let requestCount = 0;

        await page.route('**/api/**', async (route) => {
            const url = route.request().url();

            if (url.includes('/api/player/Player%20One') || url.includes('/api/player/Player%2520One')) {
                await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(playerRoutePayload) });
                return;
            }
            if (url.includes('/api/fetch/clan_data/100')) {
                await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(clanPlotPayload) });
                return;
            }
            if (url.includes('/api/fetch/clan_members/100')) {
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
            if (url.includes('/api/fetch/player_correlation/tier_type/77')) {
                requestCount += 1;
                if (requestCount < 3) {
                    // Return pending (empty player_cells) for first 2 requests
                    await route.fulfill({
                        status: 200,
                        contentType: 'application/json',
                        headers: { 'X-Tier-Type-Pending': 'true' },
                        body: JSON.stringify({ ...tierTypePayload, player_cells: [] }),
                    });
                    return;
                }
                await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(tierTypePayload) });
                return;
            }
            if (url.includes('/api/analytics/entity-view')) {
                await route.fulfill({ status: 204, body: '' });
                return;
            }

            await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
        });

        await page.goto('/player/Player%20One');

        // After retries, the full chart should render with player cells
        const playerCells = page.locator('.player-cell');
        await expect(playerCells).toHaveCount(5, { timeout: 15000 });
    });
});
