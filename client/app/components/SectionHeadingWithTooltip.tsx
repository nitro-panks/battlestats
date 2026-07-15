import React from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCircleInfo } from '@fortawesome/free-solid-svg-icons';

interface SectionHeadingWithTooltipProps {
    title: string;
    description: string;
    className?: string;
}

const SectionHeadingWithTooltip: React.FC<SectionHeadingWithTooltipProps> = ({
    title,
    description,
    className = '',
}) => (
    <div className={`flex items-center gap-2 ${className}`.trim()}>
        <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--accent-mid)]">{title}</h3>
        <div className="group relative inline-flex items-center">
            <button
                type="button"
                className="inline-flex h-4 w-4 items-center justify-center text-[var(--accent-light)] transition-colors hover:text-[var(--accent-mid)] focus:outline-none focus-visible:text-[var(--accent-mid)]"
                aria-label={`More information about ${title}`}
            >
                <FontAwesomeIcon icon={faCircleInfo} className="text-sm" aria-hidden="true" />
            </button>
            <div className="pointer-events-none absolute left-0 top-full z-20 mt-2 hidden w-80 max-w-[calc(100vw-2rem)] rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-2 text-left text-xs normal-case tracking-normal text-[var(--text-primary)] shadow-lg group-hover:block group-focus-within:block">
                {description}
            </div>
        </div>
    </div>
);

export default SectionHeadingWithTooltip;