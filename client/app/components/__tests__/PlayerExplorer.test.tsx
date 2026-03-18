import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import PlayerExplorer from '../PlayerExplorer';

jest.mock('../HiddenAccountIcon', () => {
    return function MockHiddenAccountIcon() {
        return <span aria-label="Hidden account">hidden</span>;
    };
});

const buildExplorerResponse = (overrides: Partial<{
    count: number;
    page: number;
    page_size: number;
    results: Array<Record<string, unknown>>;
}> = {}) => ({
    count: 0,
    page: 1,
    page_size: 10,
    results: [],
    ...overrides,
});

const getLastRequestedUrl = (): URL => {
    const calls = (global.fetch as jest.Mock).mock.calls;
    const requestUrl = calls[calls.length - 1][0] as string;
    return new URL(requestUrl);
};

describe('PlayerExplorer', () => {
    beforeEach(() => {
        jest.useFakeTimers();
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    beforeEach(() => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            json: async () => buildExplorerResponse(),
        });
    });

    it('defaults to player score descending for ranking-first player views', async () => {
        render(<PlayerExplorer onSelectMember={() => undefined} />);

        await act(async () => {
            jest.advanceTimersByTime(180);
        });

        await waitFor(() => {
            expect(global.fetch).toHaveBeenCalled();
        });

        const requestUrl = getLastRequestedUrl();
        expect(requestUrl.searchParams.get('sort')).toBe('player_score');
        expect(requestUrl.searchParams.get('direction')).toBe('desc');
        expect(requestUrl.searchParams.get('hidden')).toBe('visible');
        expect(requestUrl.searchParams.get('activity_bucket')).toBe('30d');
    });

    it('renders explorer rows, formats metrics, and routes selected members', async () => {
        const onSelectMember = jest.fn();
        (global.fetch as jest.Mock).mockResolvedValue({
            ok: true,
            json: async () => buildExplorerResponse({
                count: 2,
                results: [
                    {
                        name: 'AcePlayer',
                        player_id: 101,
                        is_hidden: true,
                        pvp_ratio: 55.2,
                        pvp_battles: 8123,
                        account_age_days: 400,
                        ships_played_total: 91,
                        ranked_seasons_participated: 8,
                        kill_ratio: 1.68,
                        player_score: 987,
                        pvp_survival_rate: 42.4,
                    },
                    {
                        name: 'SparsePlayer',
                        player_id: 102,
                        is_hidden: false,
                        pvp_ratio: null,
                        pvp_battles: null,
                        account_age_days: null,
                        ships_played_total: null,
                        ranked_seasons_participated: null,
                        kill_ratio: null,
                        player_score: null,
                        pvp_survival_rate: null,
                    },
                ],
            }),
        });

        render(<PlayerExplorer onSelectMember={onSelectMember} />);

        await act(async () => {
            jest.advanceTimersByTime(180);
        });

        expect(await screen.findByRole('button', { name: /AcePlayer/i })).toBeInTheDocument();
        expect(screen.getByText('987')).toBeInTheDocument();
        expect(screen.getByText('8,123')).toBeInTheDocument();
        expect(screen.getByText('42.4%')).toBeInTheDocument();
        expect(screen.getByText('1.68')).toBeInTheDocument();
        expect(screen.getByText('55.2%')).toBeInTheDocument();
        expect(screen.getByLabelText('Hidden account')).toBeInTheDocument();
        expect(screen.getAllByText('—').length).toBeGreaterThan(0);

        fireEvent.click(screen.getByRole('button', { name: /AcePlayer/i }));
        expect(onSelectMember).toHaveBeenCalledWith('AcePlayer');
        expect(screen.getByText('2 matching players')).toBeInTheDocument();
    });

    it('updates filters, resets pagination, and requests the new parameter set', async () => {
        (global.fetch as jest.Mock)
            .mockResolvedValueOnce({
                ok: true,
                json: async () => buildExplorerResponse({ count: 25, page: 1 }),
            })
            .mockResolvedValueOnce({
                ok: true,
                json: async () => buildExplorerResponse({ count: 25, page: 2 }),
            })
            .mockResolvedValue({
                ok: true,
                json: async () => buildExplorerResponse({ count: 4, page: 1 }),
            });

        render(<PlayerExplorer onSelectMember={() => undefined} />);

        await act(async () => {
            jest.advanceTimersByTime(180);
        });
        await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));

        fireEvent.click(screen.getByRole('button', { name: 'Next' }));

        await act(async () => {
            jest.advanceTimersByTime(180);
        });
        await waitFor(() => expect(screen.getByText('Page 2 of 3')).toBeInTheDocument());

        fireEvent.change(screen.getByPlaceholderText('Filter players'), { target: { value: 'shima' } });
        fireEvent.change(screen.getByDisplayValue('Visible only'), { target: { value: 'all' } });
        fireEvent.change(screen.getByDisplayValue('Active in last 30 days'), { target: { value: '7d' } });
        fireEvent.change(screen.getByDisplayValue('All ranked states'), { target: { value: 'yes' } });
        fireEvent.change(screen.getByDisplayValue('Player score'), { target: { value: 'kill_ratio' } });
        fireEvent.change(screen.getByDisplayValue('Desc'), { target: { value: 'asc' } });

        await act(async () => {
            jest.advanceTimersByTime(180);
        });

        await waitFor(() => expect(screen.getByText('Page 1 of 1')).toBeInTheDocument());
        const requestUrl = getLastRequestedUrl();
        expect(requestUrl.searchParams.get('q')).toBe('shima');
        expect(requestUrl.searchParams.get('hidden')).toBe('all');
        expect(requestUrl.searchParams.get('activity_bucket')).toBe('7d');
        expect(requestUrl.searchParams.get('ranked')).toBe('yes');
        expect(requestUrl.searchParams.get('sort')).toBe('kill_ratio');
        expect(requestUrl.searchParams.get('direction')).toBe('asc');
        expect(requestUrl.searchParams.get('page')).toBe('1');
    });

    it('renders an empty state when no players match the current filters', async () => {
        render(<PlayerExplorer onSelectMember={() => undefined} />);

        await act(async () => {
            jest.advanceTimersByTime(180);
        });

        expect(await screen.findByText('No players matched the current explorer filters.')).toBeInTheDocument();
        expect(screen.getByText('0 matching players')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Prev' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
    });

    it('shows an error when explorer loading fails', async () => {
        const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => undefined);
        (global.fetch as jest.Mock).mockResolvedValue({
            ok: false,
            json: async () => buildExplorerResponse(),
        });

        render(<PlayerExplorer onSelectMember={() => undefined} />);

        await act(async () => {
            jest.advanceTimersByTime(180);
        });

        expect(await screen.findByText('Unable to load explorer data right now.')).toBeInTheDocument();
        expect(screen.getByText('No explorer data yet')).toBeInTheDocument();
        consoleErrorSpy.mockRestore();
    });
});