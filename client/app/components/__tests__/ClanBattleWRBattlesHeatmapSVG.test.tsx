import { render, waitFor } from '@testing-library/react';
import ClanBattleWRBattlesHeatmapSVG from '../ClanBattleWRBattlesHeatmapSVG';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
}));
const mockFetch = fetchSharedJson as jest.Mock;

const basePayload = {
    metric: 'clan_battle_wr_battles' as const,
    label: 'Clan Battles vs Win Rate',
    x_label: 'Total Clan Battles',
    y_label: 'Clan Battle Win Rate',
    tracked_population: 3,
    correlation: 0.2,
    x_scale: 'log' as const,
    y_scale: 'linear' as const,
    x_ticks: [50, 100],
    x_edges: [50, 71, 100, 141],
    y_domain: { min: 30, max: 70, bin_width: 0.75 },
    tiles: [{ x_index: 1, y_index: 20, count: 5 }],
    trend: [{ x_index: 1, y: 50, count: 5 }],
};

describe('ClanBattleWRBattlesHeatmapSVG', () => {
    beforeEach(() => mockFetch.mockReset());

    it('draws and reports visible when the player has a CB point', async () => {
        mockFetch.mockResolvedValue({ data: { ...basePayload, player_point: { x: 150, y: 51, label: 'Tester' } }, headers: {} });
        const onVisibilityChange = jest.fn();

        const { container } = render(<ClanBattleWRBattlesHeatmapSVG playerId={1} theme="light" onVisibilityChange={onVisibilityChange} />);
        await waitFor(() => expect(onVisibilityChange).toHaveBeenCalledWith(true));
        expect(container.querySelector('svg')).toBeTruthy();
    });

    it('hides only itself (visible=false, cleared) when the player has no CB point', async () => {
        mockFetch.mockResolvedValue({ data: { ...basePayload, player_point: null }, headers: {} });
        const onVisibilityChange = jest.fn();

        const { container } = render(<ClanBattleWRBattlesHeatmapSVG playerId={2} theme="dark" onVisibilityChange={onVisibilityChange} />);
        await waitFor(() => expect(onVisibilityChange).toHaveBeenCalledWith(false));
        expect(container.querySelector('svg')).toBeNull();
    });
});
