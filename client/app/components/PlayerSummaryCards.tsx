import React, { useEffect, useState } from 'react';

interface PlayerSummaryCardsProps {
    playerId: number;
    isLoading?: boolean;
}

interface PlayerSummaryData {
    battles_last_29_days: number | null;
    active_days_last_29_days: number | null;
    recent_win_rate: number | null;
    ships_played_total: number | null;
    ranked_seasons_participated: number | null;
}

const formatMetric = (value: number | null | undefined, formatter?: (input: number) => string): string => {
    if (value == null) {
        return '—';
    }

    return formatter ? formatter(value) : value.toLocaleString();
};

const PlayerSummaryCards: React.FC<PlayerSummaryCardsProps> = ({ playerId, isLoading = false }) => {
    const [summary, setSummary] = useState<PlayerSummaryData | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [isSummaryLoading, setIsSummaryLoading] = useState(false);

    useEffect(() => {
        let isMounted = true;

        const fetchSummary = async () => {
            setIsSummaryLoading(true);
            setError(null);

            try {
                const response = await fetch(`/api/fetch/player_summary/${playerId}`);
                if (!response.ok) {
                    throw new Error(`Failed to load player summary for ${playerId}`);
                }

                const result: PlayerSummaryData = await response.json();
                if (!isMounted) {
                    return;
                }

                setSummary(result);
            } catch (fetchError) {
                if (!isMounted) {
                    return;
                }

                console.error('Error fetching player summary:', fetchError);
                setError('Unable to load summary metrics right now.');
                setSummary(null);
            } finally {
                if (isMounted) {
                    setIsSummaryLoading(false);
                }
            }
        };

        fetchSummary();

        return () => {
            isMounted = false;
        };
    }, [playerId]);

    const shouldGrayOut = isLoading || isSummaryLoading;
    const cards = [
        {
            label: '29D Battles',
            value: formatMetric(summary?.battles_last_29_days),
        },
        {
            label: 'Active Days',
            value: formatMetric(summary?.active_days_last_29_days),
        },
        {
            label: 'Recent WR',
            value: formatMetric(summary?.recent_win_rate, (value) => `${(value * 100).toFixed(1)}%`),
        },
        {
            label: 'Ships Played',
            value: formatMetric(summary?.ships_played_total),
        },
        {
            label: 'Ranked Seasons',
            value: formatMetric(summary?.ranked_seasons_participated),
        },
    ];

    return (
        <div>
            {error ? (
                <p className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                    {error}
                </p>
            ) : null}
            <div className={shouldGrayOut ? 'pointer-events-none opacity-60 grayscale transition' : 'transition'} aria-busy={shouldGrayOut}>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                    {cards.map((card) => (
                        <div key={card.label} className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] p-3">
                            <p className="text-[11px] uppercase tracking-wide text-[var(--accent-light)]">{card.label}</p>
                            <p className="mt-1 text-xl font-semibold text-[var(--accent-dark)]">{card.value}</p>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
};

export default PlayerSummaryCards;