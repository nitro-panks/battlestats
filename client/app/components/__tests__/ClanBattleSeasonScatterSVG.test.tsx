import { render, screen, waitFor } from '@testing-library/react';
import ClanBattleSeasonScatterSVG from '../ClanBattleSeasonScatterSVG';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
    isAbortError: () => false,
}));
const mockFetch = fetchSharedJson as jest.Mock;

// win_rate is a PERCENTAGE here (not a 0..1 fraction like ranked).
const resolved = (data: unknown) => Promise.resolve({ data, headers: {} });

describe('ClanBattleSeasonScatterSVG', () => {
    beforeEach(() => mockFetch.mockReset());

    it('renders the labelled region and draws without throwing (multi-season)', async () => {
        mockFetch.mockReturnValue(resolved([
            { season_id: 3, season_label: 'CB3', battles: 40, win_rate: 55 },
            { season_id: 2, season_label: 'CB2', battles: 120, win_rate: 48 },
            { season_id: 1, season_label: 'CB1', battles: 12, win_rate: 62 },
        ]));

        render(<ClanBattleSeasonScatterSVG playerId={1} theme="light" />);
        const region = screen.getByRole('img', { name: /clan battle win rate versus battles/i });
        await waitFor(() => expect(region.querySelector('svg')).toBeTruthy());
    });

    it('survives a single season (collapsed domains) without throwing', async () => {
        mockFetch.mockReturnValue(resolved([
            { season_id: 1, season_label: 'CB1', battles: 30, win_rate: 53 },
        ]));

        render(<ClanBattleSeasonScatterSVG playerId={2} theme="dark" />);
        const region = screen.getByRole('img', { name: /clan battle win rate versus battles/i });
        await waitFor(() => expect(region.querySelector('svg')).toBeTruthy());
    });

    it('renders a placeholder when no season has battles', async () => {
        mockFetch.mockReturnValue(resolved([
            { season_id: 1, season_label: 'CB1', battles: 0, win_rate: 0 },
        ]));

        render(<ClanBattleSeasonScatterSVG playerId={3} theme="light" />);
        const region = screen.getByRole('img', { name: /clan battle win rate versus battles/i });
        await waitFor(() => expect(region.querySelector('svg')).toBeTruthy());
    });
});
