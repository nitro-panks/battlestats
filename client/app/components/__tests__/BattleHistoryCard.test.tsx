import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import BattleHistoryCard, { type BattleHistoryPayload } from '../BattleHistoryCard';
import { fetchSharedJson } from '../../lib/sharedJsonFetch';

jest.mock('../../lib/sharedJsonFetch', () => ({
    fetchSharedJson: jest.fn(),
}));

const mockFetchSharedJson = fetchSharedJson as jest.MockedFunction<typeof fetchSharedJson>;

const buildPayload = (overrides: Partial<BattleHistoryPayload> = {}): BattleHistoryPayload => ({
    window_days: 7,
    as_of: '2026-04-28T18:30:00Z',
    totals: {
        battles: 8,
        wins: 5,
        losses: 3,
        win_rate: 62.5,
        damage: 382_400,
        avg_damage: 47_800,
        frags: 15,
        xp: 21_400,
        planes_killed: 0,
        survived_battles: 4,
        survival_rate: 50.0,
    },
    by_ship: [
        {
            ship_id: 42,
            ship_name: 'Yamato',
            ship_tier: 10,
            ship_type: 'Battleship',
            battles: 6,
            wins: 4,
            losses: 2,
            win_rate: 66.7,
            damage: 287_400,
            avg_damage: 47_900,
            frags: 12,
            xp: 16_400,
            planes_killed: 0,
            survived_battles: 3,
        },
        {
            ship_id: 43,
            ship_name: 'Dalian',
            ship_tier: 9,
            ship_type: 'Destroyer',
            battles: 2,
            wins: 1,
            losses: 1,
            win_rate: 50.0,
            damage: 95_000,
            avg_damage: 47_500,
            frags: 3,
            xp: 5_000,
            planes_killed: 0,
            survived_battles: 1,
        },
    ],
    by_day: [
        { date: '2026-04-27', battles: 3, wins: 2, damage: 142_300, frags: 6 },
        { date: '2026-04-28', battles: 5, wins: 3, damage: 240_100, frags: 9 },
    ],
    ...overrides,
});

const resolveWith = (payload: BattleHistoryPayload) => {
    mockFetchSharedJson.mockResolvedValueOnce({ data: payload, headers: {} });
};

describe('BattleHistoryCard', () => {
    beforeEach(() => {
        mockFetchSharedJson.mockReset();
    });

    test('renders the totals row, sparkline, and per-ship table once the API resolves', async () => {
        resolveWith(buildPayload());
        render(<BattleHistoryCard playerName="lil_boots" realm="na" />);

        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });

        expect(screen.getByText(/Last 7 days/i)).toBeInTheDocument();
        // Two ships present, sorted Yamato first.
        const rows = screen.getAllByRole('row');
        // 1 header + 2 data rows.
        expect(rows.length).toBe(3);
        expect(rows[1].textContent).toContain('Yamato');
        expect(rows[2].textContent).toContain('Dalian');
        // Win-rate cell renders the percentage with one decimal.
        expect(screen.getByText('66.7%')).toBeInTheDocument();
        expect(screen.getByText('50.0%')).toBeInTheDocument();
        // Sparkline svg is present.
        expect(screen.getByLabelText('Battles per day sparkline')).toBeInTheDocument();
    });

    test('renders nothing while loading', () => {
        // Never resolve.
        mockFetchSharedJson.mockReturnValueOnce(new Promise(() => {}));
        const { container } = render(<BattleHistoryCard playerName="x" realm="na" />);
        expect(container).toBeEmptyDOMElement();
    });

    test('renders nothing when totals.battles is zero', async () => {
        resolveWith(buildPayload({
            totals: {
                battles: 0, wins: 0, losses: 0, win_rate: 0,
                damage: 0, avg_damage: 0, frags: 0, xp: 0,
                planes_killed: 0, survived_battles: 0, survival_rate: 0,
            },
            by_ship: [],
            by_day: [],
        }));
        const { container } = render(<BattleHistoryCard playerName="empty" realm="na" />);
        // Wait for the fetch to settle; the component should stay empty.
        await waitFor(() => {
            expect(mockFetchSharedJson).toHaveBeenCalled();
        });
        await new Promise((r) => setTimeout(r, 0));
        expect(container).toBeEmptyDOMElement();
    });

    test('renders nothing when the API returns 404 (capture API disabled)', async () => {
        mockFetchSharedJson.mockRejectedValueOnce(new Error('404 not found'));
        const { container } = render(<BattleHistoryCard playerName="x" realm="na" />);
        await waitFor(() => {
            expect(mockFetchSharedJson).toHaveBeenCalled();
        });
        await new Promise((r) => setTimeout(r, 0));
        expect(container).toBeEmptyDOMElement();
    });

    test('passes period + windows + realm to the URL on initial daily fetch', () => {
        mockFetchSharedJson.mockReturnValueOnce(new Promise(() => {}));
        render(<BattleHistoryCard playerName="lil_boots" realm="eu" days={14} />);
        expect(mockFetchSharedJson).toHaveBeenCalledTimes(1);
        const [url] = mockFetchSharedJson.mock.calls[0];
        expect(url).toContain('/api/player/lil_boots/battle-history/');
        expect(url).toContain('period=daily');
        expect(url).toContain('windows=14');
        expect(url).toContain('realm=eu');
    });
});
