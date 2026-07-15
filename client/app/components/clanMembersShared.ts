import type { RankedLeagueName } from './rankedLeague';
import type { ShipBadge } from './ShipTopPlayerBanner';

export type ActivityBucketKey = 'active_7d' | 'active_30d' | 'cooling_90d' | 'dormant_180d' | 'inactive_180d_plus' | 'unknown';

// The UI presents three activity phases; the backend payload still carries the
// finer five-way `activity_bucket` (contract unchanged). Every surface collapses
// the raw bucket through this map before styling: Active (≤30d), Cooling
// (31–180d), Gone dark (181d+).
export type CollapsedActivityBucketKey = 'active_7d' | 'cooling_90d' | 'inactive_180d_plus';

export const collapseActivityBucket = (bucket: ActivityBucketKey): CollapsedActivityBucketKey | 'unknown' => {
    if (bucket === 'active_30d') return 'active_7d';
    if (bucket === 'dormant_180d') return 'cooling_90d';
    return bucket;
};

export interface ClanMemberData {
    name: string;
    is_hidden: boolean;
    is_streamer?: boolean;
    pvp_ratio: number | null;
    days_since_last_battle: number | null;
    is_leader: boolean;
    is_pve_player: boolean;
    is_sleepy_player: boolean;
    is_ranked_player: boolean;
    is_clan_battle_player: boolean;
    clan_battle_win_rate: number | null;
    efficiency_hydration_pending?: boolean;
    highest_ranked_league: RankedLeagueName | null;
    ranked_hydration_pending: boolean;
    ranked_updated_at: string | null;
    efficiency_rank_percentile?: number | null;
    efficiency_rank_tier?: 'E' | 'I' | 'II' | 'III' | null;
    has_efficiency_rank_icon?: boolean;
    efficiency_rank_population_size?: number | null;
    efficiency_rank_updated_at?: string | null;
    activity_bucket: ActivityBucketKey;
    realm?: string;
    ship_badges?: ShipBadge[];
}