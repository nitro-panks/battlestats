import { useEffect, useRef, useState } from 'react';
import type { ClanMemberData } from './clanMembersShared';
import { getChartFetchesInFlight } from '../lib/sharedJsonFetch';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';

const HYDRATION_POLL_LIMIT = 12;
const HYDRATION_ACTIVE_POLL_INTERVAL_MS = 3000;
const HYDRATION_DEFERRED_POLL_INTERVAL_MS = 6000;

interface ClanMembersHydrationState {
    rankedQueued: number;
    rankedDeferred: number;
    rankedPending: number;
    efficiencyQueued: number;
    efficiencyDeferred: number;
    efficiencyPending: number;
}

const isAbortError = (error: unknown): boolean => {
    return error instanceof DOMException && error.name === 'AbortError';
};

const readJsonOrThrow = async <T,>(response: Response, label: string): Promise<T> => {
    const contentType = response.headers.get('content-type') || '';

    if (!response.ok) {
        const body = await response.text();
        throw new Error(`${label} failed with ${response.status}: ${body.slice(0, 120)}`);
    }

    if (!contentType.toLowerCase().includes('application/json')) {
        const body = await response.text();
        throw new Error(`${label} returned non-JSON content: ${body.slice(0, 120)}`);
    }

    return response.json() as Promise<T>;
};

const readCountHeader = (response: Response, headerName: string): number => {
    const rawValue = response.headers.get(headerName);
    const parsedValue = Number.parseInt(rawValue || '0', 10);
    return Number.isFinite(parsedValue) ? Math.max(parsedValue, 0) : 0;
};

const readHydrationState = (response: Response): ClanMembersHydrationState => ({
    rankedQueued: readCountHeader(response, 'X-Ranked-Hydration-Queued'),
    rankedDeferred: readCountHeader(response, 'X-Ranked-Hydration-Deferred'),
    rankedPending: readCountHeader(response, 'X-Ranked-Hydration-Pending'),
    efficiencyQueued: readCountHeader(response, 'X-Efficiency-Hydration-Queued'),
    efficiencyDeferred: readCountHeader(response, 'X-Efficiency-Hydration-Deferred'),
    efficiencyPending: readCountHeader(response, 'X-Efficiency-Hydration-Pending'),
});

const resolveHydrationPollDelay = (state: ClanMembersHydrationState): number => {
    const queuedCount = state.rankedQueued + state.efficiencyQueued;
    const deferredCount = state.rankedDeferred + state.efficiencyDeferred;
    return deferredCount > 0 && queuedCount === 0
        ? HYDRATION_DEFERRED_POLL_INTERVAL_MS
        : HYDRATION_ACTIVE_POLL_INTERVAL_MS;
};

export const useClanMembers = (clanId: number | null | undefined, enabled = true) => {
    const { realm } = useRealm();
    const [members, setMembers] = useState<ClanMemberData[]>([]);
    const [loading, setLoading] = useState(Boolean(clanId));
    const [error, setError] = useState('');
    const attemptsRef = useRef(0);

    // When the clan changes, reset to loading state so the component never
    // flashes "No clan members found." while waiting for the fetch gate.
    useEffect(() => {
        if (clanId) {
            setMembers([]);
            setLoading(true);
            setError('');
        } else {
            setMembers([]);
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

        const fetchMembers = async (showLoading: boolean, attempt: number) => {
            if (showLoading) {
                setLoading(true);
            }

            activeController?.abort();
            const controller = new AbortController();
            activeController = controller;

            try {
                const response = await fetch(withRealm(`/api/fetch/clan_members/${clanId}`, realm), {
                    signal: controller.signal,
                });
                const data = await readJsonOrThrow<ClanMemberData[]>(response, `Clan members ${clanId}`);
                if (controller.signal.aborted) {
                    return;
                }

                setMembers(Array.isArray(data) ? data : []);
                setError('');

                const hydrationState = readHydrationState(response);
                const hasPendingHydration = data.some(
                    (member) => member.ranked_hydration_pending
                        || member.efficiency_hydration_pending,
                );
                const shouldPollAgain = hasPendingHydration
                    || hydrationState.rankedPending > 0
                    || hydrationState.efficiencyPending > 0;
                if (shouldPollAgain && attempt < HYDRATION_POLL_LIMIT) {
                    attemptsRef.current = attempt + 1;
                    const baseDelay = resolveHydrationPollDelay(hydrationState);
                    const priorityDelay = getChartFetchesInFlight() > 0
                        ? Math.max(baseDelay, HYDRATION_DEFERRED_POLL_INTERVAL_MS)
                        : baseDelay;
                    timeoutId = setTimeout(() => {
                        void fetchMembers(false, attempt + 1);
                    }, priorityDelay);
                }
            } catch (fetchError) {
                if (isAbortError(fetchError)) {
                    return;
                }

                console.error('Error fetching clan members:', fetchError);
                if (!controller.signal.aborted) {
                    setError('Unable to load clan members right now.');
                }
            } finally {
                if (showLoading && !controller.signal.aborted) {
                    setLoading(false);
                }
            }
        };

        void fetchMembers(true, 0);

        return () => {
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
            activeController?.abort();
        };
    }, [clanId, enabled, realm]);

    return { members, loading, error };
};