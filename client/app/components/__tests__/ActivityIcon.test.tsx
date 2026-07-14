import React from 'react';
import { render, screen } from '@testing-library/react';
import ActivityIcon, { activityBucketFromDays } from '../ActivityIcon';

describe('activityBucketFromDays', () => {
    it('maps day counts to buckets on the same thresholds as the backend', () => {
        expect(activityBucketFromDays(0)).toBe('active_7d');
        expect(activityBucketFromDays(7)).toBe('active_7d');
        expect(activityBucketFromDays(8)).toBe('active_30d');
        expect(activityBucketFromDays(30)).toBe('active_30d');
        expect(activityBucketFromDays(31)).toBe('cooling_90d');
        expect(activityBucketFromDays(90)).toBe('cooling_90d');
        expect(activityBucketFromDays(91)).toBe('dormant_180d');
        expect(activityBucketFromDays(180)).toBe('dormant_180d');
        expect(activityBucketFromDays(181)).toBe('inactive_180d_plus');
        expect(activityBucketFromDays(null)).toBe('unknown');
        expect(activityBucketFromDays(undefined)).toBe('unknown');
    });
});

describe('ActivityIcon', () => {
    it('labels an explicit bucket with the matching rise-to-bed phase', () => {
        render(<ActivityIcon bucket="active_7d" />);
        expect(screen.getByLabelText(/Active —/i)).toBeInTheDocument();
    });

    it('derives the phase from a day count when no bucket is given', () => {
        render(<ActivityIcon daysSinceLastBattle={120} />);
        expect(screen.getByLabelText(/Cold —/i)).toBeInTheDocument();
    });

    it('renders nothing for unknown / missing recency', () => {
        const { container } = render(<ActivityIcon daysSinceLastBattle={null} />);
        expect(container).toBeEmptyDOMElement();
    });
});
