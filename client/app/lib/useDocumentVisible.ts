import { useEffect, useState } from 'react';

// Tracks document visibility (Page Visibility API). SSR-safe: reports `true`
// until mounted, so server render + first paint assume a visible tab. Used to
// pause background polling when a tab is hidden and resume on focus.
export const useDocumentVisible = (): boolean => {
    const [visible, setVisible] = useState(true);

    useEffect(() => {
        if (typeof document === 'undefined') {
            return;
        }
        const update = () => setVisible(document.visibilityState !== 'hidden');
        update();
        document.addEventListener('visibilitychange', update);
        return () => document.removeEventListener('visibilitychange', update);
    }, []);

    return visible;
};
