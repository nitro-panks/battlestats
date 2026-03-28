import React from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faBed } from '@fortawesome/free-solid-svg-icons';

const SIZE_CLASS = { header: 'text-sm', inline: 'text-[11px]', search: 'text-xs' } as const;

interface InactiveIconProps {
    size?: keyof typeof SIZE_CLASS;
}

const InactiveIcon: React.FC<InactiveIconProps> = ({ size = 'header' }) => (
    <span
        title="inactive for over a year"
        aria-label="inactive for over a year"
        className="inline-flex items-center cursor-help"
    >
        <FontAwesomeIcon
            icon={faBed}
            className={`${SIZE_CLASS[size]} text-slate-400`}
            aria-hidden="true"
        />
    </span>
);

export default InactiveIcon;
