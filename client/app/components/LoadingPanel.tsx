import React from 'react';

// Label color tone. `accent` (default) is the brighter treatment used by the
// player/clan route + search surfaces; `muted` is the quieter treatment used
// inside already-loaded detail panes (e.g. ClanDetail).
type LoadingPanelTone = 'accent' | 'muted';

interface LoadingPanelProps {
    label: string;
    minHeight?: number;
    tone?: LoadingPanelTone;
}

const TONE_CLASS: Record<LoadingPanelTone, string> = {
    accent: 'text-[var(--accent-light)]',
    muted: 'text-[var(--text-secondary)]',
};

const LoadingPanel: React.FC<LoadingPanelProps> = ({ label, minHeight = 220, tone = 'accent' }) => (
    <div
        className={`flex animate-pulse items-center justify-center rounded-md border border-[var(--border)] bg-[var(--bg-surface)] text-sm ${TONE_CLASS[tone]}`}
        style={{ minHeight }}
    >
        {label}
    </div>
);

export default LoadingPanel;
