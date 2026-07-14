import React from 'react';
import { nationLabel } from '../lib/shipIdentity';

// WoWS nation code -> bundled flag file in /public/flags. Real modern nations
// use the lipis/flag-icons SVGs; the in-game nations without a modern flag
// (ussr, pan_asia, europe, pan_america, commonwealth) use their authentic WG /
// historical flags (WebP). Swap a file here (and drop it in public/flags/) to
// change one.
const FLAG_FILE: Record<string, string> = {
    usa: 'usa.svg',
    japan: 'japan.svg',
    germany: 'germany.svg',
    ussr: 'ussr.webp',
    uk: 'uk.svg',
    france: 'france.svg',
    italy: 'italy.svg',
    pan_asia: 'pan_asia.webp',
    europe: 'europe.webp',
    commonwealth: 'commonwealth.webp',
    netherlands: 'netherlands.svg',
    spain: 'spain.svg',
    pan_america: 'pan_america.webp',
};

// object-cover keeps every flag the same height (fills the 4x3 box, no squish);
// wide flags get cropped left/right. Default crop is centered, which suits the
// centered-emblem flags. The USSR flag is a red field with its emblem in the
// top-left canton, so it anchors there — cropping the empty red side, not the
// emblem.
const FLAG_POSITION: Record<string, string> = {
    ussr: 'object-left-top',
};

interface NationFlagProps {
    nation: string | null | undefined;
    className?: string;
}

// A small nation flag rendered to the left of a ship name. Renders nothing for
// an unknown/absent nation so callers can drop it in unconditionally. The thin
// ring keeps light flags (e.g. Japan) legible on the dark surface.
const NationFlag: React.FC<NationFlagProps> = ({ nation, className = '' }) => {
    if (!nation || !(nation in FLAG_FILE)) {
        return null;
    }
    const label = nationLabel(nation) ?? nation;
    return (
        // Decorative: the adjacent ship name is the accessible content, so the flag
        // is aria-hidden (keeps the ship-link's accessible name clean). `title`
        // still gives sighted users a hover tooltip of the nation.
        // eslint-disable-next-line @next/next/no-img-element -- tiny static flag SVG; next/image optimization is unnecessary here
        <img
            src={`/flags/${FLAG_FILE[nation]}`}
            alt=""
            aria-hidden="true"
            title={label}
            width={16}
            height={12}
            loading="lazy"
            className={`inline-block h-3 w-4 shrink-0 rounded-[1px] object-cover ${FLAG_POSITION[nation] ?? ''} ring-1 ring-black/25 ${className}`}
        />
    );
};

export default NationFlag;
