import { useEffect, useRef, useState } from 'react';
import { getChartFetchesInFlight, incrementChartFetches, decrementChartFetches } from '../lib/sharedJsonFetch';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';

// Represents { ship_tier: 1..11, pvp_battles: number }
export interface ClanTierData {
    ship_tier: number;
    pvp_battles: number;
}

const POLLING_LIMIT = 10;
const POLLING_INTERVAL_MS = 2000;

const isAbortError = (error: unknown): boolean => {
    return error instanceof DOMException && error.name === 'AbortError';
};

export const useClanTiersDistribution = (clanId: number | null | undefined, enabled = true) => {
    const { realm } = useRealm();
    const [data, setData] = useState<ClanTierData[]>([]);
    const [loading, setLoading] = useState(Boolean(clanId));
    const [error, setError] = useState('');
    const attemptsRef = useRef(0);

    useEffect(() => {
        if (clanId) {
            setData([]);
            setLoading(true);
            setError('');
        } else {
            setData([]);
            setLoading(false);
            setError('');
        }
        attemptsRef.current = 0;
    }, [clanId]);

    useEffect(() => {
        if (!clanId || !enabled) {
            return;
        }

        let timeoutId: ReturnType<typeof setTimeout> | null = null;
        let gateIntervalId: ReturnType<typeof setInterval> | null = null;
        let activeController: AbortController | null = null;
        let chartFetchSignalled = false;
        attemptsRef.current = 0;

        const acquireChartSignal = () => {
            if (!chartFetchSignalled) {
                chartFetchSignalled = true;
                incrementChartFetches();
            }
        };

        const releaseChartSignal = () => {
            if (chartFetchSignalled) {
                chartFetchSignalled = false;
                decrementChartFetches();
            }
        };

        const fetchData = async (showLoading: boolean, attempt: number) => {
            if (showLoading) {
                setLoading(true);
            }

            activeController?.abort();
            const controller = new AbortController();
            activeController = controller;

            try {
                const response = await fetch(withRealm(`/api/fetch/clan_tiers/${clanId}`, realm), {
                    signal: controller.signal,
                });

                if (!response.ok) {
                    throw new Error(`Clan tiers failed with ${response.status}`);
                }

                const json = await response.json() as ClanTierData[];
                if (controller.signal.aborted) {
                    return;
                }

                setData(Array.isArray(json) ? json : []);
                setError('');

                const isPending = response.headers.get('X-Clan-Tiers-Pending') === 'true';
                if (isPending && attempt < POLLING_LIMIT) {
                    attemptsRef.current = attempt + 1;
                    const priorityDelay = getChartFetchesInFlight() > 0 ? 5000 : POLLING_INTERVAL_MS;
                    timeoutId = setTimeout(() => {
                        void fetchData(false, attempt + 1);
                    }, priorityDelay);
                } else if (!controller.signal.aborted) {
                    setLoading(false);
                    releaseChartSignal();
                }
            } catch (fetchError) {
                if (isAbortError(fetchError)) {
                    return;
                }
                console.error('Error fetching clan tiers:', fetchError);
                if (!controller.signal.aborted) {
                    setError('Tier data unavailable');
                    setLoading(false);
                    releaseChartSignal();
                }
            }
        };

        const startFetch = () => {
            acquireChartSignal();
            void fetchData(true, 0);
        };

        // Always defer the first check by one tick so that ClanSVG's
        // dynamic import has time to mount and signal chartFetchesInFlight.
        gateIntervalId = setInterval(() => {
            if (getChartFetchesInFlight() === 0) {
                clearInterval(gateIntervalId!);
                gateIntervalId = null;
                startFetch();
            }
        }, 500);

        return () => {
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
            if (gateIntervalId) {
                clearInterval(gateIntervalId);
            }
            activeController?.abort();
            releaseChartSignal();
        };
    }, [clanId, enabled, realm]);

    return { data, loading, error };
};
