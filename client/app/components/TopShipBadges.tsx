import React from 'react';
import TopShipIcon from './TopShipIcon';
import type { ShipBadge } from './ShipTopPlayerBanner';

// The up-to-3 "currently top-N in a T10 ship" medals, identical across the
// player header, clan-member rows, and search/landing rows (only realm + size
// differ). The surrounding classification-icon dispatch — which other icons a
// surface shows, and in what order — genuinely differs per surface and stays
// inline; only this shared badge tail is factored out here.
interface TopShipBadgesProps {
    badges?: ShipBadge[];
    realm?: string;
    size?: React.ComponentProps<typeof TopShipIcon>['size'];
}

const TopShipBadges: React.FC<TopShipBadgesProps> = ({ badges, realm, size }) => (
    <>
        {(badges ?? []).slice(0, 3).map((badge) => (
            <TopShipIcon
                key={`${badge.ship_id}-${badge.rank}`}
                rank={badge.rank}
                shipName={badge.ship_name}
                tier={badge.tier}
                realm={realm}
                size={size}
            />
        ))}
    </>
);

export default TopShipBadges;
