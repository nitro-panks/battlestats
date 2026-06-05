"use client";

import React, { createContext, useContext, useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';

export type Realm = 'na' | 'eu' | 'asia';

const VALID_REALMS: Realm[] = ['na', 'eu', 'asia'];

interface RealmContextValue {
    realm: Realm;
    setRealm: (r: Realm) => void;
}

const RealmContext = createContext<RealmContextValue>({
    realm: 'na',
    setRealm: () => undefined,
});

export const RealmProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [realm, setRealmState] = useState<Realm>('na');
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

    const setRealm = (r: Realm) => {
        setRealmState(r);
        try {
            localStorage.setItem('bs-realm', r);
        } catch {
            // localStorage unavailable
        }
    };

    return (
        <RealmContext.Provider value={{ realm, setRealm }}>
            {children}
        </RealmContext.Provider>
    );
};

export const useRealm = (): RealmContextValue => useContext(RealmContext);
