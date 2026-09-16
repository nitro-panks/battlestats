const slugifySegment = (value: string): string => {
    return value
        .trim()
        .toLowerCase()
        .replace(/["']/g, '')
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
};


export const buildPlayerPath = (playerName: string, realm?: string): string => {
    const base = `/player/${encodeURIComponent(playerName.trim())}`;
    return realm ? `${base}?realm=${realm}` : base;
};


export const buildClanPath = (clanId: number | string, clanName?: string, realm?: string): string => {
    const normalizedId = String(clanId).trim();
    const slug = slugifySegment(clanName || '');
    const base = slug ? `/clan/${normalizedId}-${slug}` : `/clan/${normalizedId}`;
    return realm ? `${base}?realm=${realm}` : base;
};


export const parseClanIdFromRouteSegment = (segment: string): number | null => {
    const match = segment.match(/^(\d+)/);
    if (!match) {
        return null;
    }

    const clanId = Number(match[1]);
    if (!Number.isInteger(clanId) || clanId <= 0) {
        return null;
    }

    return clanId;
};


export const buildShipPath = (shipId: number | string, shipName?: string, realm?: string): string => {
    const normalizedId = String(shipId).trim();
    const slug = slugifySegment(shipName || '');
    const base = slug ? `/ship/${normalizedId}-${slug}` : `/ship/${normalizedId}`;
    return realm ? `${base}?realm=${realm}` : base;
};


export const parseShipIdFromRouteSegment = (segment: string): number | null => {
    const match = segment.match(/^(\d+)/);
    if (!match) {
        return null;
    }

    const shipId = Number(match[1]);
    if (!Number.isInteger(shipId) || shipId <= 0) {
        return null;
    }

    return shipId;
};

// ---------------------------------------------------------------------------
// Ship-leaderboard buckets (/ships/t10-battleships)
//
// The landing ship leaderboard is a tier x type bucket with two view-state
// axes (win-rate percentile, column sort). The bucket itself goes in the path
// because those 15 combinations are worth indexing on their own ("best t10
// battleships wows" is a real query); view state stays in the query string.
//
// Precedence rule: on /ships/<bucket> the URL is the WHOLE truth. localStorage
// is neither read nor written there, so an absent `sort` means the server's
// natural order rather than the recipient's remembered column. Without that,
// a shared link silently renders the recipient's view instead of the sharer's
// -- the exact failure the share button exists to prevent. The landing page
// keeps its localStorage behaviour untouched.
//
// Runbook: agents/runbooks/runbook-shareable-ship-leaderboard-2026-08-20.md
// ---------------------------------------------------------------------------

/**
 * Tiers the backend computes ship-standings data for. Must stay in step with
 * `SHIP_BADGE_TIERS` on the server (`server/deploy/deploy_to_droplet.sh`): a
 * tier listed here but absent there makes the bucket endpoint 400.
 *
 * Tier 11 is Wargaming's supership tier; it ranks as an ordinary tier because
 * every ship is ranked within its own pool (depth study:
 * `agents/runbooks/runbook-ship-standings-tier-extension-2026-09-15.md`).
 */
export const SHIP_BUCKET_TIERS = [8, 9, 10, 11] as const;
export type Tier = (typeof SHIP_BUCKET_TIERS)[number];

/** Raw `Ship.ship_type` strings the backend filters on (note: "AirCarrier"). */
export const SHIP_TYPES = ['Battleship', 'Cruiser', 'Destroyer', 'AirCarrier', 'Submarine'] as const;
export type ShipType = (typeof SHIP_TYPES)[number];

/**
 * Buckets World of Warships has no hulls for, so no data can ever exist:
 * carriers are even-tier only (no T9 CV), and neither T9 nor T11 has a
 * submarine. These must issue NO fetch and stay out of the sitemap — an empty
 * indexable page is worse than no page.
 *
 * This is the single source of truth. Before it existed the knowledge lived as
 * `tier === 9` inside `ShipLeaderboard.tsx`, which is why adding a tier meant
 * remembering to look there.
 */
const SHIPLESS_BUCKETS: ReadonlySet<string> = new Set([
    '9|AirCarrier',
    '9|Submarine',
    '11|Submarine',
]);

/** True when the game has no hulls at all for this bucket. */
export const isShiplessBucket = (tier: Tier, type: ShipType): boolean =>
    SHIPLESS_BUCKETS.has(`${tier}|${type}`);

/** Win-rate-percentile filter; `null` is the realm-wide aggregate ("All"). */
export type WrPct = 50 | 25 | null;

// Plural, lowercase, and readable in a URL. "AirCarrier" deliberately becomes
// "carriers" rather than "aircarriers" -- the slug is a public surface.
const TYPE_TO_SLUG: Record<ShipType, string> = {
    Battleship: 'battleships',
    Cruiser: 'cruisers',
    Destroyer: 'destroyers',
    AirCarrier: 'carriers',
    Submarine: 'submarines',
};

const SLUG_TO_TYPE: Record<string, ShipType> = Object.fromEntries(
    (Object.entries(TYPE_TO_SLUG) as [ShipType, string][]).map(([type, slug]) => [slug, type]),
);

/** Human label for a bucket, e.g. "T10 Battleships". */
export const shipBucketLabel = (tier: Tier, type: ShipType): string =>
    `T${tier} ${TYPE_TO_SLUG[type].replace(/^\w/, (c) => c.toUpperCase())}`;

/** The path segment for a bucket, e.g. "t10-battleships". */
export const buildShipBucketSegment = (tier: Tier, type: ShipType): string =>
    `t${tier}-${TYPE_TO_SLUG[type]}`;

/**
 * Parse a bucket segment back to its axes. Returns null for anything malformed
 * so the route can 404 rather than fetch a garbage bucket.
 */
export const parseShipBucketSegment = (
    segment: string,
): { tier: Tier; type: ShipType } | null => {
    const match = (segment ?? '').toLowerCase().match(/^t(\d+)-([a-z]+)$/);
    if (!match) {
        return null;
    }

    const tier = Number(match[1]) as Tier;
    if (!SHIP_BUCKET_TIERS.includes(tier)) {
        return null;
    }

    const type = SLUG_TO_TYPE[match[2]];
    if (!type) {
        return null;
    }

    return { tier, type };
};

/**
 * Every bucket segment worth indexing, for the sitemap. Shipless buckets are
 * excluded: the route still resolves (a visitor who types it gets the "no hulls"
 * branch), it simply is not advertised.
 */
export const allShipBucketSegments = (): string[] =>
    SHIP_BUCKET_TIERS.flatMap((tier) =>
        SHIP_TYPES
            .filter((type) => !isShiplessBucket(tier, type))
            .map((type) => buildShipBucketSegment(tier, type)));

export interface ShipBucketView {
    tier: Tier;
    type: ShipType;
    realm: string;
    wrPct: WrPct;
    /** Column key; omitted/null means the server's natural order. */
    sort?: string | null;
    dir?: 'asc' | 'desc' | null;
}

/**
 * The full shareable URL for a bucket view. `realm` and `wr` are always
 * emitted -- both have non-obvious defaults that differ per visitor, and a
 * realm-blind link shows a different realm's numbers entirely.
 */
export const buildShipBucketPath = ({ tier, type, realm, wrPct, sort, dir }: ShipBucketView): string => {
    const params = new URLSearchParams({
        realm,
        wr: wrPct === null ? 'all' : String(wrPct),
    });
    if (sort) {
        params.set('sort', sort);
        if (dir) {
            params.set('dir', dir);
        }
    }
    return `/ships/${buildShipBucketSegment(tier, type)}?${params.toString()}`;
};

/** Parse the `wr` query param. Anything unrecognised falls back to "All". */
export const parseWrPctParam = (value: string | null | undefined): WrPct => {
    if (value === '50') return 50;
    if (value === '25') return 25;
    return null;
};
