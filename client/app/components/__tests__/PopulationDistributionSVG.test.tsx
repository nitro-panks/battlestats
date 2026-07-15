import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import PopulationDistributionSVG from '../PopulationDistributionSVG';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../context/RealmContext', () => ({ useRealm: () => ({ realm: 'na' }) }));
jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
}));

const fetchMock = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

const distributionPayload = {
    metric: 'win_rate',
    label: 'Win rate',
    x_label: 'Win rate',
    scale: 'linear',
    value_format: 'percent',
    tracked_population: 120000,
    bins: [
        { bin_min: 40, bin_max: 44, count: 900 },
        { bin_min: 44, bin_max: 48, count: 4200 },
        { bin_min: 48, bin_max: 52, count: 9800 },
        { bin_min: 52, bin_max: 56, count: 3100 },
        { bin_min: 56, bin_max: 60, count: 600 },
    ],
};

describe('PopulationDistributionSVG', () => {
    beforeEach(() => {
        fetchMock.mockReset();
    });

    it('shows the loading panel before the distribution payload arrives', () => {
        fetchMock.mockReturnValue(new Promise(() => undefined));

        render(<PopulationDistributionSVG primaryMetric="win_rate" primaryValue={52} />);

        expect(screen.getByText('Loading distribution…')).toBeInTheDocument();
    });

    it('renders the distribution curve once the payload loads', async () => {
        fetchMock.mockResolvedValue({ data: distributionPayload, headers: {} });

        const { container } = render(<PopulationDistributionSVG primaryMetric="win_rate" primaryValue={52} />);

        // The distribution renders as area/line paths (not per-bin rects).
        await waitFor(() => {
            expect(container.querySelectorAll('svg path').length).toBeGreaterThanOrEqual(2);
        });
        expect(container.querySelector('svg text')?.textContent)
            .not.toBe('Unable to load distribution chart.');
        expect(screen.queryByText('Loading distribution…')).not.toBeInTheDocument();
    });

    it('renders the error message into the chart area when the fetch fails', async () => {
        fetchMock.mockRejectedValue(new Error('boom'));

        const { container } = render(<PopulationDistributionSVG primaryMetric="win_rate" primaryValue={52} />);

        await waitFor(() => {
            expect(container.querySelector('svg text')?.textContent)
                .toBe('Unable to load distribution chart.');
        });
    });
});
