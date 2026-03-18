import React from 'react';
import { render, waitFor } from '@testing-library/react';
import PlayerExplorer from '../PlayerExplorer';


describe('PlayerExplorer', () => {
    beforeEach(() => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                count: 0,
                page: 1,
                page_size: 10,
                results: [],
            }),
        });
    });

    it('defaults to player score descending for ranking-first player views', async () => {
        render(<PlayerExplorer onSelectMember={() => undefined} />);

        await waitFor(() => {
            expect(global.fetch).toHaveBeenCalled();
        });

        const requestUrl = (global.fetch as jest.Mock).mock.calls[0][0] as string;
        expect(requestUrl).toContain('sort=player_score');
        expect(requestUrl).toContain('direction=desc');
    });
});