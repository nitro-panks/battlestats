import type { RankedLeagueName } from './rankedLeague';


export interface LandingClan {
    clan_id: number;
    name: string;
    tag: string;
    members_count: number;
    clan_wr: number | null;
    total_battles: number | null;
    active_members?: number | null;
    is_clan_battle_active?: boolean;
    avg_cb_battles?: number | null;
    avg_cb_wr?: number | null;
    cb_recency_days?: number | null;
}


export interface LandingPlayer {
    player_id?: number;
    name: string;
    pvp_ratio: number | null;
    is_hidden?: boolean;
    pvp_battles?: number | null;
    high_tier_pvp_ratio?: number | null;
    high_tier_pvp_battles?: number | null;
    is_pve_player?: boolean;
    is_sleepy_player?: boolean;
    is_ranked_player?: boolean;
    is_clan_battle_player?: boolean;
    clan_battle_win_rate?: number | null;
    clan_battle_total_battles?: number | null;
    clan_battle_seasons_participated?: number | null;
    highest_ranked_league?: RankedLeagueName | null;
    efficiency_rank_percentile?: number | null;
    efficiency_rank_tier?: 'E' | 'I' | 'II' | 'III' | null;
    has_efficiency_rank_icon?: boolean;
    efficiency_rank_population_size?: number | null;
    efficiency_rank_updated_at?: string | null;
}


export interface PlayerData {
    id: number;
    name: string;
    player_id: number;
    kill_ratio: number | null;
    actual_kdr?: number | null;
    player_score: number | null;
    total_battles: number;
    pvp_battles: number;
    pvp_wins: number;
    pvp_losses: number;
    pvp_ratio: number;
    pvp_survival_rate: number;
    wins_survival_rate: number | null;
    creation_date: string;
    days_since_last_battle: number;
    last_battle_date: string;
    recent_games: object;
    is_hidden: boolean;
    stats_updated_at: string;
    last_fetch: string;
    last_lookup: string | null;
    clan: number;
    clan_name: string;
    clan_tag: string | null;
    clan_id: number;
    verdict: string | null;
    highest_ranked_league?: RankedLeagueName | null;
    is_clan_leader?: boolean;
    is_pve_player?: boolean;
    efficiency_rank_percentile?: number | null;
    efficiency_rank_tier?: 'E' | 'I' | 'II' | 'III' | null;
    has_efficiency_rank_icon?: boolean;
    efficiency_rank_population_size?: number | null;
    efficiency_rank_updated_at?: string | null;
    clan_battle_header_eligible?: boolean;
    clan_battle_header_total_battles?: number | null;
    clan_battle_header_seasons_played?: number | null;
    clan_battle_header_overall_win_rate?: number | null;
    clan_battle_header_updated_at?: string | null;
    randoms_json?: Array<{
        ship_name?: string | null;
        ship_chart_name?: string | null;
        ship_type?: string | null;
        ship_tier?: number | null;
        pvp_battles?: number | null;
        wins?: number | null;
        win_ratio?: number | null;
    }> | null;
    efficiency_json?: Array<{
        ship_id?: number | null;
        top_grade_class?: number | null;
        top_grade_label?: string | null;
        badge_label?: string | null;
        ship_name?: string | null;
        ship_chart_name?: string | null;
        ship_type?: string | null;
        ship_tier?: number | null;
        nation?: string | null;
    }> | null;
    ranked_json?: Array<{
        total_battles?: number | null;
        total_wins?: number | null;
        win_rate?: number | null;
        highest_league?: number | null;
        highest_league_name?: RankedLeagueName | null;
    }> | null;
}