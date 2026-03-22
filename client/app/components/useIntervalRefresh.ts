import { useEffect, useRef } from 'react';

export const useIntervalRefresh = (
    callback: () => void,
    intervalMs: number,
    enabled: boolean = true,
) => {
    const callbackRef = useRef(callback);

    useEffect(() => {
        callbackRef.current = callback;
    }, [callback]);

    useEffect(() => {
        if (!enabled) {
            return;
        }

        const intervalId = window.setInterval(() => {
            callbackRef.current();
        }, intervalMs);

        return () => window.clearInterval(intervalId);
    }, [enabled, intervalMs]);
};

export default useIntervalRefresh;