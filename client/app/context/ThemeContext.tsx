"use client";

import React, { createContext, useContext, useEffect, useState } from 'react';

export type Theme = 'light' | 'dark';

interface ThemeContextValue {
    theme: Theme;
    setTheme: (t: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
    theme: 'light',
    setTheme: () => undefined,
});

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [theme, setThemeState] = useState<Theme>('light');

    useEffect(() => {
        let initial: Theme = 'light';
        try {
            const stored = localStorage.getItem('bs-theme');
            if (stored === 'light' || stored === 'dark') {
                initial = stored;
            } else if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
                initial = 'dark';
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
