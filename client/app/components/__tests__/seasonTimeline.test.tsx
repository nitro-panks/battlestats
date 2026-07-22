import { render, screen, waitFor } from '@testing-library/react';
import { fractionalYear } from '../../lib/seasonTimeline';
import ClanBattleSeasonTimelineSVG from '../ClanBattleSeasonTimelineSVG';
import RankedSeasonTimelineSVG from '../RankedSeasonTimelineSVG';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
    isAbortError: () => false,
}));
const mockFetch = fetchSharedJson as jest.Mock;
const resolved = (data: unknown) => Promise.resolve({ data, headers: {} });

describe('fractionalYear', () => {
    it('parses YYYY-MM-DD to a within-year fraction', () => {
        expect(fractionalYear('2020-01-01')).toBeCloseTo(2020, 5);
        // ~mid-year
        expect(fractionalYear('2020-07-01')).toBeGreaterThan(2020.45);
        expect(fractionalYear('2020-07-01')).toBeLessThan(2020.55);
    });

    it('parses a bare year and rejects junk', () => {
        expect(fractionalYear('2019')).toBe(2019);
        expect(fractionalYear(null)).toBeNull();
        expect(fractionalYear('n/a')).toBeNull();
    });
});

describe('season timeline components', () => {
    beforeEach(() => mockFetch.mockReset());

    it('draws the clan-battle timeline across the season span (percent WR)', async () => {
        mockFetch.mockReturnValue(resolved([
            { season_label: 'CB1', battles: 40, win_rate: 55, start_date: '2020-06-01' },
            { season_label: 'CB2', battles: 120, win_rate: 48, start_date: '2025-02-01' },
        ]));

        render(<ClanBattleSeasonTimelineSVG playerId={1} theme="light" />);
        const region = screen.getByRole('img', { name: /clan battle season activity timeline/i });
        await waitFor(() => expect(region.querySelector('svg')).toBeTruthy());
    });

    it('draws the ranked timeline with league glyphs (fractional WR scaled to percent)', async () => {
        // Mixed leagues exercise the glyph branch (star/square/circle) + the
        // league-less fallback, without throwing.
        mockFetch.mockReturnValue(resolved([
            { season_label: 'S14', total_battles: 300, win_rate: 0.57, start_date: '2024-09-15', highest_league_name: 'Gold' },
            { season_label: 'S13', total_battles: 120, win_rate: 0.51, start_date: '2024-03-01', highest_league_name: 'Silver' },
            { season_label: 'S12', total_battles: 80, win_rate: 0.48, start_date: '2023-06-01', highest_league_name: 'Bronze' },
            { season_label: 'S11', total_battles: 40, win_rate: 0.5, start_date: '2022-01-01' },
        ]));

        render(<RankedSeasonTimelineSVG playerId={2} theme="dark" />);
        const region = screen.getByRole('img', { name: /ranked season activity timeline/i });
        await waitFor(() => expect(region.querySelector('svg')).toBeTruthy());
    });

    it('scales markers by battles relative to the player record (min→1×, max→4×)', async () => {
        mockFetch.mockReturnValue(resolved([
            { season_label: 'CB1', battles: 10, win_rate: 50, start_date: '2020-01-01' },
            { season_label: 'CB2', battles: 100, win_rate: 55, start_date: '2022-01-01' },
        ]));

        render(<ClanBattleSeasonTimelineSVG playerId={9} theme="light" />);
        const region = screen.getByRole('img', { name: /clan battle season activity timeline/i });
        await waitFor(() => expect(region.querySelector('circle')).toBeTruthy());

        const radii = Array.from(region.querySelectorAll('circle')).map((circle) => Number(circle.getAttribute('r')));
        // Base radius 5 → fewest battles r5 (1×), most r20 (4×).
        expect(Math.min(...radii)).toBeCloseTo(5, 1);
        expect(Math.max(...radii)).toBeCloseTo(20, 1);
    });

    it('renders a placeholder when no season is dated/played', async () => {
        mockFetch.mockReturnValue(resolved([
            { season_label: 'CB1', battles: 0, win_rate: 0, start_date: null },
        ]));

        render(<ClanBattleSeasonTimelineSVG playerId={3} theme="light" />);
        const region = screen.getByRole('img', { name: /clan battle season activity timeline/i });
        await waitFor(() => expect(region.querySelector('svg')).toBeTruthy());
    });
});
