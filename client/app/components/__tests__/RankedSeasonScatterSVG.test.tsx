import { render, screen, waitFor } from '@testing-library/react';
import RankedSeasonScatterSVG from '../RankedSeasonScatterSVG';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
    isAbortError: () => false,
}));
const mockFetch = fetchSharedJson as jest.Mock;

const resolved = (data: unknown) => Promise.resolve({ data, headers: {} });

// jsdom has no layout, but resolveContainerChartWidth falls back to the 600px
// default (clientWidth 0), so drawChart actually runs — which lets these assert
// the degenerate-domain guards don't throw. Dots themselves aren't asserted
// (real verification is visual, per the chart's SVG nature).
describe('RankedSeasonScatterSVG', () => {
    beforeEach(() => mockFetch.mockReset());

    it('renders the labelled chart region and draws without throwing (multi-season)', async () => {
        // Mixed leagues exercise the glyph mapping (star/diamond/circle) plus a
        // league-less season (→ circle) without throwing.
        mockFetch.mockReturnValue(resolved([
            { season_id: 3, season_label: 'S3', total_battles: 400, win_rate: 0.55, highest_league_name: 'Gold' },
            { season_id: 2, season_label: 'S2', total_battles: 120, win_rate: 0.49, highest_league_name: 'Silver' },
            { season_id: 1, season_label: 'S1', total_battles: 900, win_rate: 0.58, highest_league_name: 'Bronze' },
            { season_id: 0, season_label: 'S0', total_battles: 50, win_rate: 0.5 },
        ]));

        render(<RankedSeasonScatterSVG playerId={1} theme="light" />);

        const region = screen.getByRole('img', { name: /win rate versus battles/i });
        await waitFor(() => expect(region.querySelector('svg')).toBeTruthy());
    });

    it('survives a single season (collapsed domains) without throwing', async () => {
        mockFetch.mockReturnValue(resolved([
            { season_id: 1, season_label: 'S1', total_battles: 200, win_rate: 0.53 },
        ]));

        render(<RankedSeasonScatterSVG playerId={2} theme="dark" />);
        const region = screen.getByRole('img', { name: /win rate versus battles/i });
        await waitFor(() => expect(region.querySelector('svg')).toBeTruthy());
    });

    it('renders a placeholder when no season has battles', async () => {
        mockFetch.mockReturnValue(resolved([
            { season_id: 1, season_label: 'S1', total_battles: 0, win_rate: 0 },
        ]));

        render(<RankedSeasonScatterSVG playerId={3} theme="light" />);
        const region = screen.getByRole('img', { name: /win rate versus battles/i });
        await waitFor(() => expect(region.querySelector('svg')).toBeTruthy());
    });
});
