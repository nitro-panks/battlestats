import { useEffect, useRef, useState } from 'react';
import type { ClanMemberData } from './clanMembersShared';

const HYDRATION_POLL_LIMIT = 6;
const HYDRATION_POLL_INTERVAL_MS = 2500;

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

export const useClanMembers = (clanId: number | null | undefined, enabled = true) => {
    const [members, setMembers] = useState<ClanMemberData[]>([]);
    const [loading, setLoading] = useState(Boolean(clanId && enabled));
    const [error, setError] = useState('');
    const attemptsRef = useRef(0);

    useEffect(() => {
        if (!clanId || !enabled) {
            setMembers([]);
            setLoading(false);
            setError('');
            attemptsRef.current = 0;
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
                const response = await fetch(`http://localhost:8888/api/fetch/clan_members/${clanId}/`, {
                    signal: controller.signal,
                });
                const data = await readJsonOrThrow<ClanMemberData[]>(response, `Clan members ${clanId}`);
                if (controller.signal.aborted) {
                    return;
                }

                setMembers(Array.isArray(data) ? data : []);
                setError('');

                const hasPendingHydration = data.some(
                    (member) => member.ranked_hydration_pending
                        || member.efficiency_hydration_pending,
                );
                if (hasPendingHydration && attempt < HYDRATION_POLL_LIMIT) {
                    attemptsRef.current = attempt + 1;
                    timeoutId = setTimeout(() => {
                        void fetchMembers(false, attempt + 1);
                    }, HYDRATION_POLL_INTERVAL_MS);
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
    }, [clanId, enabled]);

    return { members, loading, error };
};