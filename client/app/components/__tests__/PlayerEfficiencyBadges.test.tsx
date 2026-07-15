import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import PlayerEfficiencyBadges from '../PlayerEfficiencyBadges';
import { chartColors } from '../../lib/chartTheme';

jest.mock('../SectionHeadingWithTooltip', () => {
    return function MockSectionHeadingWithTooltip({ title }: { title: string }) {
        return <h3>{title}</h3>;
    };
});

const sampleRows = [
    { ship_id: 1, top_grade_class: 2, ship_name: 'Bismarck', ship_type: 'battleship', ship_tier: 8 },
    { ship_id: 2, top_grade_class: 1, ship_name: 'Des Moines', ship_type: 'cruiser', ship_tier: 10 },
    { ship_id: 3, top_grade_class: 3, ship_name: 'Shimakaze', ship_type: 'destroyer', ship_tier: 10 },
    { ship_id: 4, top_grade_class: 4, ship_name: 'Gato', ship_type: 'submarine', ship_tier: 10 },
];

describe('PlayerEfficiencyBadges', () => {
    it('renders empty-state copy when there are no qualifying rows', () => {
        render(<PlayerEfficiencyBadges efficiencyRows={[]} />);

        expect(screen.getByText('Efficiency Badges')).toBeInTheDocument();
        expect(screen.getByText(/No Efficiency Badge data is stored/i)).toBeInTheDocument();
    });

    it('renders legend counts per badge class', () => {
        render(<PlayerEfficiencyBadges efficiencyRows={sampleRows} />);

        expect(screen.getByTitle('Expert badges: 1')).toBeInTheDocument();
        expect(screen.getByTitle('Grade I badges: 1')).toBeInTheDocument();
        expect(screen.getByTitle('Grade II badges: 1')).toBeInTheDocument();
        expect(screen.getByTitle('Grade III badges: 1')).toBeInTheDocument();
    });

    it('renders one strip-plot dot per badged ship, colored by badge class', () => {
        const { container } = render(<PlayerEfficiencyBadges efficiencyRows={sampleRows} />);

        const dots = container.querySelectorAll('circle.badge-dot');
        expect(dots).toHaveLength(sampleRows.length);

        // The site theme defaults to dark (ThemeContext), so the dots wear the
        // dark-mode badge palette.
        const fills = Array.from(dots).map((dot) => dot.getAttribute('fill'));
        expect(fills).toContain(chartColors.dark.badgeE);
        expect(fills).toContain(chartColors.dark.badgeI);
        expect(fills).toContain(chartColors.dark.badgeII);
        expect(fills).toContain(chartColors.dark.badgeIII);
    });

    it('drops rows without a tier from both the plot and the legend counts', () => {
        const { container } = render(
            <PlayerEfficiencyBadges
                efficiencyRows={[...sampleRows, { ship_id: 5, top_grade_class: 1, ship_name: 'Ghost', ship_type: 'cruiser', ship_tier: null }]}
            />,
        );

        expect(container.querySelectorAll('circle.badge-dot')).toHaveLength(sampleRows.length);
        expect(screen.getByTitle('Expert badges: 1')).toBeInTheDocument();
    });

    it('defaults the summary line to the best badge and updates it on hover', () => {
        const { container } = render(<PlayerEfficiencyBadges efficiencyRows={sampleRows} />);

        // Des Moines holds the class-1 (E) badge, so it leads the summary.
        expect(screen.getByText('Des Moines')).toBeInTheDocument();
        expect(screen.getByText('Badge E')).toBeInTheDocument();

        const hitTargets = container.querySelectorAll('circle.badge-dot-hit');
        expect(hitTargets.length).toBe(sampleRows.length);
        hitTargets.forEach((hit) => fireEvent.mouseOver(hit));

        // After hovering every dot the summary shows exactly one ship —
        // the last-hovered one.
        const shownShips = ['Bismarck', 'Des Moines', 'Shimakaze', 'Gato']
            .filter((name) => screen.queryByText(name) !== null);
        expect(shownShips).toHaveLength(1);
    });

    it('labels each ship-type cluster and webs circles sharing a tier', () => {
        const { container } = render(<PlayerEfficiencyBadges efficiencyRows={sampleRows} />);

        ['BB', 'CA', 'DD', 'Sub'].forEach((typeLabel) => {
            expect(screen.getByText(typeLabel)).toBeInTheDocument();
        });

        // Four single-ship types with distinct classes: no type/class mesh
        // lines, one pair per Tier-X duo among the three Tier-X ships.
        expect(container.querySelectorAll('line.badge-mesh-type')).toHaveLength(0);
        expect(container.querySelectorAll('line.badge-mesh-tier')).toHaveLength(3);
        expect(container.querySelectorAll('line.badge-mesh-class')).toHaveLength(0);
    });

    it('webs all circle pairs sharing a type, tier, or award class', () => {
        const { container } = render(
            <PlayerEfficiencyBadges
                efficiencyRows={[
                    ...sampleRows,
                    { ship_id: 6, top_grade_class: 2, ship_name: 'Hindenburg', ship_type: 'cruiser', ship_tier: 10 },
                    { ship_id: 7, top_grade_class: 3, ship_name: 'Zao', ship_type: 'cruiser', ship_tier: 10 },
                ]}
            />,
        );

        // Three cruisers -> C(3,2)=3 type lines; five Tier-X ships -> C(5,2)=10
        // tier lines; class II and class III each have one pair -> 2 class lines.
        expect(container.querySelectorAll('line.badge-mesh-type')).toHaveLength(3);
        expect(container.querySelectorAll('line.badge-mesh-tier')).toHaveLength(10);
        expect(container.querySelectorAll('line.badge-mesh-class')).toHaveLength(2);
    });
});
