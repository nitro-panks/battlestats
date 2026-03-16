const slugifySegment = (value: string): string => {
    return value
        .trim()
        .toLowerCase()
        .replace(/["']/g, '')
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
};


export const buildPlayerPath = (playerName: string): string => {
    return `/player/${encodeURIComponent(playerName.trim())}`;
};


export const buildClanPath = (clanId: number | string, clanName?: string): string => {
    const normalizedId = String(clanId).trim();
    const slug = slugifySegment(clanName || '');
    return slug ? `/clan/${normalizedId}-${slug}` : `/clan/${normalizedId}`;
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