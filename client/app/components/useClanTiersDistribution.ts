import { useEffect, useRef, useState } from 'react';
import { getChartFetchesInFlight } from '../lib/sharedJsonFetch';
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
        let activeController: AbortController | null = null;
        attemptsRef.current = 0;

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
                } else if (showLoading && !controller.signal.aborted) {
                    setLoading(false);
                }
            } catch (fetchError) {
                if (isAbortError(fetchError)) {
                    return;
                }
                console.error('Error fetching clan tiers:', fetchError);
                if (!controller.signal.aborted) {
                    setError('Tier data unavailable');
                    setLoading(false);
                }
            }
        };

        void fetchData(true, 0);

        return () => {
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
            activeController?.abort();
        };
    }, [clanId, enabled, realm]);

    return { data, loading, error };
};
