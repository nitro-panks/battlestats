import React from 'react';

// Two-tone medal-on-a-lanyard glyph: the ribbon/lanyard is always white and only
// the circular disc carries the rank color (gold/silver/bronze) via currentColor.
// A single-path font glyph (faMedal) can't two-tone the ribbon and disc, so we
// draw the SVG inline. It scales to 1em, so a font-size class (text-*) on
// `className` sizes it; the rank color is applied internally. Shared by
// TopShipIcon (classification tray), the ShipTopPlayerBanner, and the ShipHonors
// ledger so all three medal surfaces stay visually identical.

export const RANK_COLOR: Record<number, string> = {
    1: 'text-amber-500',   // gold
    2: 'text-zinc-400',    // silver
    3: 'text-orange-700',  // bronze
};

interface MedalIconProps {
    rank: number;
    className?: string;
    // When set, the glyph is exposed to a11y as an image with this label;
    // otherwise it is decorative (aria-hidden) and the wrapper supplies the label.
    title?: string;
}

const MedalIcon: React.FC<MedalIconProps> = ({ rank, className = '', title }) => {
    const color = RANK_COLOR[rank] ?? 'text-amber-500';
    return (
        <svg
            viewBox="0 0 24 24"
            width="1em"
            height="1em"
            fill="none"
            role={title ? 'img' : undefined}
            aria-label={title}
            aria-hidden={title ? undefined : true}
            className={`${color} ${className}`}
            style={{ verticalAlign: '-0.125em' }}
        >
            {title ? <title>{title}</title> : null}
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
    );
};

export default MedalIcon;
