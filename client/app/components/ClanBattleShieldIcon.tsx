import React from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faShieldHalved } from '@fortawesome/free-solid-svg-icons';
import wrColor from '../lib/wrColor';

const SIZE_CLASS = { header: 'text-sm', inline: 'text-[11px]', search: 'text-xs' } as const;

interface ClanBattleShieldIconProps {
    winRate: number | null;
    size?: keyof typeof SIZE_CLASS;
    titleText?: string;
    ariaLabel?: string;
    color?: string;
}

const ClanBattleShieldIcon: React.FC<ClanBattleShieldIconProps> = ({
    winRate,
    size = 'header',
    titleText,
    ariaLabel,
    color,
}) => (
    <span
        title={titleText ?? (winRate == null ? 'clan battle enjoyer' : `clan battle enjoyer · ${winRate.toFixed(1)}% WR`)}
        aria-label={ariaLabel ?? (winRate == null ? 'clan battle enjoyer' : `clan battle enjoyer ${winRate.toFixed(1)} percent WR`)}
        className="inline-flex items-center cursor-help"
    >
        <FontAwesomeIcon
            icon={faShieldHalved}
            className={SIZE_CLASS[size]}
            style={{ color: color ?? wrColor(winRate) }}
            aria-hidden="true"
        />
    </span>
);

export default ClanBattleShieldIcon;
