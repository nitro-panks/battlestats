import React from 'react';
import Link from 'next/link';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faMedal } from '@fortawesome/free-solid-svg-icons';
import { buildShipPath } from '../lib/entityRoutes';

// Tiered, labeled profile badge for a top-3 weekly finish in a Tier-10 ship:
// a gold/silver/bronze medal + the ship name, linking to that ship's standings
// page. Fed by the player payload's `ship_badges` (data.get_player_ship_badges).
// See agents/runbooks/runbook-ship-top-player-badges-2026-06-05.md.

const SIZE_CLASS = { header: 'text-xs', inline: 'text-[11px]', search: 'text-[10px]' } as const;

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
    realm?: string;
    size?: keyof typeof SIZE_CLASS;
}

const ShipTopPlayerBadgeIcon: React.FC<ShipTopPlayerBadgeIconProps> = ({ badge, realm, size = 'header' }) => {
    const color = RANK_COLOR[badge.rank] ?? 'text-amber-500';
    const label = `#${badge.rank} ${badge.ship_name} · ${badge.win_rate.toFixed(1)}% WR · ${badge.battles.toLocaleString()} battles (last 14d)`;
    return (
        <Link
            href={buildShipPath(badge.ship_id, badge.ship_name, realm)}
            title={label}
            aria-label={label}
            className={`inline-flex items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--bg-surface)] px-2 py-0.5 ${SIZE_CLASS[size]} font-medium text-[var(--text-strong)] hover:border-[var(--accent-mid)] transition-colors`}
        >
            <FontAwesomeIcon icon={faMedal} className={color} aria-hidden="true" />
            <span>{badge.ship_name}</span>
        </Link>
    );
};

export default ShipTopPlayerBadgeIcon;
