import { buildClanPath, buildPlayerPath, parseClanIdFromRouteSegment, buildShipPath, parseShipIdFromRouteSegment } from '../entityRoutes';

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