import type { RankedLeagueName } from './rankedLeague';

export type ActivityBucketKey = 'active_7d' | 'active_30d' | 'cooling_90d' | 'dormant_180d' | 'inactive_180d_plus' | 'unknown';

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
}