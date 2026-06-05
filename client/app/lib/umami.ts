// Thin, SSR-safe wrapper around Umami's custom-event API.
//
// Umami is injected only in layout.tsx behind an env flag, so `window.umami`
// may be absent (SSR, flag off, blocked). This wrapper no-ops in those cases so
// callers never have to guard, and it swallows any tracker error — analytics
// must never throw into the UI.
//
// Conventions: kebab-case event names; keep the property set small and
// low-cardinality (Umami event-data drives dashboard breakdowns, not
// high-cardinality lookups). Surface these in the Umami "Events" report.

type UmamiEventData = Record<string, string | number | boolean>;

declare global {
    interface Window {
        umami?: {
            track: (event: string, data?: UmamiEventData) => void;
        };
    }
}

export const trackEvent = (event: string, data?: UmamiEventData): void => {
    if (typeof window === 'undefined') return;
    try {
        window.umami?.track(event, data);
    } catch {
        // Never let analytics break the page.
    }
};
