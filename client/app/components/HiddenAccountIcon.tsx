import React from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faMask } from '@fortawesome/free-solid-svg-icons';

interface HiddenAccountIconProps {
    className?: string;
}

const HiddenAccountIcon: React.FC<HiddenAccountIconProps> = ({ className = 'text-xs text-[#6baed6]' }) => (
    <span
        title="Hidden account"
        aria-label="Hidden account"
        className="inline-flex items-center cursor-help"
    >
        <FontAwesomeIcon
            icon={faMask}
            className={className}
            aria-hidden="true"
        />
    </span>
);

export default HiddenAccountIcon;