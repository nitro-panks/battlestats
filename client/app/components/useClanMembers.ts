import { useEffect, useRef, useState } from 'react';
import type { ClanMemberData } from './clanMembersShared';
import { fetchSharedJson, getChartFetchesInFlight, isAbortError } from '../lib/sharedJsonFetch';
import { degradationMonitor } from '../lib/degradationMonitor';
import { isPlayerDewaterfallEnabled } from '../lib/featureFlags';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';

const HYDRATION_POLL_LIMIT = 12;
const HYDRATION_ACTIVE_POLL_INTERVAL_MS = 3000;
const HYDRATION_DEFERRED_POLL_INTERVAL_MS = 6000;

type ClanMembersHeaders = Record<string, string | null>;

const HYDRATION_HEADER_NAMES = [
    'X-Ranked-Hydration-Queued',
    'X-Ranked-Hydration-Deferred',
    'X-Ranked-Hydration-Pending',
    'X-Efficiency-Hydration-Queued',
    'X-Efficiency-Hydration-Deferred',
    'X-Efficiency-Hydration-Pending',
    'X-Clan-Idle-Pending',
];

interface ClanMembersHydrationState {
    rankedQueued: number;
    rankedDeferred: number;
    rankedPending: number;
    efficiencyQueued: number;
    efficiencyDeferred: number;
    efficiencyPending: number;
}

const readCountHeader = (headers: ClanMembersHeaders, headerName: string): number => {
    const parsedValue = Number.parseInt(headers[headerName] || '0', 10);
    return Number.isFinite(parsedValue) ? Math.max(parsedValue, 0) : 0;
};

const readHydrationState = (headers: ClanMembersHeaders): ClanMembersHydrationState => ({
    rankedQueued: readCountHeader(headers, 'X-Ranked-Hydration-Queued'),
    rankedDeferred: readCountHeader(headers, 'X-Ranked-Hydration-Deferred'),
    rankedPending: readCountHeader(headers, 'X-Ranked-Hydration-Pending'),
    efficiencyQueued: readCountHeader(headers, 'X-Efficiency-Hydration-Queued'),
    efficiencyDeferred: readCountHeader(headers, 'X-Efficiency-Hydration-Deferred'),
    efficiencyPending: readCountHeader(headers, 'X-Efficiency-Hydration-Pending'),
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
        let gateIntervalId: ReturnType<typeof setInterval> | null = null;
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
                const { data, headers } = await fetchSharedJson<ClanMemberData[]>(
                    withRealm(`/api/fetch/clan_members/${clanId}`, realm),
                    {
                        label: `Clan members ${clanId}`,
                        responseHeaders: HYDRATION_HEADER_NAMES,
                        ttlMs: 0, // polled freshness — never serve a cached body
                        priority: 'high', // above-the-fold rail
                        signal: controller.signal,
                        cacheKey: `clan-members:${clanId}:${realm}:${attempt}`,
                    },
                );

                const rows = Array.isArray(data) ? data : [];
                setMembers(rows);
                setError('');

                const hydrationState = readHydrationState(headers);
                const hasPendingHydration = rows.some(
                    (member) => member.ranked_hydration_pending
                        || member.efficiency_hydration_pending,
                );
                // Roster idle bulk-refresh in flight: re-poll so corrected
                // "X days idle" (fresh last_battle_date) replaces stale values.
                const idlePending = headers['X-Clan-Idle-Pending'] === 'true';
                const shouldPollAgain = hasPendingHydration
                    || idlePending
                    || hydrationState.rankedPending > 0
                    || hydrationState.efficiencyPending > 0;
                if (shouldPollAgain && attempt < HYDRATION_POLL_LIMIT) {
                    attemptsRef.current = attempt + 1;
                    const baseDelay = resolveHydrationPollDelay(hydrationState);
                    const priorityDelay = (getChartFetchesInFlight() > 0
                        ? Math.max(baseDelay, HYDRATION_DEFERRED_POLL_INTERVAL_MS)
                        : baseDelay) * degradationMonitor.getPollIntervalMultiplier();
                    timeoutId = setTimeout(() => {
                        void fetchMembers(false, attempt + 1);
                    }, priorityDelay);
                } else if (shouldPollAgain) {
                    // Reached the poll cap with hydration still pending. Clear the
                    // per-member pending flags so the "Updating N members" banner
                    // does not stick at a non-zero count after we stop polling.
                    // See runbook-clan-members-hydration-wedge-2026-04-07.md.
                    setMembers((current) => current.map((member) => (
                        member.ranked_hydration_pending || member.efficiency_hydration_pending
                            ? { ...member, ranked_hydration_pending: false, efficiency_hydration_pending: false }
                            : member
                    )));
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

        // De-waterfall: fetch the roster immediately, in parallel with the chart
        // warmup, instead of waiting for chart fetches to drain. The clan rail is
        // above-the-fold and has no data dependency on the charts.
        if (!isPlayerDewaterfallEnabled() && getChartFetchesInFlight() > 0) {
            gateIntervalId = setInterval(() => {
                if (getChartFetchesInFlight() === 0) {
                    clearInterval(gateIntervalId!);
                    gateIntervalId = null;
                    void fetchMembers(true, 0);
                }
            }, 500);
        } else {
            void fetchMembers(true, 0);
        }

        return () => {
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
            if (gateIntervalId) {
                clearInterval(gateIntervalId);
            }
            activeController?.abort();
        };
    }, [clanId, enabled, realm]);

    return { members, loading, error };
};