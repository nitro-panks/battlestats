type EntityType = 'player' | 'clan';

interface TrackEntityDetailViewInput {
    entityType: EntityType;
    entityId: number;
    entityName: string;
    entitySlug: string;
}

const ANALYTICS_ENDPOINT = '/api/analytics/entity-view';
const VISITOR_COOKIE_NAME = 'battlestats_visitor_key';
const SESSION_STORAGE_KEY = 'battlestats_session_key';
const VISITOR_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;

const generateId = (): string => {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID();
    }

    return `visit-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

const readCookie = (cookieName: string): string | null => {
    if (typeof document === 'undefined') {
        return null;
    }

    const cookiePrefix = `${cookieName}=`;
    const cookie = document.cookie
        .split(';')
        .map((value) => value.trim())
        .find((value) => value.startsWith(cookiePrefix));

    if (!cookie) {
        return null;
    }

    return decodeURIComponent(cookie.slice(cookiePrefix.length));
};

const writeCookie = (cookieName: string, value: string, maxAgeSeconds: number) => {
    if (typeof document === 'undefined') {
        return;
    }

    document.cookie = `${cookieName}=${encodeURIComponent(value)}; Max-Age=${maxAgeSeconds}; Path=/; SameSite=Lax`;
};

const getOrCreateVisitorKey = (): string => {
    const existingValue = readCookie(VISITOR_COOKIE_NAME);
    if (existingValue) {
        return existingValue;
    }

    const newValue = generateId();
    writeCookie(VISITOR_COOKIE_NAME, newValue, VISITOR_COOKIE_MAX_AGE_SECONDS);
    return newValue;
};

const getOrCreateSessionKey = (): string => {
    if (typeof window === 'undefined') {
        return generateId();
    }

    const existingValue = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (existingValue) {
        return existingValue;
    }

    const newValue = generateId();
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, newValue);
    return newValue;
};

const getSameOriginReferrerPath = (): string => {
    if (typeof document === 'undefined' || typeof window === 'undefined' || !document.referrer) {
        return '';
    }

    try {
        const referrerUrl = new URL(document.referrer);
        if (referrerUrl.origin !== window.location.origin) {
            return '';
        }

        return `${referrerUrl.pathname}${referrerUrl.search}`;
    } catch {
        return '';
    }
};

export const trackEntityDetailView = async ({
    entityType,
    entityId,
    entityName,
    entitySlug,
}: TrackEntityDetailViewInput): Promise<void> => {
    if (typeof window === 'undefined') {
        return;
    }

    const payload = {
        event_uuid: generateId(),
        occurred_at: new Date().toISOString(),
        entity_type: entityType,
        entity_id: entityId,
        entity_slug: entitySlug,
        entity_name: entityName,
        route_path: `${window.location.pathname}${window.location.search}`,
        referrer_path: getSameOriginReferrerPath(),
        source: 'web_first_party',
        visitor_key: getOrCreateVisitorKey(),
        session_key: getOrCreateSessionKey(),
    };

    try {
        await fetch(ANALYTICS_ENDPOINT, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
            keepalive: true,
        });
    } catch (error) {
        console.warn('Failed to track entity detail view:', error);
    }
};