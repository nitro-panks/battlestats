import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import StreamerSubmissionModal from '../StreamerSubmissionModal';

const trackEventMock = jest.fn();
jest.mock('../../lib/umami', () => ({
    trackEvent: (...args: unknown[]) => trackEventMock(...args),
}));

const fillValidForm = () => {
    fireEvent.change(screen.getByPlaceholderText(/in-game name/i), { target: { value: 'CaptainTest' } });
    fireEvent.change(screen.getByPlaceholderText(/Twitch handle without/i), { target: { value: 'captaintest' } });
    fireEvent.change(screen.getByPlaceholderText(/full Twitch channel URL/i), { target: { value: 'https://twitch.tv/captaintest' } });
};

const submitForm = async () => {
    await act(async () => {
        fireEvent.submit(screen.getByRole('button', { name: 'Submit' }).closest('form')!);
    });
};

describe('StreamerSubmissionModal submit tracking', () => {
    beforeEach(() => {
        trackEventMock.mockReset();
        (global.fetch as jest.Mock) = jest.fn();
    });

    it('fires streamer-submit {status: success} on a 201 response', async () => {
        (global.fetch as jest.Mock).mockResolvedValue({ status: 201 });
        render(<StreamerSubmissionModal open onClose={() => undefined} />);
        fillValidForm();

        await submitForm();

        await waitFor(() => {
            expect(trackEventMock).toHaveBeenCalledWith('streamer-submit', { status: 'success' });
        });
    });

    it('fires streamer-submit {status: invalid} on a 400 response', async () => {
        (global.fetch as jest.Mock).mockResolvedValue({
            status: 400,
            json: async () => ({ ign: ['already submitted'] }),
        });
        render(<StreamerSubmissionModal open onClose={() => undefined} />);
        fillValidForm();

        await submitForm();

        await waitFor(() => {
            expect(trackEventMock).toHaveBeenCalledWith('streamer-submit', { status: 'invalid' });
        });
    });

    it('fires streamer-submit {status: error} when the request throws', async () => {
        (global.fetch as jest.Mock).mockRejectedValue(new Error('network down'));
        render(<StreamerSubmissionModal open onClose={() => undefined} />);
        fillValidForm();

        await submitForm();

        await waitFor(() => {
            expect(trackEventMock).toHaveBeenCalledWith('streamer-submit', { status: 'error' });
        });
    });
});
