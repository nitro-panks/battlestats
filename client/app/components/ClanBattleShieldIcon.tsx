import React from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faShieldHalved } from '@fortawesome/free-solid-svg-icons';
import wrColor from '../lib/wrColor';

const SIZE_CLASS = { header: 'text-sm', inline: 'text-[11px]' } as const;

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
        // Current-season semantics: the shield marks battles logged in the
        // current CB season, tinted by the current-season WR (see
        // agents/runbooks/runbook-cb-icon-current-season-2026-07-15.md).
        title={titleText ?? (winRate == null ? 'clan battles this season' : `clan battles this season · ${winRate.toFixed(1)}% WR`)}
        aria-label={ariaLabel ?? (winRate == null ? 'clan battles this season' : `clan battles this season ${winRate.toFixed(1)} percent WR`)}
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
