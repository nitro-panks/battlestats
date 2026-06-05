import React from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faMedal } from '@fortawesome/free-solid-svg-icons';

// Tiered profile badge for a top-3 weekly finish in a Tier-10 ship.
// Gold/silver/bronze by rank. Fed by the player payload's `ship_badges`,
// produced by the weekly snapshot (data.compute_ship_top_player_snapshot).
// See agents/runbooks/runbook-ship-top-player-badges-2026-06-05.md.

const SIZE_CLASS = { header: 'text-sm', inline: 'text-[11px]', search: 'text-xs' } as const;

const RANK_COLOR: Record<number, string> = {
    1: 'text-amber-500',   // gold
    2: 'text-zinc-400',    // silver
    3: 'text-orange-700',  // bronze
};

export interface ShipBadge {
    ship_id: number;
    ship_name: string;
    rank: number;
    win_rate: number;
    battles: number;
}

interface ShipTopPlayerBadgeIconProps {
    badge: ShipBadge;
    size?: keyof typeof SIZE_CLASS;
}

const ShipTopPlayerBadgeIcon: React.FC<ShipTopPlayerBadgeIconProps> = ({ badge, size = 'header' }) => {
    const color = RANK_COLOR[badge.rank] ?? 'text-amber-500';
    const label = `#${badge.rank} ${badge.ship_name} · ${badge.win_rate.toFixed(1)}% WR · ${badge.battles.toLocaleString()} battles (last 7d)`;
    return (
        <span
            title={label}
            aria-label={label}
            className="inline-flex items-center cursor-help"
        >
            <FontAwesomeIcon
                icon={faMedal}
                className={`${SIZE_CLASS[size]} ${color}`}
                aria-hidden="true"
            />
        </span>
    );
};

export default ShipTopPlayerBadgeIcon;
