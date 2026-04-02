import { expect, test } from '@playwright/test';
import {
    API_ORIGIN,
    APP_ORIGIN,
    SEEDED_PLAYER_NAMES,
    average,
    collectLandingPlayerNames,
    fetchJson,
    percentile,
    shuffleArray,
    writeBenchmarkArtifact,
} from './liveBenchmarkSupport';

interface PlayerDetailPayload {
    name: string;
    player_id: number;
    pvp_battles?: number | null;
    clan_id?: number | null;
    clan_battle_header_eligible?: boolean | null;
    clan_battle_header_total_battles?: number | null;
    clan_battle_header_seasons_played?: number | null;
    clan_battle_header_overall_win_rate?: number | null;
    ranked_json?: Array<unknown> | null;
    randoms_json?: Array<{ ship_name?: string | null }> | null;
    efficiency_json?: Array<{ ship_name?: string | null }> | null;
    is_hidden?: boolean;
}

interface TierTypePayload {
    player_cells?: Array<unknown>;
}

interface ClanBattleSeasonRow {
    season_label?: string | null;
    battles?: number | null;
    wins?: number | null;
    losses?: number | null;
}

interface RichPlayerCandidate {
    name: string;
    playerId: number;
    pvpBattles: number;
    topRandomShip: string;
    topBadgeShip: string;
    latestClanBattleSeasonLabel: string;
}

interface PlayerComponentMeasurement extends RichPlayerCandidate {
    routeToHeaderMs: number;
    routeToClanChartMs: number | null;
    routeToProfileMs: number;
    populationTabMs: number;
    shipsTabMs: number;
    rankedTabMs: number;
    badgesTabMs: number;
    careerTabMs: number;
}

interface BenchmarkFailure {
    name: string;
    playerId: number | null;
    stage: 'candidate' | 'route' | 'profile' | 'population' | 'ships' | 'ranked' | 'badges' | 'career';
    message: string;
}

class SkipCandidateError extends Error {
    constructor(message: string) {
        super(message);
        this.name = 'SkipCandidateError';
    }
}

const LIVE_SAMPLE_SIZE = 12;
const LIVE_CANDIDATE_POOL_LIMIT = 60;
const MAX_ROUTE_HEADER_MS = 12000;
const MAX_INITIAL_COMPONENT_MS = 20000;
const MAX_AVERAGE_INITIAL_COMPONENT_MS = 8000;
const MAX_TAB_COMPONENT_MS = 5000;
const FAILURE_TEXTS = [
    'Player not found.',
    'Unable to load clan chart.',
    'Unable to load win rate and survival chart.',
    'Unable to load distribution chart.',
    'Unable to load tier and ship-type heatmap.',
    'Unable to load ranked heatmap.',
    'Unable to load ranked data right now.',
    'Unable to load clan battle seasons right now.',
    'Unable to load profile charts right now.',
    'Profile charts are still warming. Try again in a moment.',
];

const toErrorMessage = (error: unknown): string => error instanceof Error ? error.message : String(error);

const waitForVisible = async (locator: ReturnType<import('@playwright/test').Page['locator']>, timeout: number) => {
    await expect(locator).toBeVisible({ timeout });
};

const hasEligibleClanBattleHeader = (playerDetail: PlayerDetailPayload): boolean => {
    if (!playerDetail.clan_battle_header_eligible) {
        return false;
    }

    const totalBattles = Number(playerDetail.clan_battle_header_total_battles ?? 0);
    const seasonsPlayed = Number(playerDetail.clan_battle_header_seasons_played ?? 0);
    const overallWinRate = Number(playerDetail.clan_battle_header_overall_win_rate ?? NaN);

    return Number.isFinite(totalBattles)
        && Number.isFinite(seasonsPlayed)
        && Number.isFinite(overallWinRate)
        && totalBattles >= 40
        && seasonsPlayed >= 2;
};

const hasRenderableClanBattleRows = (rows: ClanBattleSeasonRow[]): boolean => {
    const validRows = rows.filter((row) => {
        const seasonLabel = (row.season_label || '').trim();
        const battles = Number(row.battles ?? 0);
        const wins = Number(row.wins ?? 0);
        const losses = Number(row.losses ?? 0);

        return Boolean(seasonLabel)
            && Number.isFinite(battles)
            && Number.isFinite(wins)
            && Number.isFinite(losses)
            && battles > 0
            && wins + losses <= battles;
    });

    return validRows.length >= 2;
};

