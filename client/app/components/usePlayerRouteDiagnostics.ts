import { useEffect } from 'react';

const SECTION_RENDER_EVENT = 'player-route:section-rendered';
const MAX_RECORDS = 80;
const RECENT_RENDER_WINDOW_MS = 1500;

type SectionRenderMode = 'deferred' | 'immediate';

interface SectionRenderDetail {
    sectionId: string;
    playerId: number;
    mode: SectionRenderMode;
    timestamp: number;
}

interface LayoutShiftSourceLike {
    node?: Node | null;
}

interface LayoutShiftEntryLike extends PerformanceEntry {
    hadRecentInput: boolean;
    value: number;
    sources?: LayoutShiftSourceLike[];
}

interface LcpEntryLike extends PerformanceEntry {
    renderTime?: number;
    loadTime?: number;
}

interface LayoutShiftRecord {
    playerId: number;
    playerName: string;
    value: number;
    startTimeMs: number;
    sections: string[];
}

interface RouteDiagnosticsStore {
    sectionRenders: SectionRenderDetail[];
    layoutShifts: LayoutShiftRecord[];
    lcpMs: number | null;
}

declare global {
    interface Window {
        __playerRouteDiagnostics?: RouteDiagnosticsStore;
    }
}

const diagnosticsEnabled = (): boolean => {
    if (typeof window === 'undefined') {
        return false;
    }

    return process.env.NODE_ENV !== 'production' && window.location.hostname === 'localhost';
};

const pushRecord = <T,>(items: T[], item: T) => {
    items.push(item);
    if (items.length > MAX_RECORDS) {
        items.splice(0, items.length - MAX_RECORDS);
    }
};

const getStore = (): RouteDiagnosticsStore => {
    if (!window.__playerRouteDiagnostics) {
        window.__playerRouteDiagnostics = {
            sectionRenders: [],
            layoutShifts: [],
            lcpMs: null,
        };
    }

    return window.__playerRouteDiagnostics;
};

const getNearestSectionId = (node: Node | null): string | null => {
    if (!node) {
        return null;
    }

    const element = node instanceof Element ? node : node.parentElement;
    if (!element) {
        return null;
    }

    return element.closest('[data-perf-section]')?.getAttribute('data-perf-section') ?? null;
};

export const dispatchPlayerRouteSectionRendered = (
    sectionId: string,
    playerId: number,
    mode: SectionRenderMode,
) => {
    if (typeof window === 'undefined' || typeof window.CustomEvent === 'undefined' || typeof window.performance === 'undefined') {
        return;
    }

    window.dispatchEvent(new CustomEvent<SectionRenderDetail>(SECTION_RENDER_EVENT, {
        detail: {
            sectionId,
            playerId,
            mode,
            timestamp: window.performance.now(),
        },
    }));
};

export const usePlayerRouteDiagnostics = (playerId: number, playerName: string) => {
    useEffect(() => {
        if (!diagnosticsEnabled() || typeof PerformanceObserver === 'undefined' || typeof performance === 'undefined') {
            return;
        }

        const store = getStore();
        const label = `[player-route-perf:${playerName}]`;

        const handleRender = (event: Event) => {
            const detail = (event as CustomEvent<SectionRenderDetail>).detail;
            if (!detail || detail.playerId !== playerId) {
                return;
            }

            pushRecord(store.sectionRenders, detail);
            console.info(label, 'section render', detail.sectionId, detail.mode, Math.round(detail.timestamp));
        };

        const layoutObserver = new PerformanceObserver((entryList) => {
            for (const entry of entryList.getEntries() as LayoutShiftEntryLike[]) {
                if (entry.hadRecentInput || !entry.value) {
                    continue;
                }

                const sectionHits = (entry.sources ?? [])
                    .map((source) => getNearestSectionId(source.node ?? null))
                    .filter((sectionId): sectionId is string => Boolean(sectionId));
                const recentSectionHits = store.sectionRenders
                    .filter((render) => (
                        render.playerId === playerId
                        && entry.startTime >= render.timestamp
                        && entry.startTime - render.timestamp <= RECENT_RENDER_WINDOW_MS
                    ))
                    .map((render) => render.sectionId);
                const sections = Array.from(new Set([...sectionHits, ...recentSectionHits]));
                const record: LayoutShiftRecord = {
                    playerId,
                    playerName,
                    value: Number(entry.value.toFixed(4)),
                    startTimeMs: Number(entry.startTime.toFixed(1)),
                    sections,
                };

                pushRecord(store.layoutShifts, record);
                console.warn(label, 'layout shift', record);
            }
        });

        const lcpObserver = new PerformanceObserver((entryList) => {
            const entries = entryList.getEntries() as LcpEntryLike[];
            const lastEntry = entries[entries.length - 1];
            if (!lastEntry) {
                return;
            }

            const lcp = lastEntry.renderTime ?? lastEntry.loadTime ?? lastEntry.startTime;
            store.lcpMs = Number(lcp.toFixed(1));
        });

        window.addEventListener(SECTION_RENDER_EVENT, handleRender as EventListener);
        layoutObserver.observe({ type: 'layout-shift', buffered: true } as PerformanceObserverInit);
        lcpObserver.observe({ type: 'largest-contentful-paint', buffered: true } as PerformanceObserverInit);

        return () => {
            window.removeEventListener(SECTION_RENDER_EVENT, handleRender as EventListener);
            layoutObserver.disconnect();
            lcpObserver.disconnect();
        };
    }, [playerId, playerName]);
};