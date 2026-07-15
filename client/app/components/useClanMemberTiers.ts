import { useEffect, useState } from 'react';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';
import { fetchSharedJson, isAbortError } from '../lib/sharedJsonFetch';

export interface ClanMemberTier {
    player_id: number;
    name: string;
    avg_tier: number | null;
    kdr: number | null;
}

export const useClanMemberTiers = (clanId: number | null | undefined) => {
    const { realm } = useRealm();
    const [data, setData] = useState<ClanMemberTier[]>([]);
    const [loading, setLoading] = useState(Boolean(clanId));

    useEffect(() => {
        if (!clanId) {
            setData([]);
            setLoading(false);
            return;
        }

        let cancelled = false;
        const controller = new AbortController();

        const fetchData = async () => {
            setLoading(true);
            try {
                const { data: json } = await fetchSharedJson<ClanMemberTier[]>(
                    withRealm(`/api/fetch/clan_member_tiers/${clanId}`, realm),
                    { label: `clan_member_tiers ${clanId}`, signal: controller.signal },
                );
                if (!cancelled) {
                    setData(Array.isArray(json) ? json : []);
                }
            } catch (err) {
                if (isAbortError(err)) return;
                console.error('Error fetching clan member tiers:', err);
            } finally {
                if (!cancelled) setLoading(false);
            }
        };

        void fetchData();

        return () => {
            cancelled = true;
            controller.abort();
        };
    }, [clanId, realm]);

    return { data, loading };
};