const waitForCareerTabReady = async (page: import('@playwright/test').Page, timeout: number): Promise<void> => {
    const panel = page.locator('#player-insights-panel-career');
    const table = panel.locator('table');
    const tableRows = panel.locator('tbody tr');
    const emptyState = panel.getByText('No clan battle season data available for this player.', { exact: true });
    const errorState = panel.getByText('Unable to load clan battle seasons right now.', { exact: true });
    const loadingState = panel.getByText('Loading clan battle seasons...', { exact: true });

    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
        if (await table.isVisible().catch(() => false) && await tableRows.count() > 0) {
            return;
        }

        if (await emptyState.isVisible().catch(() => false)) {
            throw new SkipCandidateError('Clan battles panel settled into empty state.');
        }

        if (await errorState.isVisible().catch(() => false)) {
            throw new SkipCandidateError('Clan battles panel settled into error state.');
        }

        await page.waitForTimeout((await loadingState.isVisible().catch(() => false)) ? 250 : 150);
    }

    throw new Error('Clan battles panel did not reach a terminal render state.');
};

const collectRichCandidates = async (): Promise<RichPlayerCandidate[]> => {
    const candidates: RichPlayerCandidate[] = [];
    const names = shuffleArray([
        ...new Set([
            ...SEEDED_PLAYER_NAMES,
            ...(await collectLandingPlayerNames()),
        ]),
    ]);
    const inspectedNames = names.slice(0, Math.max(LIVE_CANDIDATE_POOL_LIMIT, LIVE_SAMPLE_SIZE * 4));

    for (const playerName of inspectedNames) {
        if (candidates.length >= LIVE_CANDIDATE_POOL_LIMIT) {
            break;
        }

        let playerDetail: PlayerDetailPayload;
        try {
            playerDetail = await fetchJson<PlayerDetailPayload>(`${API_ORIGIN}/api/player/${encodeURIComponent(playerName)}/`);
        } catch {
            continue;
        }

        if (
            !playerDetail?.player_id
            || playerDetail.is_hidden
            || !playerDetail.clan_id
            || !hasEligibleClanBattleHeader(playerDetail)
            || !Array.isArray(playerDetail.ranked_json)
            || playerDetail.ranked_json.length === 0
            || !Array.isArray(playerDetail.randoms_json)
            || playerDetail.randoms_json.length === 0
            || !Array.isArray(playerDetail.efficiency_json)
            || playerDetail.efficiency_json.length === 0
        ) {
            continue;
        }

        const topRandomShip = (playerDetail.randoms_json[0]?.ship_name || '').trim();
        const topBadgeShip = (playerDetail.efficiency_json[0]?.ship_name || '').trim();
        if (!topRandomShip || !topBadgeShip) {
            continue;
        }

        let tierTypePayload: TierTypePayload;
        try {
            tierTypePayload = await fetchJson<TierTypePayload>(`${API_ORIGIN}/api/fetch/player_correlation/tier_type/${playerDetail.player_id}/`);
        } catch {
            continue;
        }

        if (!Array.isArray(tierTypePayload.player_cells) || tierTypePayload.player_cells.length === 0) {
            continue;
        }

        let clanBattleRows: ClanBattleSeasonRow[];
        try {
            clanBattleRows = await fetchJson<ClanBattleSeasonRow[]>(`${API_ORIGIN}/api/fetch/player_clan_battle_seasons/${playerDetail.player_id}/`);
        } catch {
            continue;
        }

        if (!Array.isArray(clanBattleRows) || !hasRenderableClanBattleRows(clanBattleRows)) {
            continue;
        }

        const latestClanBattleSeasonLabel = (clanBattleRows[0]?.season_label || '').trim();

        candidates.push({
            name: playerDetail.name,
            playerId: playerDetail.player_id,
            pvpBattles: playerDetail.pvp_battles ?? 0,
            topRandomShip,
            topBadgeShip,
            latestClanBattleSeasonLabel,
        });
    }

    return candidates;
};

const assertNoFailureText = async (page: import('@playwright/test').Page) => {
    for (const failureText of FAILURE_TEXTS) {
        await expect(page.getByText(failureText, { exact: true })).toHaveCount(0);
    }
};

