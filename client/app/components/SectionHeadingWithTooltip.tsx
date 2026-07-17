import React from 'react';
import InfoTooltip from './InfoTooltip';

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
        <InfoTooltip label={title} description={description} />
    </div>
);

export default SectionHeadingWithTooltip;
