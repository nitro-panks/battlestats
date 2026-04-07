import React from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faTwitch } from '@fortawesome/free-brands-svg-icons';

const SIZE_CLASS = { header: 'text-sm', inline: 'text-[11px]', search: 'text-xs' } as const;

interface TwitchStreamerIconProps {
    size?: keyof typeof SIZE_CLASS;
    titleText?: string;
    ariaLabel?: string;
}

const TwitchStreamerIcon: React.FC<TwitchStreamerIconProps> = ({
    size = 'header',
    titleText = 'Known streamer',
    ariaLabel = 'Known streamer',
}) => (
    <span
        title={titleText}
        aria-label={ariaLabel}
        className="inline-flex cursor-help items-center"
    >
        <FontAwesomeIcon
            icon={faTwitch}
            className={SIZE_CLASS[size]}
            style={{ color: '#9146FF' }}
            aria-hidden="true"
        />
    </span>
);

export default TwitchStreamerIcon;