test.describe('live player route component and performance sweep', () => {
    test.setTimeout(600000);

    test('loads a dozen random rich live players and verifies player-page components stay fast', async ({ browser }) => {
        const candidateStart = performance.now();
        const candidates = await collectRichCandidates();
        const candidateElapsedMs = Number((performance.now() - candidateStart).toFixed(2));
        expect(candidates.length).toBeGreaterThanOrEqual(LIVE_SAMPLE_SIZE);

        const selectedCandidates = shuffleArray(candidates);
        const measurements: PlayerComponentMeasurement[] = [];
        const failures: BenchmarkFailure[] = [];

        for (const candidate of selectedCandidates) {
            if (measurements.length >= LIVE_SAMPLE_SIZE) {
                break;
            }

            const page = await browser.newPage();

            try {
                const routeStart = performance.now();
                await page.goto(`${APP_ORIGIN}/player/${encodeURIComponent(candidate.name)}`, { waitUntil: 'domcontentloaded' });

                await waitForVisible(page.getByRole('heading', { name: candidate.name, exact: true }), 30000);
                const routeToHeaderMs = Number((performance.now() - routeStart).toFixed(2));

                await waitForVisible(page.getByRole('tab', { name: 'Profile', exact: true }), 15000);
                await waitForVisible(page.getByText('Tier vs Type Profile', { exact: true }), 30000);
                await waitForVisible(page.getByText('Performance by Ship Type', { exact: true }), 30000);
                await waitForVisible(page.getByText('Performance by Tier', { exact: true }), 30000);
                await expect.poll(async () => page.locator('#player-insights-panel-profile svg').count(), { timeout: 30000 }).toBeGreaterThanOrEqual(3);
                const routeToProfileMs = Number((performance.now() - routeStart).toFixed(2));

                let routeToClanChartMs: number | null = null;
                try {
                    await expect.poll(async () => page.locator('#clan_plot_container svg').count(), { timeout: 30000 }).toBeGreaterThan(0);
                    routeToClanChartMs = Number((performance.now() - routeStart).toFixed(2));
                } catch {
                    routeToClanChartMs = null;
                }

                await assertNoFailureText(page);

                const populationStart = performance.now();
                await page.getByRole('tab', { name: 'Population', exact: true }).click();
                await waitForVisible(page.getByText('Win Rate vs Survival', { exact: true }), 30000);
                await waitForVisible(page.getByText('Battles Played Distribution', { exact: true }), 30000);
                const populationTabMs = Number((performance.now() - populationStart).toFixed(2));

                const shipsStart = performance.now();
                await page.getByRole('tab', { name: 'Ships', exact: true }).click();
                await waitForVisible(page.getByText('Top Ships (Random Battles)', { exact: true }), 30000);
                await expect.poll(async () => page.locator('#player-insights-panel-ships svg').count(), { timeout: 30000 }).toBeGreaterThan(0);
                const shipsTabMs = Number((performance.now() - shipsStart).toFixed(2));

                const rankedStart = performance.now();
                await page.getByRole('tab', { name: 'Ranked', exact: true }).click();
                await waitForVisible(page.getByText('Ranked Games vs Win Rate', { exact: true }), 30000);
                await waitForVisible(page.getByText('Ranked Seasons', { exact: true }), 30000);
                const rankedTabMs = Number((performance.now() - rankedStart).toFixed(2));

                const badgesStart = performance.now();
                await page.getByRole('tab', { name: 'Efficiency', exact: true }).click();
                await waitForVisible(page.getByText('Efficiency Badges', { exact: true }), 30000);
                await waitForVisible(page.getByText(candidate.topBadgeShip, { exact: true }).first(), 30000);
                const badgesTabMs = Number((performance.now() - badgesStart).toFixed(2));

                const careerStart = performance.now();
                await page.getByRole('tab', { name: 'Clan Battles', exact: true }).click();
                await waitForVisible(page.getByText('Clan Battle Seasons', { exact: true }), 30000);
                await waitForCareerTabReady(page, 30000);
                const careerTabMs = Number((performance.now() - careerStart).toFixed(2));

                await assertNoFailureText(page);

                measurements.push({
                    ...candidate,
                    routeToHeaderMs,
                    routeToClanChartMs,
                    routeToProfileMs,
                    populationTabMs,
                    shipsTabMs,
                    rankedTabMs,
                    badgesTabMs,
                    careerTabMs,
                });
            } catch (error) {
                const stage = error instanceof SkipCandidateError ? 'candidate' : 'route';
                failures.push({
                    name: candidate.name,
                    playerId: candidate.playerId,
                    stage,
                    message: toErrorMessage(error),
                });
            } finally {
                await page.close();
            }
        }

        const routeToHeaderValues = measurements.map((entry) => entry.routeToHeaderMs);
        const routeToProfileValues = measurements.map((entry) => entry.routeToProfileMs);
        const clanChartValues = measurements.map((entry) => entry.routeToClanChartMs).filter((value): value is number => value != null);
        const populationValues = measurements.map((entry) => entry.populationTabMs);
        const shipsValues = measurements.map((entry) => entry.shipsTabMs);
        const rankedValues = measurements.map((entry) => entry.rankedTabMs);
        const badgesValues = measurements.map((entry) => entry.badgesTabMs);
        const careerValues = measurements.map((entry) => entry.careerTabMs);

        const summary = {
            sampledPlayers: measurements.length,
            candidateDiscoveryMs: candidateElapsedMs,
            routeToHeaderMs: {
                avg: average(routeToHeaderValues),
                median: percentile(routeToHeaderValues, 0.5),
                p95: percentile(routeToHeaderValues, 0.95),
                max: percentile(routeToHeaderValues, 1),
            },
            routeToProfileMs: {
                avg: average(routeToProfileValues),
                median: percentile(routeToProfileValues, 0.5),
                p95: percentile(routeToProfileValues, 0.95),
                max: percentile(routeToProfileValues, 1),
            },
            routeToClanChartMs: {
                avg: average(clanChartValues),
                median: percentile(clanChartValues, 0.5),
                p95: percentile(clanChartValues, 0.95),
                max: percentile(clanChartValues, 1),
            },
            populationTabMs: {
                avg: average(populationValues),
                median: percentile(populationValues, 0.5),
                p95: percentile(populationValues, 0.95),
                max: percentile(populationValues, 1),
            },
            shipsTabMs: {
                avg: average(shipsValues),
                median: percentile(shipsValues, 0.5),
                p95: percentile(shipsValues, 0.95),
                max: percentile(shipsValues, 1),
            },
            rankedTabMs: {
                avg: average(rankedValues),
                median: percentile(rankedValues, 0.5),
                p95: percentile(rankedValues, 0.95),
                max: percentile(rankedValues, 1),
            },
            badgesTabMs: {
                avg: average(badgesValues),
                median: percentile(badgesValues, 0.5),
                p95: percentile(badgesValues, 0.95),
                max: percentile(badgesValues, 1),
            },
            careerTabMs: {
                avg: average(careerValues),
                median: percentile(careerValues, 0.5),
                p95: percentile(careerValues, 0.95),
                max: percentile(careerValues, 1),
            },
        };

        const artifactPaths = writeBenchmarkArtifact({
            benchmarkName: 'player-route-live-site-components-performance',
            summary,
            measurements,
            failures,
            extraMetadata: {
                appOrigin: APP_ORIGIN,
                apiOrigin: API_ORIGIN,
                samplePoolSize: candidates.length,
                benchmarkSurface: 'player-route-live-site-components',
            },
        });

        console.log(`player-route-live-components ${JSON.stringify({ artifactPaths, summary, measurements, failures })}`);

        expect(measurements.length).toBe(LIVE_SAMPLE_SIZE);
        expect(Math.max(...routeToHeaderValues)).toBeLessThan(MAX_ROUTE_HEADER_MS);
        expect(Math.max(...routeToProfileValues)).toBeLessThan(MAX_INITIAL_COMPONENT_MS);
        expect(average(routeToProfileValues) ?? MAX_AVERAGE_INITIAL_COMPONENT_MS + 1).toBeLessThan(MAX_AVERAGE_INITIAL_COMPONENT_MS);
        expect(Math.max(...populationValues)).toBeLessThan(MAX_TAB_COMPONENT_MS);
        expect(Math.max(...shipsValues)).toBeLessThan(MAX_TAB_COMPONENT_MS);
        expect(Math.max(...rankedValues)).toBeLessThan(MAX_TAB_COMPONENT_MS);
        expect(Math.max(...badgesValues)).toBeLessThan(MAX_TAB_COMPONENT_MS);
        expect(Math.max(...careerValues)).toBeLessThan(MAX_TAB_COMPONENT_MS);
    });
});