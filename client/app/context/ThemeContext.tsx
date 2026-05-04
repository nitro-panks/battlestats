"use client";

import React, { createContext, useContext, useEffect, useState } from 'react';

export type Theme = 'light' | 'dark';

interface ThemeContextValue {
    theme: Theme;
    setTheme: (t: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
    theme: 'dark',
    setTheme: () => undefined,
});

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [theme, setThemeState] = useState<Theme>('dark');

    useEffect(() => {
        // Dark is the implicit default. Users who explicitly toggle to
        // light persist that choice in localStorage; everyone else gets
        // dark, regardless of `prefers-color-scheme` (the OS-preference
        // fallback was removed because it surprised users whose OS was
        // light but who expected the site's branded dark look).
        let initial: Theme = 'dark';
        try {
            const stored = localStorage.getItem('bs-theme');
            if (stored === 'light' || stored === 'dark') {
                initial = stored;
            }
        } catch {
            // localStorage unavailable
        }
        setThemeState(initial);
        document.documentElement.dataset.theme = initial;
    }, []);

    const setTheme = (t: Theme) => {
        setThemeState(t);
        document.documentElement.dataset.theme = t;
        try {
            localStorage.setItem('bs-theme', t);
        } catch {
            // localStorage unavailable
        }
    };

    return (
        <ThemeContext.Provider value={{ theme, setTheme }}>
            {children}
        </ThemeContext.Provider>
    );
};

export const useTheme = (): ThemeContextValue => useContext(ThemeContext);
