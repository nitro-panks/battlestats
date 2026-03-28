import React from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCrown } from '@fortawesome/free-solid-svg-icons';

const SIZE_CLASS = { header: 'text-sm', inline: 'text-[11px]', search: 'text-xs' } as const;

interface LeaderCrownIconProps {
    size?: keyof typeof SIZE_CLASS;
}

const LeaderCrownIcon: React.FC<LeaderCrownIconProps> = ({ size = 'header' }) => (
    <span
        title="Clan leader"
        aria-label="Clan leader"
        className="inline-flex items-center cursor-help"
    >
        <FontAwesomeIcon
            icon={faCrown}
            className={`${SIZE_CLASS[size]} text-amber-500`}
            aria-hidden="true"
        />
    </span>
);

export default LeaderCrownIcon;
