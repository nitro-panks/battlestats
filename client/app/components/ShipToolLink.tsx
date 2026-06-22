'use client';

import React from 'react';
import { trackEvent } from '../lib/umami';

// Build a Ship Tool (shiptool.st) deep link for a ship's short index code.
// The code (e.g. "RC110" for Moskva) is derived server-side from the WoWS
// GameParams index and arrives on the ship payload as `shiptool_code`.
export const shiptoolUrl = (code: string): string =>
    `https://shiptool.st/params?S=${encodeURIComponent(code)}`;

interface ShipToolLinkProps {
    code: string | null | undefined;
    shipName: string;
    realm?: string;
    shipId?: number;
    size?: keyof typeof SIZE_CLASS;
}

const SIZE_CLASS = { sm: 'h-4 w-4', md: 'h-5 w-5' } as const;

// Small external-link chip that opens a ship's parameters on shiptool.st.
// Renders nothing when no code is available (ship has no conforming GameParams
// index, or codes haven't been populated yet) so the surface degrades cleanly.
// The brand logo is a black gear on transparent, so it sits on an always-light
// chip to stay legible in both light and dark themes.
const ShipToolLink: React.FC<ShipToolLinkProps> = ({
    code,
    shipName,
    realm,
    shipId,
    size = 'sm',
}) => {
    if (!code) {
        return null;
    }
    const label = `View ${shipName} on Ship Tool (shiptool.st)`;
    return (
        <a
            href={shiptoolUrl(code)}
            target="_blank"
            rel="noreferrer"
            title={label}
            aria-label={label}
            onClick={() =>
                trackEvent('shiptool-click', {
                    realm: realm ?? '',
                    ship_id: shipId ?? 0,
                })
            }
            className="inline-flex shrink-0 items-center justify-center rounded bg-white p-0.5 ring-1 ring-[var(--border)] transition-transform hover:scale-110 hover:ring-[var(--accent-mid)]"
        >
            {/* eslint-disable-next-line @next/next/no-img-element -- tiny static brand icon; next/image optimization is unnecessary here */}
            <img
                src="/shiptool-logo.png"
                alt=""
                aria-hidden="true"
                className={SIZE_CLASS[size]}
            />
        </a>
    );
};

export default ShipToolLink;
