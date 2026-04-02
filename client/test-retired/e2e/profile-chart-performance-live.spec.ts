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

interface PlayerMeasurement extends BenchmarkCandidate {
    chartRequestCount: number;
    typeRequestCount: number;
    tierRequestCount: number;
    routeToChartsMs: number;
    chartRequestToRenderMs: number | null;
    chartRoundTripMs: number | null;
    responseToRenderMs: number | null;
    chartPayloadBytes: number;
}

interface BenchmarkFailure {
    name: string;
    playerId: number;
    stage: 'route' | 'charts';
    message: string;
}

test.describe('live profile chart benchmark', () => {
    test.setTimeout(300000);

    test('loads 10 real player profile chart tabs efficiently', async ({ browser }) => {
        const benchmarkCandidates = await collectProfileBenchmarkCandidates();
        expect(benchmarkCandidates.length).toBeGreaterThanOrEqual(PLAYER_SAMPLE_SIZE);

        const measurements: PlayerMeasurement[] = [];
        const failures: BenchmarkFailure[] = [];

        for (const candidate of benchmarkCandidates) {
            if (measurements.length >= PLAYER_SAMPLE_SIZE) {
                break;
            }

            const page = await browser.newPage();
            let chartRequestCount = 0;
            let typeRequestCount = 0;
            let tierRequestCount = 0;
            let chartRequestStart = 0;
            let chartResponseEnd = 0;
            let chartPayloadBytes = 0;

            page.on('request', (request) => {
                const url = request.url();
                if (matchesPlayerEndpoint(url, `/api/fetch/player_correlation/tier_type/${candidate.playerId}`)) {
                    chartRequestCount += 1;
                    if (!chartRequestStart) {
                        chartRequestStart = performance.now();
                    }
                }

                if (matchesPlayerEndpoint(url, `/api/fetch/type_data/${candidate.playerId}`)) {
                    typeRequestCount += 1;
                }

                if (matchesPlayerEndpoint(url, `/api/fetch/tier_data/${candidate.playerId}`)) {
                    tierRequestCount += 1;
                }
            });

            page.on('response', async (response) => {
                const url = response.url();
                if (!matchesPlayerEndpoint(url, `/api/fetch/player_correlation/tier_type/${candidate.playerId}`)) {
                    return;
                }

                await response.finished();
                chartResponseEnd = performance.now();

                if (!chartPayloadBytes) {
                    const body = await response.text();
                    chartPayloadBytes = Buffer.byteLength(body, 'utf8');
                }
            });

            await page.route('**/api/**', async (route) => {
                const requestUrl = route.request().url();

                if (matchesPlayerEndpoint(requestUrl, `/api/player/${encodeURIComponent(candidate.name)}`)) {
                    await route.continue();
                    return;
                }

                if (matchesPlayerEndpoint(requestUrl, `/api/fetch/player_correlation/tier_type/${candidate.playerId}`)) {
                    await route.continue();
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
                await expect(page.getByRole('tab', { name: 'Profile' })).toHaveAttribute('aria-selected', 'true', { timeout: 30000 });
            } catch (error) {
                failures.push({
                    name: candidate.name,
                    playerId: candidate.playerId,
                    stage: 'route',
                    message: error instanceof Error ? error.message : String(error),
                });
                await page.close();
                continue;
            }

            try {
                await expect(page.getByText('Tier vs Type Profile')).toBeVisible({ timeout: 30000 });
                await expect(page.getByText('Performance by Ship Type')).toBeVisible({ timeout: 30000 });
                await expect(page.getByText('Performance by Tier')).toBeVisible({ timeout: 30000 });
                await expect.poll(async () => page.locator('#player-insights-panel-profile svg').count(), { timeout: 30000 }).toBeGreaterThanOrEqual(3);

                const renderEnd = performance.now();
                measurements.push({
                    ...candidate,
                    chartRequestCount,
                    typeRequestCount,
                    tierRequestCount,
                    routeToChartsMs: Number((renderEnd - routeStart).toFixed(2)),
                    chartRequestToRenderMs: chartRequestStart ? Number((renderEnd - chartRequestStart).toFixed(2)) : null,
                    chartRoundTripMs: chartRequestStart && chartResponseEnd ? Number((chartResponseEnd - chartRequestStart).toFixed(2)) : null,
                    responseToRenderMs: chartResponseEnd ? Number((renderEnd - chartResponseEnd).toFixed(2)) : null,
                    chartPayloadBytes,
                });
            } catch (error) {
                failures.push({
                    name: candidate.name,
                    playerId: candidate.playerId,
                    stage: 'charts',
                    message: error instanceof Error ? error.message : String(error),
                });
            } finally {
                await page.close();
            }
        }

        expect(measurements.length).toBe(PLAYER_SAMPLE_SIZE);

        const routeToChartsValues = measurements.map((entry) => entry.routeToChartsMs);
        const chartRequestToRenderValues = measurements
            .map((entry) => entry.chartRequestToRenderMs)
            .filter((value): value is number => value != null);
        const chartRoundTripValues = measurements
            .map((entry) => entry.chartRoundTripMs)
            .filter((value): value is number => value != null);
        const responseToRenderValues = measurements
            .map((entry) => entry.responseToRenderMs)
            .filter((value): value is number => value != null);
        const payloadBytesValues = measurements.map((entry) => entry.chartPayloadBytes);

        const summary = {
            sampledPlayers: measurements.length,
            routeToChartsMs: {
                avg: average(routeToChartsValues),
                median: percentile(routeToChartsValues, 0.5),
                p95: percentile(routeToChartsValues, 0.95),
                max: percentile(routeToChartsValues, 1),
            },
            chartRequestToRenderMs: {
                avg: average(chartRequestToRenderValues),
                median: percentile(chartRequestToRenderValues, 0.5),
                p95: percentile(chartRequestToRenderValues, 0.95),
                max: percentile(chartRequestToRenderValues, 1),
            },
            chartRoundTripMs: {
                avg: average(chartRoundTripValues),
                median: percentile(chartRoundTripValues, 0.5),
                p95: percentile(chartRoundTripValues, 0.95),
                max: percentile(chartRoundTripValues, 1),
            },
            responseToRenderMs: {
                avg: average(responseToRenderValues),
                median: percentile(responseToRenderValues, 0.5),
                p95: percentile(responseToRenderValues, 0.95),
                max: percentile(responseToRenderValues, 1),
            },
            chartPayloadBytes: {
                avg: average(payloadBytesValues),
                median: percentile(payloadBytesValues, 0.5),
                p95: percentile(payloadBytesValues, 0.95),
                max: percentile(payloadBytesValues, 1),
            },
        };

        const artifactPaths = writeBenchmarkArtifact({
            benchmarkName: 'profile-chart-performance-live',
            summary,
            measurements,
            failures,
            extraMetadata: {
                samplePoolSize: benchmarkCandidates.length,
                benchmarkSurface: 'player-profile-tab',
            },
        });

        console.log(`profile-chart-live-perf ${JSON.stringify({ artifactPaths, summary, players: measurements, failures })}`);

        expect(measurements.every((entry) => entry.chartRequestCount === 1)).toBeTruthy();
        expect(measurements.every((entry) => entry.typeRequestCount === 0)).toBeTruthy();
        expect(measurements.every((entry) => entry.tierRequestCount === 0)).toBeTruthy();
    });
});