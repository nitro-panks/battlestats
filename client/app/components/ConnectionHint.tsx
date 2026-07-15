'use client';

import { useDegradationMode } from '../context/DegradationContext';

// A small, non-alarming hint shown only while the network looks degraded. It
// sets expectations ("we're still working, just slowly") so the backed-off,
// slower-loading page doesn't read as broken. Renders nothing when healthy.
export default function ConnectionHint() {
    const mode = useDegradationMode();
    if (mode !== 'degraded') {
        return null;
    }
    return (
        <div
            role="status"
            aria-live="polite"
            className="mb-3 flex items-center gap-2 rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-1.5 text-xs text-[var(--text-secondary)]"
        >
            <span
                aria-hidden="true"
                className="inline-block h-2 w-2 animate-pulse rounded-full bg-[var(--text-secondary)]"
            />
            Connection is slow — updating in the background.
        </div>
    );
}
