import React from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCircleInfo } from '@fortawesome/free-solid-svg-icons';

interface InfoTooltipProps {
    label: string;
    description: string;
    /* Which edge of the icon the hover panel hangs from — 'right' keeps the
       panel on-screen when the icon sits at the end of a full-width row. */
    align?: 'left' | 'right';
    className?: string;
}

const InfoTooltip: React.FC<InfoTooltipProps> = ({
    label,
    description,
    align = 'left',
    className = '',
}) => (
    <div className={`group relative inline-flex items-center ${className}`.trim()}>
        <button
            type="button"
            className="inline-flex h-4 w-4 items-center justify-center text-[var(--accent-light)] transition-colors hover:text-[var(--accent-mid)] focus:outline-none focus-visible:text-[var(--accent-mid)]"
            aria-label={`More information about ${label}`}
        >
            <FontAwesomeIcon icon={faCircleInfo} className="text-sm" aria-hidden="true" />
        </button>
        <div className={`pointer-events-none absolute ${align === 'right' ? 'right-0' : 'left-0'} top-full z-20 mt-2 hidden w-80 max-w-[calc(100vw-2rem)] rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-2 text-left text-xs normal-case tracking-normal text-[var(--text-primary)] shadow-lg group-hover:block group-focus-within:block`}>
            {description}
        </div>
    </div>
);

export default InfoTooltip;
