import React from 'react';
import { render, screen } from '@testing-library/react';
import TopShipBadges from '../TopShipBadges';
import type { ShipBadge } from '../ShipTopPlayerBanner';

const badge = (overrides: Partial<ShipBadge>): ShipBadge => ({
    ship_id: 1,
    ship_name: 'Shimakaze',
    tier: 10,
    rank: 1,
    win_rate: 60,
    battles: 100,
    avg_damage: 90000,
    window_days: 14,
    ...overrides,
});

describe('TopShipBadges', () => {
    it('renders nothing when there are no badges', () => {
        const { container } = render(<TopShipBadges badges={undefined} realm="na" size="inline" />);
        expect(container).toBeEmptyDOMElement();
    });

    it('renders one medal per badge with the realm-stamped tooltip', () => {
        render(
            <TopShipBadges
                badges={[badge({ ship_id: 1, ship_name: 'Shimakaze', rank: 1 }), badge({ ship_id: 2, ship_name: 'Zao', rank: 2 })]}
                realm="na"
                size="header"
            />,
        );

        expect(screen.getByTitle('Currently #1 Shimakaze (T10) on NA')).toBeInTheDocument();
        expect(screen.getByTitle('Currently #2 Zao (T10) on NA')).toBeInTheDocument();
    });

    it('caps the rendered badges at the top three', () => {
        render(
            <TopShipBadges
                badges={[1, 2, 3, 4].map((n) => badge({ ship_id: n, ship_name: `Ship${n}`, rank: n }))}
                realm="eu"
                size="search"
            />,
        );

        expect(screen.getByTitle('Currently #1 Ship1 (T10) on EU')).toBeInTheDocument();
        expect(screen.getByTitle('Currently #3 Ship3 (T10) on EU')).toBeInTheDocument();
        expect(screen.queryByTitle('Currently #4 Ship4 (T10) on EU')).not.toBeInTheDocument();
    });
});
