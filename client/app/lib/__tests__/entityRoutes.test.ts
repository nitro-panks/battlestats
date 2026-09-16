import { buildClanPath, buildPlayerPath, parseClanIdFromRouteSegment, buildShipPath, parseShipIdFromRouteSegment, SHIP_BUCKET_TIERS, SHIP_TYPES, buildShipBucketSegment, parseShipBucketSegment, allShipBucketSegments, buildShipBucketPath, parseWrPctParam, shipBucketLabel, isShiplessBucket } from '../entityRoutes';

describe('entityRoutes', () => {
    it('builds player paths with trimmed encoded names and optional realm', () => {
        expect(buildPlayerPath('  John Doe  ')).toBe('/player/John%20Doe');
        expect(buildPlayerPath('A/B')).toBe('/player/A%2FB');
        expect(buildPlayerPath('John Doe', 'eu')).toBe('/player/John%20Doe?realm=eu');
    });

    it('builds clan paths with slugified names and optional realm', () => {
        expect(buildClanPath(1000067803, 'The "Best" Clan')).toBe('/clan/1000067803-the-best-clan');
        expect(buildClanPath('1000067803', '   ')).toBe('/clan/1000067803');
        expect(buildClanPath(1000067803, 'The "Best" Clan', 'eu')).toBe('/clan/1000067803-the-best-clan?realm=eu');
    });

    it('parses clan ids from route segments', () => {
        expect(parseClanIdFromRouteSegment('1000067803-the-best-clan')).toBe(1000067803);
        expect(parseClanIdFromRouteSegment('1000067803')).toBe(1000067803);
        expect(parseClanIdFromRouteSegment('not-a-clan')).toBeNull();
        expect(parseClanIdFromRouteSegment('0-bad')).toBeNull();
    });

    it('builds ship paths with slugified names and optional realm', () => {
        expect(buildShipPath(3751340016, 'Shimakaze')).toBe('/ship/3751340016-shimakaze');
        expect(buildShipPath('3751340016', '   ')).toBe('/ship/3751340016');
        expect(buildShipPath(3751340016, 'Île de France', 'eu')).toBe('/ship/3751340016-le-de-france?realm=eu');
    });

    it('parses ship ids from route segments', () => {
        expect(parseShipIdFromRouteSegment('3751340016-shimakaze')).toBe(3751340016);
        expect(parseShipIdFromRouteSegment('3751340016')).toBe(3751340016);
        expect(parseShipIdFromRouteSegment('not-a-ship')).toBeNull();
        expect(parseShipIdFromRouteSegment('0-bad')).toBeNull();
    });
});

describe('ship bucket segments', () => {
    it('round-trips every tier x type combination', () => {
        for (const tier of SHIP_BUCKET_TIERS) {
            for (const type of SHIP_TYPES) {
                const segment = buildShipBucketSegment(tier, type);
                expect(parseShipBucketSegment(segment)).toEqual({ tier, type });
            }
        }
    });

    it('computes tier 11, the supership tier', () => {
        expect(SHIP_BUCKET_TIERS).toContain(11);
        expect(parseShipBucketSegment('t11-battleships')).toEqual({ tier: 11, type: 'Battleship' });
        expect(shipBucketLabel(11, 'Cruiser')).toBe('T11 Cruisers');
    });

    it('knows which buckets the game has no hulls for', () => {
        // Carriers are even-tier only; neither T9 nor T11 has a submarine.
        expect(isShiplessBucket(9, 'AirCarrier')).toBe(true);
        expect(isShiplessBucket(9, 'Submarine')).toBe(true);
        expect(isShiplessBucket(11, 'Submarine')).toBe(true);
        expect(isShiplessBucket(11, 'AirCarrier')).toBe(false);
        expect(isShiplessBucket(10, 'Submarine')).toBe(false);
    });

    it('keeps shipless buckets out of the sitemap but still parseable', () => {
        const segments = allShipBucketSegments();
        expect(segments).not.toContain('t9-submarines');
        expect(segments).not.toContain('t9-carriers');
        expect(segments).not.toContain('t11-submarines');
        expect(segments).toContain('t11-battleships');
        expect(segments).toContain('t11-carriers');
        // 4 tiers x 5 types, less the 3 buckets with no hulls in the game.
        expect(segments).toHaveLength(SHIP_BUCKET_TIERS.length * SHIP_TYPES.length - 3);
        // The route itself still resolves, so a typed URL is not a 404.
        expect(parseShipBucketSegment('t11-submarines')).toEqual({ tier: 11, type: 'Submarine' });
    });

    it('slugs AirCarrier as "carriers", not "aircarriers"', () => {
        expect(buildShipBucketSegment(10, 'AirCarrier')).toBe('t10-carriers');
        expect(parseShipBucketSegment('t10-carriers')).toEqual({ tier: 10, type: 'AirCarrier' });
    });

    it('accepts an uppercased segment', () => {
        expect(parseShipBucketSegment('T9-Destroyers')).toEqual({ tier: 9, type: 'Destroyer' });
    });

    it.each([
        ['t7-battleships', 'a tier we do not compute'],
        ['t10-frigates', 'an unknown hull type'],
        ['battleships', 'a missing tier'],
        ['t10', 'a missing type'],
        ['t10-battleships-extra', 'trailing junk'],
        ['', 'an empty segment'],
    ])('rejects %s (%s)', (segment) => {
        expect(parseShipBucketSegment(segment)).toBeNull();
    });

    it('enumerates every indexable bucket for the sitemap, with no duplicates', () => {
        const segments = allShipBucketSegments();
        const indexable = SHIP_BUCKET_TIERS.length * SHIP_TYPES.length - 3;
        expect(segments).toHaveLength(indexable);
        expect(new Set(segments).size).toBe(indexable);
    });
});


describe('buildShipBucketPath', () => {
    it('always emits realm and wr so neither falls back to the recipient prefs', () => {
        expect(buildShipBucketPath({ tier: 10, type: 'Battleship', realm: 'na', wrPct: 50 })).toBe(
            '/ships/t10-battleships?realm=na&wr=50',
        );
    });

    it('emits wr=all for the realm-wide aggregate', () => {
        expect(buildShipBucketPath({ tier: 8, type: 'Destroyer', realm: 'eu', wrPct: null })).toBe(
            '/ships/t8-destroyers?realm=eu&wr=all',
        );
    });

    it('carries an active column sort', () => {
        expect(
            buildShipBucketPath({
                tier: 9,
                type: 'Cruiser',
                realm: 'asia',
                wrPct: 25,
                sort: 'avg_damage',
                dir: 'desc',
            }),
        ).toBe('/ships/t9-cruisers?realm=asia&wr=25&sort=avg_damage&dir=desc');
    });

    it('omits sort entirely for the natural server order', () => {
        const path = buildShipBucketPath({
            tier: 10,
            type: 'Submarine',
            realm: 'na',
            wrPct: null,
            sort: null,
        });
        expect(path).not.toContain('sort=');
    });
});


describe('parseWrPctParam', () => {
    it.each([
        ['50', 50],
        ['25', 25],
        ['all', null],
        ['', null],
        [null, null],
        ['80', null],
    ])('parses %s', (input, expected) => {
        expect(parseWrPctParam(input as string | null)).toBe(expected);
    });
});


describe('shipBucketLabel', () => {
    it('reads as a title', () => {
        expect(shipBucketLabel(10, 'Battleship')).toBe('T10 Battleships');
        expect(shipBucketLabel(9, 'AirCarrier')).toBe('T9 Carriers');
    });
});
