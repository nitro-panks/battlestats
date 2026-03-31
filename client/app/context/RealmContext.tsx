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
            const stored = localStorage.getItem('bs-realm') as Realm | null;
            if (stored && VALID_REALMS.includes(stored)) {
                setRealmState(stored);
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
