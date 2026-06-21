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
            className="mb-3 flex items-center gap-2 rounded-md border border-[var(--accent-border,rgba(125,125,125,0.25))] bg-[var(--bg-elevated,rgba(125,125,125,0.08))] px-3 py-1.5 text-xs text-[var(--text-secondary,#9aa0a6)]"
        >
            <span
                aria-hidden="true"
                className="inline-block h-2 w-2 animate-pulse rounded-full bg-[var(--text-secondary,#9aa0a6)]"
            />
            Connection is slow — updating in the background.
        </div>
    );
}
