import React from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faMedal } from '@fortawesome/free-solid-svg-icons';

// Small classification-tray icon for a player who currently holds a top-spot
// (rank 1..SHIP_BADGE_TOP_N) in a Tier-10 ship's fortnight standings. One icon
// per held spot, rendered alongside the other player classification icons in the
// player header, clan-member rows, and landing/home rows. Tooltip-only (matches
// the other tray icons — not a link). Fed by the player payload's `ship_badges`.

const SIZE_CLASS = { header: 'text-sm', inline: 'text-[11px]', search: 'text-xs' } as const;

const RANK_COLOR: Record<number, string> = {
    1: 'text-amber-500',   // gold
    2: 'text-zinc-400',    // silver
    3: 'text-orange-700',  // bronze
};

interface TopShipIconProps {
    rank: number;
    shipName: string;
    realm?: string;
    size?: keyof typeof SIZE_CLASS;
}

const TopShipIcon: React.FC<TopShipIconProps> = ({ rank, shipName, realm, size = 'header' }) => {
    const color = RANK_COLOR[rank] ?? 'text-amber-500';
    const label = `Currently #${rank} ${shipName}${realm ? ` on ${realm.toUpperCase()}` : ''}`;
    return (
        <span title={label} aria-label={label} className="inline-flex items-center cursor-help">
            <FontAwesomeIcon icon={faMedal} className={`${SIZE_CLASS[size]} ${color}`} aria-hidden="true" />
        </span>
    );
};

export default TopShipIcon;
