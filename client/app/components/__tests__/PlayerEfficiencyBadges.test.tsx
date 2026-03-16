import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import PlayerEfficiencyBadges from '../PlayerEfficiencyBadges';

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

    it('renders header totals, summary cards, and compact ship metadata labels', () => {
        render(<PlayerEfficiencyBadges efficiencyRows={sampleRows} />);

        expect(screen.getByText('Efficiency Badges')).toBeInTheDocument();
        expect(screen.getByTitle('Expert badges: 1')).toBeInTheDocument();
        expect(screen.getByTitle('Grade I badges: 1')).toBeInTheDocument();
        expect(screen.getByTitle('Grade II badges: 1')).toBeInTheDocument();
        expect(screen.getByTitle('Grade III badges: 1')).toBeInTheDocument();

        expect(screen.getByText('Highest Badge')).toBeInTheDocument();
        expect(screen.getByText('Strongest Class')).toBeInTheDocument();
        expect(screen.getByText('Strongest Tier Band')).toBeInTheDocument();

        const highestBadgeCard = screen.getByText('Highest Badge').closest('div');
        const strongestClassCard = screen.getByText('Strongest Class').closest('div');
        const strongestTierBandCard = screen.getByText('Strongest Tier Band').closest('div');

        expect(highestBadgeCard).not.toBeNull();
        expect(strongestClassCard).not.toBeNull();
        expect(strongestTierBandCard).not.toBeNull();

        expect(within(highestBadgeCard as HTMLElement).getByText('E')).toBeInTheDocument();
        expect(within(strongestClassCard as HTMLElement).getByText('CA')).toBeInTheDocument();
        expect(within(strongestTierBandCard as HTMLElement).getByText('IX-X')).toBeInTheDocument();

        expect(screen.getByText('BB')).toBeInTheDocument();
        expect(screen.getByText('Sub')).toBeInTheDocument();
    });

    it('sorts by ship name when the Ship header is clicked', () => {
        render(<PlayerEfficiencyBadges efficiencyRows={sampleRows} />);

        fireEvent.click(screen.getByRole('button', { name: /Ship/i }));

        const rows = screen.getAllByRole('row').slice(1);
        const firstRow = rows[0];
        expect(within(firstRow).getByText('Bismarck')).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: /Ship/i }));

        const resortedRows = screen.getAllByRole('row').slice(1);
        const firstResortedRow = resortedRows[0];
        expect(within(firstResortedRow).getByText('Shimakaze')).toBeInTheDocument();
    });
});