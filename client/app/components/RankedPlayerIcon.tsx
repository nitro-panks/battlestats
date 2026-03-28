import React from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faStar } from '@fortawesome/free-solid-svg-icons';
import { getRankedLeagueColor, getRankedLeagueTooltip, type RankedLeagueName } from './rankedLeague';

const SIZE_CLASS = { header: 'text-sm', inline: 'text-[11px]', search: 'text-xs' } as const;

interface RankedPlayerIconProps {
    league: RankedLeagueName | null | undefined;
    size?: keyof typeof SIZE_CLASS;
}

const RankedPlayerIcon: React.FC<RankedPlayerIconProps> = ({ league, size = 'header' }) => (
    <span
        title={getRankedLeagueTooltip(league)}
        aria-label={getRankedLeagueTooltip(league)}
        className="inline-flex items-center cursor-help"
    >
        <FontAwesomeIcon
            icon={faStar}
            className={SIZE_CLASS[size]}
            style={{ color: getRankedLeagueColor(league) }}
            aria-hidden="true"
        />
    </span>
);

export default RankedPlayerIcon;
