import { fireEvent, render, screen } from '@testing-library/react';
import PlayerEfficiencyBadges from '../PlayerEfficiencyBadges';
import { chartColors } from '../../lib/chartTheme';

const sampleRows = [
    { ship_id: 1, top_grade_class: 2, ship_name: 'Bismarck', ship_type: 'battleship', ship_tier: 8 },
    { ship_id: 2, top_grade_class: 1, ship_name: 'Des Moines', ship_type: 'cruiser', ship_tier: 10 },
    { ship_id: 3, top_grade_class: 3, ship_name: 'Shimakaze', ship_type: 'destroyer', ship_tier: 10 },
    { ship_id: 4, top_grade_class: 4, ship_name: 'Gato', ship_type: 'submarine', ship_tier: 10 },
];

const svgTextContents = (container: HTMLElement): (string | null)[] => (
    Array.from(container.querySelectorAll('svg text')).map((el) => el.textContent)
);

describe('PlayerEfficiencyBadges', () => {
    it('renders empty-state copy when there are no qualifying rows', () => {
        render(<PlayerEfficiencyBadges efficiencyRows={[]} />);

        expect(screen.getByText('Efficiency Badges')).toBeInTheDocument();
        expect(screen.getByText(/No Efficiency Badge data is stored/i)).toBeInTheDocument();
    });

    it('renders legend counts per badge level', () => {
        render(<PlayerEfficiencyBadges efficiencyRows={sampleRows} />);

        expect(screen.getByTitle('Expert: 1')).toBeInTheDocument();
        expect(screen.getByTitle('Badge I: 1')).toBeInTheDocument();
        expect(screen.getByTitle('Badge II: 1')).toBeInTheDocument();
        expect(screen.getByTitle('Badge III: 1')).toBeInTheDocument();
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
        expect(screen.getByTitle('Expert: 1')).toBeInTheDocument();
    });

    it('shows no ship-name summary until hover, then only the hovered ship', () => {
        const { container } = render(<PlayerEfficiencyBadges efficiencyRows={sampleRows} />);
        const shipNames = ['Bismarck', 'Des Moines', 'Shimakaze', 'Gato'];

        // No summary before any hover.
        shipNames.forEach((name) => {
            expect(screen.queryByText(name)).toBeNull();
        });

        const hitTargets = container.querySelectorAll('circle.badge-dot-hit');
        expect(hitTargets.length).toBe(sampleRows.length);
        hitTargets.forEach((hit) => fireEvent.mouseOver(hit));

        // After hovering every dot the summary shows exactly one ship —
        // the last-hovered one.
        const shownShips = shipNames.filter((name) => screen.queryByText(name) !== null);
        expect(shownShips).toHaveLength(1);

        // Mouseout clears the summary again.
        fireEvent.mouseOut(hitTargets[hitTargets.length - 1]);
        shipNames.forEach((name) => {
            expect(screen.queryByText(name)).toBeNull();
        });
    });

    it('lights the hover cohorts: cyan same-type borders, roman-numeral tier labels, medal fill contrast', () => {
        const { container } = render(<PlayerEfficiencyBadges efficiencyRows={sampleRows} />);

        // Nodes sort by badge class then name, so index 0 is Des Moines
        // (Expert, cruiser, Tier X) and index 1 is Bismarck (I, BB, Tier VIII).
        const hitTargets = container.querySelectorAll('circle.badge-dot-hit');
        fireEvent.mouseOver(hitTargets[0]);

        const dots = container.querySelectorAll<SVGCircleElement>('circle.badge-dot');
        // Type cohort: only the hovered cruiser wears the cyan border.
        expect(dots[0].getAttribute('stroke')).toBe('#06b6d4');
        expect(dots[1].getAttribute('stroke')).toBe(chartColors.dark.barStroke);

        // Tier cohort: Tier-X ships fade in their roman numeral; Bismarck's
        // Tier-VIII label stays hidden.
        const tierLabels = container.querySelectorAll<SVGTextElement>('text.badge-dot-tier-label');
        expect(tierLabels[0].textContent).toBe('X');
        expect(tierLabels[0].style.opacity).toBe('1');
        expect(tierLabels[1].textContent).toBe('VIII');
        expect(tierLabels[1].style.opacity).toBe('0');
        expect(tierLabels[2].style.opacity).toBe('1');

        // Medal contrast: the hovered Expert saturates; other classes empty.
        expect(dots[0].style.fillOpacity).toBe('1');
        expect(dots[1].style.fillOpacity).toBe('0');

        // Mouseout restores the rest state.
        fireEvent.mouseOut(hitTargets[0]);
        expect(dots[0].getAttribute('stroke')).toBe(chartColors.dark.barStroke);
        expect(tierLabels[0].style.opacity).toBe('0');
        expect(dots[1].style.fillOpacity).toBe('0.35');
    });

    it('labels each ship-type cluster and webs circles sharing a tier', () => {
        const { container } = render(<PlayerEfficiencyBadges efficiencyRows={sampleRows} />);

        // Cluster labels live inside the SVG (the header filter buttons carry
        // the same short type labels, so scope the query to the plot).
        const svgLabels = svgTextContents(container);
        ['BB', 'CA', 'DD', 'Sub'].forEach((typeLabel) => {
            expect(svgLabels).toContain(typeLabel);
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

    describe('type/tier filters', () => {
        it('renders both filter groups defaulted to All, with the info icon at the end', () => {
            render(<PlayerEfficiencyBadges efficiencyRows={sampleRows} />);

            const allButtons = screen.getAllByRole('button', { name: 'All' });
            expect(allButtons).toHaveLength(2);
            allButtons.forEach((button) => expect(button).toHaveAttribute('aria-pressed', 'true'));

            ['BB', 'CA', 'DD', 'Sub'].forEach((label) => {
                expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
            });
            expect(screen.getByRole('button', { name: 'T10' })).toBeInTheDocument();
            expect(screen.getByRole('button', { name: 'T8' })).toBeInTheDocument();

            expect(screen.getByRole('button', { name: 'More information about Efficiency Badges' })).toBeInTheDocument();
        });

        it('hides the filter groups when there are no qualifying rows', () => {
            render(<PlayerEfficiencyBadges efficiencyRows={[]} />);

            expect(screen.queryByRole('button', { name: 'All' })).toBeNull();
        });

        it('soloing a type narrows the plot and the legend counts to that type', () => {
            const { container } = render(<PlayerEfficiencyBadges efficiencyRows={sampleRows} />);

            fireEvent.click(screen.getByRole('button', { name: 'BB' }));

            // Only Bismarck (Badge I) survives the filter.
            expect(container.querySelectorAll('circle.badge-dot')).toHaveLength(1);
            expect(screen.getByTitle('Badge I: 1')).toBeInTheDocument();
            expect(screen.getByTitle('Expert: 0')).toBeInTheDocument();

            // The types All button releases the solo.
            const [typesAll] = screen.getAllByRole('button', { name: 'All' });
            fireEvent.click(typesAll);
            expect(container.querySelectorAll('circle.badge-dot')).toHaveLength(sampleRows.length);
        });

        it('soloing a tier narrows the plot to that tier', () => {
            const { container } = render(<PlayerEfficiencyBadges efficiencyRows={sampleRows} />);

            fireEvent.click(screen.getByRole('button', { name: 'T10' }));

            expect(container.querySelectorAll('circle.badge-dot')).toHaveLength(3);
            expect(screen.getByTitle('Badge I: 0')).toBeInTheDocument();
        });

        it('deselecting the last active value returns the group to All', () => {
            const { container } = render(<PlayerEfficiencyBadges efficiencyRows={sampleRows} />);

            const bbButton = screen.getByRole('button', { name: 'BB' });
            fireEvent.click(bbButton);
            expect(container.querySelectorAll('circle.badge-dot')).toHaveLength(1);

            fireEvent.click(bbButton);
            const [typesAll] = screen.getAllByRole('button', { name: 'All' });
            expect(typesAll).toHaveAttribute('aria-pressed', 'true');
            expect(container.querySelectorAll('circle.badge-dot')).toHaveLength(sampleRows.length);
        });

        it('shows a no-match message when the type and tier filters exclude every badge', () => {
            const { container } = render(<PlayerEfficiencyBadges efficiencyRows={sampleRows} />);

            // BB (Tier 8 only) crossed with Tier 10 matches nothing.
            fireEvent.click(screen.getByRole('button', { name: 'BB' }));
            fireEvent.click(screen.getByRole('button', { name: 'T10' }));

            expect(container.querySelectorAll('circle.badge-dot')).toHaveLength(0);
            expect(screen.getByText(/No badges match the selected type and tier filters/i)).toBeInTheDocument();
        });
    });
});
