import React from 'react';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import ShipStats from '../ShipStats';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
}));

const mockFetch = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

const payload = {
    ship_id: 3763141360,
    ship_name: 'Henri IV',
    ship_tier: 10,
    ship_type: 'Cruiser',
    window_days: 30,
    min_account_battles: 200,
    brackets: {
        all: { players: 1174, battles: 3872 },
        top50: { players: 587, battles: 2000 },
        top25: { players: 294, battles: 967 },
    },
    user_battles: 40,
    has_user_data: true,
    clusters: [
        {
            name: 'Outcomes',
            metrics: [
                { key: 'win_rate', label: 'Win rate', unit: '%', better: 'high',
                  user: 72.2, averages: { all: 49.4, top50: 55.1, top25: 58.5 } },
            ],
        },
        {
            name: 'Combat output',
            metrics: [
                { key: 'damage_pb', label: 'Damage', unit: '/battle', better: 'high',
                  user: 157393, averages: { all: 83580, top50: 100000, top25: 119277 } },
            ],
        },
        {
            name: 'Accuracy',
            metrics: [
                { key: 'secondary_hit_rate', label: 'Secondary hit %', unit: '%', better: 'high',
                  user: 9.9, averages: { all: 11.2, top50: 11.0, top25: 11.0 } },
            ],
        },
    ],
};

const renderPanel = () =>
    render(
        <ShipStats playerName="hachiminyan" realm="na" shipId={3763141360} shipName="Henri IV" onClose={jest.fn()} />,
    );

describe('ShipStats', () => {
    beforeEach(() => {
        mockFetch.mockReset();
        // fetchSharedJson resolves to { data }
        mockFetch.mockResolvedValue({ data: payload } as never);
    });

    it('renders the comparison table with Average/Player/Delta columns', async () => {
        renderPanel();
        expect(await screen.findByText('Win rate')).toBeInTheDocument();
        expect(screen.getByRole('columnheader', { name: 'Average' })).toBeInTheDocument();
        expect(screen.getByRole('columnheader', { name: 'Player' })).toBeInTheDocument();
        expect(screen.getByRole('columnheader', { name: 'Delta' })).toBeInTheDocument();
        expect(screen.getByText('49.4%')).toBeInTheDocument();
        expect(screen.getByText('72.2%')).toBeInTheDocument();
        expect(screen.getByText('+46%')).toBeInTheDocument();
    });

    it('appends a /battle unit to the metric label and leaves the value cells bare', async () => {
        renderPanel();
        expect(await screen.findByText('Damage/battle')).toBeInTheDocument();
        // Value cells carry no "/battle" suffix.
        expect(screen.getByText('83,580')).toBeInTheDocument();
        expect(screen.getByText('157,393')).toBeInTheDocument();
        expect(screen.queryByText('157,393/battle')).not.toBeInTheDocument();
    });

    it('omits the Outcomes group header but keeps the other clusters', async () => {
        renderPanel();
        await screen.findByText('Win rate');
        expect(screen.queryByText('Outcomes')).not.toBeInTheDocument();
        expect(screen.getByText('Combat output')).toBeInTheDocument();
        expect(screen.getByText('Accuracy')).toBeInTheDocument();
    });

    it('emphasizes the better reading per row, not the column', async () => {
        renderPanel();
        await screen.findByText('Win rate');
        // Win rate: player (72.2%) beats average (49.4%) → player emphasized.
        const winRow = screen.getByText('Win rate').closest('tr') as HTMLElement;
        expect(within(winRow).getByText('72.2%')).toHaveClass('font-semibold');
        expect(within(winRow).getByText('49.4%')).not.toHaveClass('font-semibold');
        // Secondary hit: player (9.9%) trails average (11.2%) → average emphasized.
        const secRow = screen.getByText('Secondary hit %').closest('tr') as HTMLElement;
        expect(within(secRow).getByText('11.2%')).toHaveClass('font-semibold');
        expect(within(secRow).getByText('9.9%')).not.toHaveClass('font-semibold');
    });

    it('switches the average column when the skill bracket changes', async () => {
        renderPanel();
        await screen.findByText('Win rate');
        expect(screen.getByText('49.4%')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Top 50%' }));
        await waitFor(() => expect(screen.getByText('55.1%')).toBeInTheDocument());
        expect(screen.queryByText('49.4%')).not.toBeInTheDocument();
    });
});
