import { appendFileSync, mkdirSync, writeFileSync } from 'fs';
import { join, resolve } from 'path';

export const APP_ORIGIN = (process.env.BATTLESTATS_APP_ORIGIN ?? process.env.PLAYWRIGHT_EXTERNAL_BASE_URL ?? 'http://127.0.0.1:3100').replace(/\/$/, '');
export const API_ORIGIN = (process.env.BATTLESTATS_API_ORIGIN ?? 'http://localhost:8888').replace(/\/$/, '');
export const PLAYER_SAMPLE_SIZE = 10;
export const PLAYER_POOL_SIZE = 20;
export const PLAYER_SCAN_LIMIT = 40;

export const SEEDED_PLAYER_NAMES = [
    'Black_Magician',
    'Rusty_Bucket__',
    'Lapplandhex',
    'Ivane',
    'xzerosangel',
    'GoldRush21',
    'senorange',
    'Umbrellarduu',
    'ffael',
    'zaiko_2016_steel',
    'Aquilam',
    'BullSomicTree1',
    'dj_dan92',
    'Hensen',
    'Squid69lips',
    'jorwann97',
    'Staff0369',
    'Harvey_Birdman_07',
    'SubRMC',
    'Garrick40',
];

interface LandingPlayer {
    name: string;
    is_hidden?: boolean;
}

interface PlayerDetail {
    name: string;
    player_id: number;
    pvp_battles?: number | null;
}

interface TierTypePayload {
    player_cells?: Array<unknown>;
    tiles?: Array<unknown>;
    trend?: Array<unknown>;
}

export interface BenchmarkCandidate {
    name: string;
    playerId: number;
    pvpBattles: number;
    playerCellCount: number;
    tileCount: number;
    trendCount: number;
}

interface BenchmarkMetadata {
    benchmarkName: string;
    capturedAt: string;
    timestampKey: string;
    source: 'github-actions' | 'local';
    apiOrigin: string;
    gitSha: string | null;
    runId: string | null;
    runAttempt: string | null;
}

interface WriteBenchmarkArtifactOptions<TSummary, TMeasurement, TFailure> {
    benchmarkName: string;
    summary: TSummary;
    measurements: TMeasurement[];
    failures: TFailure[];
    extraMetadata?: Record<string, unknown>;
}

export const percentile = (values: number[], ratio: number): number | null => {
    if (!values.length) {
        return null;
    }

    const sorted = [...values].sort((left, right) => left - right);
    const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * ratio) - 1));
    return Number(sorted[index].toFixed(2));
};

export const average = (values: number[]): number | null => {
    if (!values.length) {
        return null;
    }

    const total = values.reduce((sum, value) => sum + value, 0);
    return Number((total / values.length).toFixed(2));
};

export const matchesPlayerEndpoint = (url: string, endpointPath: string): boolean => {
    return url.includes(endpointPath) || url.includes(`${endpointPath}/`);
};

export const fetchJson = async <T>(url: string): Promise<T> => {
    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`Request failed for ${url}: ${response.status}`);
    }

    return response.json() as Promise<T>;
};

export const shuffleArray = <T>(values: T[]): T[] => {
    const copy = [...values];
    for (let index = copy.length - 1; index > 0; index -= 1) {
        const swapIndex = Math.floor(Math.random() * (index + 1));
        [copy[index], copy[swapIndex]] = [copy[swapIndex], copy[index]];
    }

    return copy;
};

export const collectLandingPlayerNames = async (): Promise<string[]> => {
    const urls = [
        `${API_ORIGIN}/api/landing/players/`,
        `${API_ORIGIN}/api/landing/players/?mode=best`,
        `${API_ORIGIN}/api/landing/players/?mode=sigma`,
    ];
    const names = new Set<string>();

    for (const url of urls) {
        try {
            const payload = await fetchJson<Array<LandingPlayer>>(url);
            payload.forEach((player) => {
                const name = (player?.name || '').trim();
                if (name) {
                    names.add(name);
                }
            });
        } catch {
            continue;
        }
    }

    return [...names];
};

