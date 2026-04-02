import { useEffect, useState } from 'react';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';

export interface ClanMemberTier {
    player_id: number;
    name: string;
    avg_tier: number | null;
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
                const response = await fetch(
                    withRealm(`/api/fetch/clan_member_tiers/${clanId}`, realm),
                    { signal: controller.signal },
                );
                if (!response.ok) {
                    throw new Error(`clan_member_tiers failed: ${response.status}`);
                }
                const json = await response.json() as ClanMemberTier[];
                if (!cancelled) {
                    setData(Array.isArray(json) ? json : []);
                }
            } catch (err) {
                if (err instanceof DOMException && err.name === 'AbortError') return;
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
