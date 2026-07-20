'use client';

import { useEffect } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faTriangleExclamation } from '@fortawesome/free-solid-svg-icons';
import type { Realm } from '../context/RealmContext';

const WARNING_ORANGE = '#f59e0b';

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
            className="realm-fallback-notice fixed right-4 left-4 top-4 z-50 flex items-center justify-between gap-3 rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] px-4 py-5 text-base text-[var(--text-secondary)] shadow-lg sm:left-auto sm:max-w-sm"
        >
            <span className="flex items-center gap-3">
                <FontAwesomeIcon
                    icon={faTriangleExclamation}
                    aria-hidden="true"
                    style={{ color: WARNING_ORANGE, fontSize: '22px' }}
                />
                <span>
                    <span className="font-bold" style={{ color: WARNING_ORANGE }}>Achtung!</span>
                    {' '}
                    <span className="font-semibold text-[var(--text-primary)]">{playerName}</span>
                    {' '}isn&apos;t on {REALM_LABELS[fromRealm]} — switched to{' '}
                    <span className="font-semibold text-[var(--text-primary)]">{REALM_LABELS[toRealm]}</span>.
                </span>
            </span>
            <button
                type="button"
                onClick={onDismiss}
                aria-label="Dismiss"
                className="shrink-0 rounded px-1.5 text-lg leading-none text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
            >
                ✕
            </button>
        </div>
    );
}
