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