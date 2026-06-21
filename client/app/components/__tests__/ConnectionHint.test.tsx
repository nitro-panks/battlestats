import { render, screen, act } from '@testing-library/react';
import ConnectionHint from '../ConnectionHint';
import { DegradationProvider } from '../../context/DegradationContext';
import { degradationMonitor } from '../../lib/degradationMonitor';

const HINT = /connection is slow/i;

describe('ConnectionHint', () => {
    beforeEach(() => {
        degradationMonitor.reset();
    });
    afterEach(() => {
        degradationMonitor.reset();
    });

    it('renders nothing while the network is healthy', () => {
        render(
            <DegradationProvider>
                <ConnectionHint />
            </DegradationProvider>,
        );
        expect(screen.queryByText(HINT)).not.toBeInTheDocument();
    });

    it('shows the subtle hint once the monitor degrades', () => {
        render(
            <DegradationProvider>
                <ConnectionHint />
            </DegradationProvider>,
        );
        expect(screen.queryByText(HINT)).not.toBeInTheDocument();

        act(() => {
            degradationMonitor.record({ kind: 'throttled', status: 429, durationMs: 5 });
        });

        expect(screen.getByText(HINT)).toBeInTheDocument();
    });
});
