import React from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faRobot } from '@fortawesome/free-solid-svg-icons';

// Deprecation-candidate kill switch (2026-07-15): the icon is hidden everywhere
// but kept on hand. Flip to true to restore it at every former render site.
export const PVE_ENJOYER_ICON_ENABLED = false;

const SIZE_CLASS = { header: 'text-sm', inline: 'text-[11px]' } as const;

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
