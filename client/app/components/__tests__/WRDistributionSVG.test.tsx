import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import WRDistributionSVG from '../WRDistributionSVG';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../context/RealmContext', () => ({ useRealm: () => ({ realm: 'na' }) }));
jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
}));

const fetchMock = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

const correlationPayload = {
    metric: 'win_rate_survival',
    label: 'Win rate vs survival',
    x_label: 'Win rate',
    y_label: 'Survival rate',
    tracked_population: 25000,
    correlation: 0.62,
    x_domain: { min: 40, max: 70, bin_width: 2 },
    y_domain: { min: 30, max: 80, bin_width: 2 },
    tiles: [
        { x_index: 0, y_index: 0, count: 40 },
        { x_index: 5, y_index: 8, count: 220 },
        { x_index: 10, y_index: 12, count: 90 },
    ],
    trend: [
        { x_index: 0, y: 40, count: 40 },
        { x_index: 5, y: 50, count: 220 },
        { x_index: 10, y: 62, count: 90 },
    ],
};

describe('WRDistributionSVG', () => {
    beforeEach(() => {
        fetchMock.mockReset();
    });

    it('shows the loading panel before the correlation payload arrives', () => {
        fetchMock.mockReturnValue(new Promise(() => undefined));

        render(<WRDistributionSVG playerWR={55} playerSurvivalRate={52} />);

        expect(screen.getByText('Loading win rate distribution…')).toBeInTheDocument();
    });

    it('renders the correlation tiles once the payload loads', async () => {
        fetchMock.mockResolvedValue({ data: correlationPayload, headers: {} });

        const { container } = render(<WRDistributionSVG playerWR={55} playerSurvivalRate={52} />);

        await waitFor(() => {
            expect(container.querySelectorAll('svg rect').length).toBeGreaterThanOrEqual(correlationPayload.tiles.length);
        });
        expect(screen.queryByText('Loading win rate distribution…')).not.toBeInTheDocument();
    });

    it('renders the error message into the chart area when the fetch fails', async () => {
        fetchMock.mockRejectedValue(new Error('boom'));

        const { container } = render(<WRDistributionSVG playerWR={55} playerSurvivalRate={52} />);

        await waitFor(() => {
            expect(container.querySelector('svg text')?.textContent)
                .toBe('Unable to load win rate and survival chart.');
        });
    });

    it('does not fetch when the player has no survival rate', () => {
        render(<WRDistributionSVG playerWR={55} playerSurvivalRate={null} />);

        expect(fetchMock).not.toHaveBeenCalled();
    });
});
