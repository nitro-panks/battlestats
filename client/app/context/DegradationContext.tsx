'use client';

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { degradationMonitor, type DegradationMode } from '../lib/degradationMonitor';

const DegradationContext = createContext<DegradationMode>('normal');

export const useDegradationMode = (): DegradationMode => useContext(DegradationContext);

// Starts the degradation monitor (registers it as the fetch telemetry sink) and
// re-renders consumers when the mode flips. Mounted once near the app root.
export const DegradationProvider = ({ children }: { children: ReactNode }) => {
    const [mode, setMode] = useState<DegradationMode>('normal');

    useEffect(() => {
        degradationMonitor.start();
        setMode(degradationMonitor.getMode());
        return degradationMonitor.subscribe(setMode);
    }, []);

    return <DegradationContext.Provider value={mode}>{children}</DegradationContext.Provider>;
};
