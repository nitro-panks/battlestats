import React from 'react';
import { render, waitFor } from '@testing-library/react';
import Clan3DSVG from '../Clan3DSVG';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../context/RealmContext', () => ({ useRealm: () => ({ realm: 'na' }) }));
jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
    incrementChartFetches: jest.fn(),
    decrementChartFetches: jest.fn(),
}));

const fetchMock = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

const plotData = [
    { player_name: 'Alpha', pvp_battles: 5200, pvp_ratio: 54.2 },
    { player_name: 'Bravo', pvp_battles: 800, pvp_ratio: 48.9 },
    { player_name: 'Charlie', pvp_battles: 15000, pvp_ratio: 61.0 },
];

const memberTiers = [
    { player_id: 1, name: 'Alpha', avg_tier: 8.2, kdr: 1.4 },
    { player_id: 2, name: 'Bravo', avg_tier: 6.1, kdr: 0.8 },
    { player_id: 3, name: 'Charlie', avg_tier: 9.4, kdr: 2.1 },
];

describe('Clan3DSVG', () => {
    beforeEach(() => {
        fetchMock.mockReset();
        // jsdom has no matchMedia; reduced-motion=true also keeps the test free
        // of the auto-rotate requestAnimationFrame loop.
        window.matchMedia = jest.fn().mockReturnValue({
            matches: true,
            addEventListener: jest.fn(),
            removeEventListener: jest.fn(),
        }) as unknown as typeof window.matchMedia;
    });

    it('renders a scaling viewBox svg instead of a fixed pixel width', async () => {
        fetchMock.mockResolvedValue({ data: plotData, headers: { 'X-Clan-Plot-Pending': null } });

        const { container } = render(
            <Clan3DSVG clanId={500123} svgWidth={850} svgHeight={480} memberTiers={memberTiers} />,
        );

        await waitFor(() => {
            expect(container.querySelector('svg')).not.toBeNull();
        });

        const svg = container.querySelector('svg') as SVGSVGElement;
        // The coordinate space is fixed; the rendered size must track the
        // container (viewBox + width:100%), so phones never overflow.
        expect(svg.getAttribute('viewBox')).toBe('0 0 850 480');
        expect(svg.getAttribute('width')).toBeNull();
        expect(svg.style.width).toBe('100%');
    });

    it('surfaces the error state when every fetch attempt fails', async () => {
        fetchMock.mockRejectedValue(new Error('boom'));

        const { findByText } = render(
            <Clan3DSVG clanId={500124} memberTiers={memberTiers} />,
        );

        expect(await findByText(/unable to load clan chart data/i)).toBeInTheDocument();
    });
});
