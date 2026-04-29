import React, { useCallback } from 'react';
import type { ClanMemberData } from './clanMembersShared';
import HiddenAccountIcon from './HiddenAccountIcon';
import EfficiencyRankIcon, { resolveEfficiencyRankTier } from './EfficiencyRankIcon';
import LeaderCrownIcon from './LeaderCrownIcon';
import TwitchStreamerIcon from './TwitchStreamerIcon';
import PveEnjoyerIcon from './PveEnjoyerIcon';
import InactiveIcon from './InactiveIcon';
import RankedPlayerIcon from './RankedPlayerIcon';
import ClanBattleShieldIcon from './ClanBattleShieldIcon';
import wrColor from '../lib/wrColor';
import { useFlipAnimation } from './useFlipAnimation';

interface ClanMembersProps {
    members: ClanMemberData[];
    onSelectMember: (memberName: string) => void;
    layout?: 'inline' | 'stacked';
    loading?: boolean;
    error?: string;
}

const formatRecency = (daysSinceLastBattle: number | null): string => {
    if (daysSinceLastBattle == null) return 'activity unknown';
    if (daysSinceLastBattle === 0) return 'played today';
    if (daysSinceLastBattle === 1) return '1 day idle';
    return `${daysSinceLastBattle} days idle`;
};

interface MemberContentProps {
    member: ClanMemberData;
    layout: 'inline' | 'stacked';
    onSelectMember: (memberName: string) => void;
}

const MemberContent: React.FC<MemberContentProps> = ({ member, layout, onSelectMember }) => {
    const efficiencyRankTier = !member.is_hidden
        ? resolveEfficiencyRankTier(member.efficiency_rank_tier, member.has_efficiency_rank_icon)
        : null;

    if (member.is_hidden) {
        return (
            <span
                className={layout === 'stacked'
                    ? 'flex items-center gap-1 font-medium text-[var(--text-secondary)]'
                    : 'mr-3 inline-flex items-center gap-1 font-medium text-[var(--text-secondary)]'}
                title={formatRecency(member.days_since_last_battle)}
            >
                <span style={{ color: wrColor(member.pvp_ratio) }} aria-hidden="true">{"◆"}</span>
                {member.name}
                <HiddenAccountIcon className="text-[11px] text-[var(--accent-light)]" />
                {member.is_leader && <LeaderCrownIcon size="inline" />}
                {member.is_streamer && <TwitchStreamerIcon size="inline" />}
                {member.is_pve_player && <PveEnjoyerIcon size="inline" />}
                {member.is_sleepy_player && <InactiveIcon size="inline" />}
                {member.is_ranked_player && <RankedPlayerIcon league={member.highest_ranked_league} size="inline" />}
                {member.is_clan_battle_player && <ClanBattleShieldIcon winRate={member.clan_battle_win_rate} size="inline" />}
                {efficiencyRankTier === 'E' ? <EfficiencyRankIcon tier={efficiencyRankTier} percentile={member.efficiency_rank_percentile} populationSize={member.efficiency_rank_population_size} size="inline" /> : null}
                <span className="text-xs font-normal text-[var(--text-secondary)]">{formatRecency(member.days_since_last_battle)}</span>
            </span>
        );
    }

    return (
        <button
            onClick={() => onSelectMember(member.name)}
            className={layout === 'stacked'
                ? 'flex items-center gap-1 font-medium text-[var(--accent-dark)] underline-offset-2 hover:underline hover:text-[var(--accent-mid)]'
                : 'mr-3 inline-flex items-center gap-1 font-medium text-[var(--accent-dark)] underline-offset-2 hover:underline hover:text-[var(--accent-mid)]'}
            aria-label={`Show player ${member.name}`}
            title={formatRecency(member.days_since_last_battle)}
        >
            <span style={{ color: wrColor(member.pvp_ratio) }} aria-hidden="true">{"◆"}</span>
            {member.name}
            {member.is_leader && <LeaderCrownIcon size="inline" />}
            {member.is_streamer && <TwitchStreamerIcon size="inline" />}
            {member.is_pve_player && <PveEnjoyerIcon size="inline" />}
            {member.is_sleepy_player && <InactiveIcon size="inline" />}
            {member.is_ranked_player && <RankedPlayerIcon league={member.highest_ranked_league} size="inline" />}
            {member.is_clan_battle_player && <ClanBattleShieldIcon winRate={member.clan_battle_win_rate} size="inline" />}
            {efficiencyRankTier === 'E' ? <EfficiencyRankIcon tier={efficiencyRankTier} percentile={member.efficiency_rank_percentile} populationSize={member.efficiency_rank_population_size} size="inline" /> : null}
            <span className="text-xs font-normal text-[var(--text-secondary)]">{formatRecency(member.days_since_last_battle)}</span>
        </button>
    );
};

const ClanMembers: React.FC<ClanMembersProps> = ({ members, onSelectMember, layout = 'inline', loading = false, error = '' }) => {
    const pendingEfficiencyCount = members.filter((member) => member.efficiency_hydration_pending).length;
    const isWarmingEfficiencyRanks = pendingEfficiencyCount > 0;

    // FLIP animation: only meaningful in stacked layout where rows have a
    // vertical position. Inline layout flows horizontally and rewrap is
    // already visually ambient.
    const animatedKeys = layout === 'stacked' ? members.map((m) => m.name) : [];
    const { register } = useFlipAnimation(animatedKeys);
    const makeRowRef = useCallback((name: string) => (el: HTMLDivElement | null) => {
        register(name, el);
    }, [register]);

    return (
        <div>
            <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Clan Members</h3>
            {loading && <p className="text-sm text-[var(--text-secondary)]">Syncing clan members...</p>}
            {!loading && error ? <p className="text-sm text-[var(--text-secondary)]">{error}</p> : null}
            {!loading && !error && isWarmingEfficiencyRanks ? (
                <p className="shimmer-green text-sm font-medium">
                    {`Updating: ${pendingEfficiencyCount} member${pendingEfficiencyCount === 1 ? '' : 's'}.`}
                </p>
            ) : null}
            {!loading && members.length === 0 && <p className="text-sm text-[var(--text-secondary)]">No clan members found.</p>}
            {!loading && members.length > 0 && (
                <div className={layout === 'stacked' ? 'mt-2 space-y-1 text-sm text-[var(--accent-light)]' : 'mt-2 text-sm leading-7 text-[var(--accent-light)]'}>
                    {members.map((member) => (
                        layout === 'stacked' ? (
                            <div key={member.name} ref={makeRowRef(member.name)} className="will-change-transform">
                                <MemberContent member={member} layout={layout} onSelectMember={onSelectMember} />
                            </div>
                        ) : (
                            <React.Fragment key={member.name}>
                                <MemberContent member={member} layout={layout} onSelectMember={onSelectMember} />
                            </React.Fragment>
                        )
                    ))}
                </div>
            )}
        </div>
    );
};

export default ClanMembers;
