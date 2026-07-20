'use client';

import { useEffect } from 'react';
import type { Realm } from '../context/RealmContext';

const REALM_LABELS: Record<Realm, string> = {
    na: 'NA',
    eu: 'EU',
    asia: 'ASIA',
};

const AUTO_DISMISS_MS = 6000;

// Shown when a player deep-link opened under the wrong realm was found in
// another realm and the app switched to it. Mirrors ConnectionHint's subtle
// pill; `aria-live="polite"` announces the switch to assistive tech. Auto-
// dismisses after a few seconds; also dismissable manually.
export default function RealmFallbackNotice({
    playerName,
    fromRealm,
    toRealm,
    onDismiss,
}: {
    playerName: string;
    fromRealm: Realm;
    toRealm: Realm;
    onDismiss: () => void;
}) {
    useEffect(() => {
        const timer = setTimeout(onDismiss, AUTO_DISMISS_MS);
        return () => clearTimeout(timer);
    }, [onDismiss]);

    return (
        <div
            role="status"
            aria-live="polite"
            className="mb-3 flex items-center justify-between gap-3 rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-1.5 text-xs text-[var(--text-secondary)]"
        >
            <span>
                <span className="font-semibold text-[var(--text-primary)]">{playerName}</span>
                {' '}isn&apos;t on {REALM_LABELS[fromRealm]} — showing{' '}
                <span className="font-semibold text-[var(--text-primary)]">{REALM_LABELS[toRealm]}</span>.
            </span>
            <button
                type="button"
                onClick={onDismiss}
                aria-label="Dismiss"
                className="shrink-0 rounded px-1 text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
            >
                ✕
            </button>
        </div>
    );
}
