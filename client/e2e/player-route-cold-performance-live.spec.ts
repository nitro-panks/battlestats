import { expect, test } from '@playwright/test';
import {
    APP_ORIGIN,
    PLAYER_SAMPLE_SIZE,
    average,
    collectProfileBenchmarkCandidates,
    matchesPlayerEndpoint,
    percentile,
    writeBenchmarkArtifact,
    type BenchmarkCandidate,
} from './liveBenchmarkSupport';

interface PlayerRouteMeasurement extends BenchmarkCandidate {
    playerRequestCount: number;
    routeToHeaderMs: number;
    playerRequestToHeaderMs: number | null;
    playerRoundTripMs: number | null;
    responseToHeaderMs: number | null;
    playerPayloadBytes: number;
}

interface BenchmarkFailure {
    name: string;
    playerId: number;
    stage: 'route';
    message: string;
}

const EMPTY_TIER_TYPE_PAYLOAD = {
    metric: 'tier_type',
    label: 'Tier vs Type Profile',
    x_label: 'Ship Type',
    y_label: 'Tier',
    tracked_population: 0,
    x_labels: ['Destroyer', 'Cruiser', 'Battleship', 'Aircraft Carrier', 'Submarine'],
    y_values: [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
    tiles: [],
    trend: [],
    player_cells: [],
};

test.describe('live player route cold benchmark', () => {
    test.setTimeout(300000);

    test('loads 10 real player routes with cold per-player browser state', async ({ browser }) => {
        const benchmarkCandidates = await collectProfileBenchmarkCandidates();
        expect(benchmarkCandidates.length).toBeGreaterThanOrEqual(PLAYER_SAMPLE_SIZE);

        const measurements: PlayerRouteMeasurement[] = [];
        const failures: BenchmarkFailure[] = [];

        for (const candidate of benchmarkCandidates) {
            if (measurements.length >= PLAYER_SAMPLE_SIZE) {
                break;
            }

            const page = await browser.newPage();
            let playerRequestCount = 0;
            let playerRequestStart = 0;
            let playerResponseEnd = 0;
            let playerPayloadBytes = 0;

            page.on('request', (request) => {
                const url = request.url();
                if (matchesPlayerEndpoint(url, `/api/player/${encodeURIComponent(candidate.name)}`)) {
                    playerRequestCount += 1;
                    if (!playerRequestStart) {
                        playerRequestStart = performance.now();
                    }
                }
            });

            page.on('response', async (response) => {
                const url = response.url();
                if (!matchesPlayerEndpoint(url, `/api/player/${encodeURIComponent(candidate.name)}`)) {
                    return;
                }

                await response.finished();
                playerResponseEnd = performance.now();

                if (!playerPayloadBytes) {
                    const body = await response.text();
                    playerPayloadBytes = Buffer.byteLength(body, 'utf8');
                }
            });

            await page.route('**/api/**', async (route) => {
                const requestUrl = route.request().url();

                if (matchesPlayerEndpoint(requestUrl, `/api/player/${encodeURIComponent(candidate.name)}`)) {
                    await route.continue();
                    return;
                }

                if (matchesPlayerEndpoint(requestUrl, `/api/fetch/player_correlation/tier_type/${candidate.playerId}`)) {
                    await route.fulfill({
                        status: 200,
                        contentType: 'application/json',
                        body: JSON.stringify(EMPTY_TIER_TYPE_PAYLOAD),
                    });
                    return;
                }

                if (requestUrl.includes('/api/analytics/entity-view')) {
                    await route.fulfill({ status: 204, body: '' });
                    return;
                }

                if (requestUrl.includes('/api/fetch/')) {
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

                await route.continue();
            });

            const routeStart = performance.now();
            try {
                await page.goto(`${APP_ORIGIN}/player/${encodeURIComponent(candidate.name)}`, { waitUntil: 'domcontentloaded' });
                await expect(page.getByRole('heading', { name: candidate.name, exact: true })).toBeVisible({ timeout: 30000 });

                const renderEnd = performance.now();
                measurements.push({
                    ...candidate,
                    playerRequestCount,
                    routeToHeaderMs: Number((renderEnd - routeStart).toFixed(2)),
                    playerRequestToHeaderMs: playerRequestStart ? Number((renderEnd - playerRequestStart).toFixed(2)) : null,
                    playerRoundTripMs: playerRequestStart && playerResponseEnd ? Number((playerResponseEnd - playerRequestStart).toFixed(2)) : null,
                    responseToHeaderMs: playerResponseEnd ? Number((renderEnd - playerResponseEnd).toFixed(2)) : null,
                    playerPayloadBytes,
                });
            } catch (error) {
                failures.push({
                    name: candidate.name,
                    playerId: candidate.playerId,
                    stage: 'route',
                    message: error instanceof Error ? error.message : String(error),
                });
            } finally {
                await page.close();
            }
        }

        expect(measurements.length).toBe(PLAYER_SAMPLE_SIZE);

        const routeToHeaderValues = measurements.map((entry) => entry.routeToHeaderMs);
        const playerRequestToHeaderValues = measurements
            .map((entry) => entry.playerRequestToHeaderMs)
            .filter((value): value is number => value != null);
        const playerRoundTripValues = measurements
            .map((entry) => entry.playerRoundTripMs)
            .filter((value): value is number => value != null);
        const responseToHeaderValues = measurements
            .map((entry) => entry.responseToHeaderMs)
            .filter((value): value is number => value != null);
        const payloadBytesValues = measurements.map((entry) => entry.playerPayloadBytes);

        const summary = {
            sampledPlayers: measurements.length,
            routeToHeaderMs: {
                avg: average(routeToHeaderValues),
                median: percentile(routeToHeaderValues, 0.5),
                p95: percentile(routeToHeaderValues, 0.95),
                max: percentile(routeToHeaderValues, 1),
            },
            playerRequestToHeaderMs: {
                avg: average(playerRequestToHeaderValues),
                median: percentile(playerRequestToHeaderValues, 0.5),
                p95: percentile(playerRequestToHeaderValues, 0.95),
                max: percentile(playerRequestToHeaderValues, 1),
            },
            playerRoundTripMs: {
                avg: average(playerRoundTripValues),
                median: percentile(playerRoundTripValues, 0.5),
                p95: percentile(playerRoundTripValues, 0.95),
                max: percentile(playerRoundTripValues, 1),
            },
            responseToHeaderMs: {
                avg: average(responseToHeaderValues),
                median: percentile(responseToHeaderValues, 0.5),
                p95: percentile(responseToHeaderValues, 0.95),
                max: percentile(responseToHeaderValues, 1),
            },
            playerPayloadBytes: {
                avg: average(payloadBytesValues),
                median: percentile(payloadBytesValues, 0.5),
                p95: percentile(payloadBytesValues, 0.95),
                max: percentile(payloadBytesValues, 1),
            },
        };

        const artifactPaths = writeBenchmarkArtifact({
            benchmarkName: 'player-route-cold-performance-live',
            summary,
            measurements,
            failures,
            extraMetadata: {
                samplePoolSize: benchmarkCandidates.length,
                benchmarkSurface: 'player-route-shell',
            },
        });

        console.log(`player-route-cold-live-perf ${JSON.stringify({ artifactPaths, summary, players: measurements, failures })}`);

        expect(measurements.every((entry) => entry.playerRequestCount === 1)).toBeTruthy();
    });
});