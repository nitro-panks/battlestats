"use client";

import React, { createContext, useContext, useEffect, useState } from 'react';

export type Realm = 'na' | 'eu';

const VALID_REALMS: Realm[] = ['na', 'eu'];

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

    useEffect(() => {
        try {
            const urlRealm = new URLSearchParams(window.location.search).get('realm') as Realm | null;
            const stored = localStorage.getItem('bs-realm') as Realm | null;

            const initial = (urlRealm && VALID_REALMS.includes(urlRealm))
                ? urlRealm
                : (stored && VALID_REALMS.includes(stored) ? stored : null);

            if (initial) {
                setRealmState(initial);
                if (initial !== stored) {
                    try {
                        localStorage.setItem('bs-realm', initial);
                    } catch { }
                }
            }
        } catch {
            // localStorage unavailable
        }
    }, []);

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
