import React from 'react';

interface LoadingPanelProps {
    label: string;
    minHeight?: number;
}

const LoadingPanel: React.FC<LoadingPanelProps> = ({ label, minHeight = 220 }) => (
    <div
        className="flex animate-pulse items-center justify-center rounded-md border border-[var(--border)] bg-[var(--bg-surface)] text-sm text-[var(--text-secondary)]"
        style={{ minHeight }}
    >
        {label}
    </div>
);

export default LoadingPanel;
