import React from 'react';

// Small classification-tray icon for a player who currently holds a top-spot
// (rank 1..SHIP_BADGE_TOP_N) in a Tier-10 ship's fortnight standings. One icon
// per held spot, rendered alongside the other player classification icons in the
// player header, clan-member rows, and landing/home rows. Tooltip-only (matches
// the other tray icons — not a link). Fed by the player payload's `ship_badges`.
//
// Rendered as a custom two-tone medal-on-a-lanyard: the ribbon/lanyard is always
// white, and only the circular medal disc carries the award color (gold/silver/
// bronze) via `currentColor`. A single-color font glyph (faMedal) can't two-tone
// the ribbon and disc, so we draw the SVG inline.

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
            <svg
                viewBox="0 0 24 24"
                width="1em"
                height="1em"
                fill="none"
                aria-hidden="true"
                className={`${SIZE_CLASS[size]} ${color}`}
                style={{ verticalAlign: '-0.125em' }}
            >
                {/* Lanyard ribbon — always white (soft slate edge for light-bg legibility) */}
                <path d="M8 2.5 L12 10.8 L16 2.5" stroke="#94a3b8" strokeWidth="3.4"
                    strokeOpacity="0.45" strokeLinejoin="round" strokeLinecap="round" />
                <path d="M8 2.5 L12 10.8 L16 2.5" stroke="#ffffff" strokeWidth="2.2"
                    strokeLinejoin="round" strokeLinecap="round" />
                {/* Medal disc — the only award-colored part (currentColor) */}
                <circle cx="12" cy="15.6" r="6.3" fill="currentColor" stroke="#ffffff" strokeWidth="0.9" />
                {/* Inner ring detail */}
                <circle cx="12" cy="15.6" r="3.2" fill="none" stroke="#ffffff" strokeWidth="1" strokeOpacity="0.7" />
            </svg>
        </span>
    );
};

export default TopShipIcon;