export const collectProfileBenchmarkCandidates = async (): Promise<BenchmarkCandidate[]> => {
    const candidates: BenchmarkCandidate[] = [];
    const seenNames = new Set<string>();

    let landingPlayers: LandingPlayer[] = [];
    try {
        landingPlayers = await fetchJson<LandingPlayer[]>(`${API_ORIGIN}/api/landing/players/`);
    } catch {
        landingPlayers = [];
    }

    const orderedNames = [
        ...SEEDED_PLAYER_NAMES,
        ...landingPlayers
            .map((player) => (player.name || '').trim())
            .filter((name) => Boolean(name))
            .slice(0, PLAYER_SCAN_LIMIT),
    ];

    for (const playerName of orderedNames) {
        if (candidates.length >= PLAYER_POOL_SIZE) {
            break;
        }

        if (!playerName || seenNames.has(playerName)) {
            continue;
        }

        seenNames.add(playerName);

        let playerDetail: PlayerDetail;
        try {
            playerDetail = await fetchJson<PlayerDetail>(`${API_ORIGIN}/api/player/${encodeURIComponent(playerName)}/`);
        } catch {
            continue;
        }

        if (!playerDetail?.player_id) {
            continue;
        }

        let tierTypePayload: TierTypePayload;
        try {
            tierTypePayload = await fetchJson<TierTypePayload>(`${API_ORIGIN}/api/fetch/player_correlation/tier_type/${playerDetail.player_id}/`);
        } catch {
            continue;
        }

        const playerCellCount = Array.isArray(tierTypePayload.player_cells) ? tierTypePayload.player_cells.length : 0;
        if (playerCellCount === 0) {
            continue;
        }

        candidates.push({
            name: playerDetail.name,
            playerId: playerDetail.player_id,
            pvpBattles: playerDetail.pvp_battles ?? 0,
            playerCellCount,
            tileCount: Array.isArray(tierTypePayload.tiles) ? tierTypePayload.tiles.length : 0,
            trendCount: Array.isArray(tierTypePayload.trend) ? tierTypePayload.trend.length : 0,
        });
    }

    return candidates;
};

const buildBenchmarkMetadata = (benchmarkName: string): BenchmarkMetadata => {
    const capturedAt = new Date().toISOString();
    const runId = process.env.GITHUB_RUN_ID ?? null;
    const runAttempt = process.env.GITHUB_RUN_ATTEMPT ?? null;
    const timestampKey = (process.env.BATTLESTATS_BENCHMARK_TIMESTAMP ?? capturedAt)
        .replace(/[:.]/g, '-')
        .replace(/[^A-Za-z0-9_-]/g, '-');

    return {
        benchmarkName,
        capturedAt,
        timestampKey,
        source: process.env.GITHUB_ACTIONS ? 'github-actions' : 'local',
        apiOrigin: API_ORIGIN,
        gitSha: process.env.GITHUB_SHA ?? null,
        runId,
        runAttempt,
    };
};

export const writeBenchmarkArtifact = <TSummary, TMeasurement, TFailure>({
    benchmarkName,
    summary,
    measurements,
    failures,
    extraMetadata = {},
}: WriteBenchmarkArtifactOptions<TSummary, TMeasurement, TFailure>) => {
    const metadata = buildBenchmarkMetadata(benchmarkName);
    const clientRoot = process.cwd();
    const repoRoot = resolve(clientRoot, '..');
    const latestArtifactPath = join(clientRoot, 'test-results', 'playwright', 'benchmarks', `${benchmarkName}.json`);
    const trendRoot = join(repoRoot, 'logs', 'benchmarks', 'client');
    const trendSnapshotPath = join(trendRoot, benchmarkName, `${metadata.timestampKey}.json`);
    const historyPath = join(trendRoot, 'history', `${benchmarkName}.jsonl`);
    const artifactPayload = {
        metadata: {
            ...metadata,
            ...extraMetadata,
        },
        summary,
        measurements,
        failures,
    };
    const historyLine = JSON.stringify({
        metadata: {
            ...metadata,
            ...extraMetadata,
        },
        summary,
        measurementCount: measurements.length,
        failureCount: failures.length,
        snapshotPath: trendSnapshotPath,
    });

    mkdirSync(resolve(latestArtifactPath, '..'), { recursive: true });
    mkdirSync(resolve(trendSnapshotPath, '..'), { recursive: true });
    mkdirSync(resolve(historyPath, '..'), { recursive: true });

    writeFileSync(latestArtifactPath, JSON.stringify(artifactPayload, null, 2));
    writeFileSync(trendSnapshotPath, JSON.stringify(artifactPayload, null, 2));
    appendFileSync(historyPath, `${historyLine}\n`);

    return {
        latestArtifactPath,
        trendSnapshotPath,
        historyPath,
    };
};