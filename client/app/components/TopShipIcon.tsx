import React from 'react';
import MedalIcon from './MedalIcon';

// Small classification-tray icon for a player who currently holds a top-spot
// (rank 1..SHIP_BADGE_TOP_N) in a Tier-10 ship's fortnight standings. One icon
// per held spot, rendered alongside the other player classification icons in the
// player header, clan-member rows, and landing/home rows. Tooltip-only (matches
// the other tray icons — not a link). Fed by the player payload's `ship_badges`.
//
// The medal glyph itself (white lanyard + rank-colored disc) lives in the shared
// MedalIcon component; this wrapper adds the tray tooltip and size.

const SIZE_CLASS = { header: 'text-sm', inline: 'text-[11px]', search: 'text-xs' } as const;

interface TopShipIconProps {
    rank: number;
    shipName: string;
    realm?: string;
    size?: keyof typeof SIZE_CLASS;
}

const TopShipIcon: React.FC<TopShipIconProps> = ({ rank, shipName, realm, size = 'header' }) => {
    const label = `Currently #${rank} ${shipName}${realm ? ` on ${realm.toUpperCase()}` : ''}`;
    return (
        <span title={label} aria-label={label} className="inline-flex items-center cursor-help">
            <MedalIcon rank={rank} className={SIZE_CLASS[size]} />
        </span>
    );
};

export default TopShipIcon;
