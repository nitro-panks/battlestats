"use client";

import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';

export type Realm = 'na' | 'eu' | 'asia';

const VALID_REALMS: Realm[] = ['na', 'eu', 'asia'];

interface RealmContextValue {
    realm: Realm;
    setRealm: (r: Realm) => void;
    // Monotonic counter bumped whenever the realm is switched *automatically*
    // (cross-realm player fallback), NOT on ordinary manual switches. The realm
    // selector watches it to flash a one-shot "your realm just changed" cue.
    autoSwitchSignal: number;
    notifyRealmAutoSwitch: () => void;
}

const RealmContext = createContext<RealmContextValue>({
    realm: 'na',
    setRealm: () => undefined,
    autoSwitchSignal: 0,
    notifyRealmAutoSwitch: () => undefined,
});

// Resolve the realm synchronously at first render from the same precedence the
// navigation effect uses (explicit ?realm= wins, else the stored preference).
// SSR has no window, so it falls back to 'na' there — the only place that then
// differs from the client's first render is realm-dependent TEXT in the
// statically-rendered shell (the realm selector label + the landing treemap
// heading), which carry `suppressHydrationWarning`. Data fetches read this
// resolved value, so a bare ?realm=-less entity link now fetches the stored
// realm on the FIRST request instead of hitting the 'na' default and refetching
// (which could flash "not found" for an EU/ASIA-only player).
const resolveInitialRealm = (): Realm => {
    if (typeof window === 'undefined') {
        return 'na';
    }
    try {
        const urlRealm = new URLSearchParams(window.location.search).get('realm') as Realm | null;
        if (urlRealm && VALID_REALMS.includes(urlRealm)) {
            return urlRealm;
        }
        const stored = localStorage.getItem('bs-realm') as Realm | null;
        if (stored && VALID_REALMS.includes(stored)) {
            return stored;
        }
    } catch {
        // URL / localStorage unavailable
    }
    return 'na';
};

export const RealmProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [realm, setRealmState] = useState<Realm>(resolveInitialRealm);
    const [autoSwitchSignal, setAutoSwitchSignal] = useState(0);
    const pathname = usePathname();

    // Resolve the realm on first mount AND on every client-side navigation
    // (keyed on `pathname`). An explicit, valid `?realm=` in the URL wins and is
    // persisted; otherwise the last stored realm is restored. Previously this ran
    // once (empty deps), so a Link carrying `?realm=` (e.g. the footer's na-only
    // links) was ignored until a full-page refresh — landing on the wrong realm
    // and 404ing the player. `usePathname` (not `useSearchParams`) avoids forcing
    // a Suspense boundary on the statically-rendered routes under this provider.
    useEffect(() => {
        try {
            const urlRealm = new URLSearchParams(window.location.search).get('realm') as Realm | null;
            if (urlRealm && VALID_REALMS.includes(urlRealm)) {
                setRealmState(urlRealm);
                try {
                    localStorage.setItem('bs-realm', urlRealm);
                } catch { }
                return;
            }
            const stored = localStorage.getItem('bs-realm') as Realm | null;
            if (stored && VALID_REALMS.includes(stored)) {
                setRealmState(stored);
            }
        } catch {
            // localStorage / URL unavailable
        }
    }, [pathname]);

    // Stable identities (useCallback) so consumers can safely list them in
    // effect deps without re-running on every provider render.
    const setRealm = useCallback((r: Realm) => {
        setRealmState(r);
        try {
            localStorage.setItem('bs-realm', r);
        } catch {
            // localStorage unavailable
        }
    }, []);

    // Signals that the realm was just switched automatically (cross-realm player
    // fallback). Kept separate from setRealm so a manual realm change never
    // triggers the selector flash.
    const notifyRealmAutoSwitch = useCallback(() => setAutoSwitchSignal((n) => n + 1), []);

    return (
        <RealmContext.Provider value={{ realm, setRealm, autoSwitchSignal, notifyRealmAutoSwitch }}>
            {children}
        </RealmContext.Provider>
    );
};

export const useRealm = (): RealmContextValue => useContext(RealmContext);

// Realm for RENDERED TEXT in the statically-prerendered shell (the realm
// selector label, the landing treemap heading). The live `realm` is resolved
// from localStorage at first client render, which the server can't know, so
// rendering it directly would mismatch the SSG 'na' default. This returns the
// SSR-safe default until mounted, then the real realm — so the first client
// render matches the server HTML (no hydration mismatch) and the label settles
// a tick later. Data fetches should keep using `useRealm()` (resolved
// synchronously) — only attribute/text rendered during SSR needs this.
export const useDisplayRealm = (): Realm => {
    const { realm } = useRealm();
    const [mounted, setMounted] = useState(false);
    useEffect(() => setMounted(true), []);
    return mounted ? realm : 'na';
};
