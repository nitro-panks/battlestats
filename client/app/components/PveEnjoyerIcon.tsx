import React from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faRobot } from '@fortawesome/free-solid-svg-icons';

const SIZE_CLASS = { header: 'text-sm', inline: 'text-[11px]', search: 'text-xs' } as const;

interface PveEnjoyerIconProps {
    size?: keyof typeof SIZE_CLASS;
}

const PveEnjoyerIcon: React.FC<PveEnjoyerIconProps> = ({ size = 'header' }) => (
    <span
        title="pve enjoyer"
        aria-label="pve enjoyer"
        className="inline-flex items-center cursor-help"
    >
        <FontAwesomeIcon
            icon={faRobot}
            className={`${SIZE_CLASS[size]} text-slate-500`}
            aria-hidden="true"
        />
    </span>
);

export default PveEnjoyerIcon;
