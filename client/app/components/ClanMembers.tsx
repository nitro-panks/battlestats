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
import TopShipBadges from './TopShipBadges';
import wrColor from '../lib/wrColor';
import { useFlipAnimation } from './useFlipAnimation';
import { trackEvent } from '../lib/umami';
import { useRealm } from '../context/RealmContext';

interface ClanMembersProps {
    members: ClanMemberData[];
    onSelectMember: (memberName: string) => void;
    layout?: 'inline' | 'stacked';
    loading?: boolean;
    error?: string;
    // When set, the row whose name matches is rendered as the current player:
    // a non-interactive "you are here" marker instead of a self-link.
    highlightedPlayerName?: string;
}

const formatRecency = (daysSinceLastBattle: number | null): string => {
    if (daysSinceLastBattle == null) return 'activity unknown';
    if (daysSinceLastBattle === 0) return 'played today';
    return `${daysSinceLastBattle}d idle`;
};

interface MemberContentProps {
    member: ClanMemberData;
    layout: 'inline' | 'stacked';
    isCurrentPlayer: boolean;
    onSelectMember: (memberName: string) => void;
}

const MemberContent: React.FC<MemberContentProps> = ({ member, layout, isCurrentPlayer, onSelectMember }) => {
    const efficiencyRankTier = !member.is_hidden
        ? resolveEfficiencyRankTier(member.efficiency_rank_tier, member.has_efficiency_rank_icon)
        : null;

    // Name + classification badges + idle recency are identical across every
    // row variant (link / hidden / current player); only the wrapper differs.
    const rowBody = (
        <>
            <span style={{ color: wrColor(member.pvp_ratio) }} aria-hidden="true">{"◆"}</span>
            {member.name}
            {member.is_hidden && <HiddenAccountIcon className="text-[11px] text-[var(--accent-light)]" />}
            {member.is_leader && <LeaderCrownIcon size="inline" />}
            {member.is_streamer && <TwitchStreamerIcon size="inline" />}
            {member.is_pve_player && <PveEnjoyerIcon size="inline" />}
            {member.is_sleepy_player && <InactiveIcon size="inline" />}
            {member.is_ranked_player && <RankedPlayerIcon league={member.highest_ranked_league} size="inline" />}
            {member.is_clan_battle_player && <ClanBattleShieldIcon winRate={member.clan_battle_win_rate} size="inline" />}
            {efficiencyRankTier === 'E' ? <EfficiencyRankIcon tier={efficiencyRankTier} percentile={member.efficiency_rank_percentile} populationSize={member.efficiency_rank_population_size} size="inline" /> : null}
            <TopShipBadges badges={member.ship_badges} realm={member.realm} size="inline" />
            <span className="whitespace-nowrap text-xs font-normal text-[var(--text-secondary)]">{formatRecency(member.days_since_last_battle)}</span>
        </>
    );

    const baseLayout = layout === 'stacked' ? 'flex items-center gap-1' : 'mr-3 inline-flex items-center gap-1';

    // Current player: the page you're already on. Render a non-interactive
    // "you are here" marker (no self-link) in the same gold the clan activity
    // chart uses to mark this same player, so the dot and the row agree. In the
    // stacked rail it reads as a full-width active band with a gold edge; the
    // "you" tag is pinned right (ml-auto) so the recency never wraps.
    if (isCurrentPlayer) {
        const markerLayout = layout === 'stacked'
            ? 'flex w-full items-center gap-1 overflow-hidden rounded-r-md border-l-2 border-[var(--champion-edge)] bg-[var(--champion-tint)] py-0.5 pl-2 pr-2'
            : 'mr-3 inline-flex items-center gap-1 rounded-md border border-[var(--champion-edge)] bg-[var(--champion-tint)] px-1.5 py-0.5';
        return (
            <span
                className={`${markerLayout} cursor-default font-semibold text-[var(--text-strong)]`}
                aria-current="page"
                title="You're viewing this player"
            >
                {rowBody}
                <span className="text-[10px] font-semibold uppercase tracking-wide text-[var(--metal-gold)]">you</span>
            </span>
        );
    }

    if (member.is_hidden) {
        return (
            <span
                className={`${baseLayout} font-medium text-[var(--text-secondary)]`}
                title={formatRecency(member.days_since_last_battle)}
            >
                {rowBody}
            </span>
        );
    }

    return (
        <button
            onClick={() => onSelectMember(member.name)}
            className={`${baseLayout} font-medium text-[var(--accent-dark)] underline-offset-2 hover:underline hover:text-[var(--accent-mid)]`}
            aria-label={`Show player ${member.name}`}
            title={formatRecency(member.days_since_last_battle)}
        >
            {rowBody}
        </button>
    );
};

const ClanMembers: React.FC<ClanMembersProps> = ({ members, onSelectMember, layout = 'inline', loading = false, error = '', highlightedPlayerName }) => {
    const { realm } = useRealm();
    const normalizedCurrentPlayer = highlightedPlayerName?.trim().toLowerCase() || null;
    // One attach point for clan-roster member clicks: this leaf renders the
    // roster on both the clan page and the player page's clan section, so
    // tracking here covers both without double-counting the landing player grid.
    const handleSelectMember = useCallback((memberName: string) => {
        trackEvent('clan-member-click', { realm });
        onSelectMember(memberName);
    }, [onSelectMember, realm]);
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
                    {members.map((member) => {
                        const isCurrentPlayer = normalizedCurrentPlayer != null
                            && member.name.trim().toLowerCase() === normalizedCurrentPlayer;
                        return layout === 'stacked' ? (
                            <div key={member.name} ref={makeRowRef(member.name)} className="will-change-transform">
                                <MemberContent member={member} layout={layout} isCurrentPlayer={isCurrentPlayer} onSelectMember={handleSelectMember} />
                            </div>
                        ) : (
                            <React.Fragment key={member.name}>
                                <MemberContent member={member} layout={layout} isCurrentPlayer={isCurrentPlayer} onSelectMember={handleSelectMember} />
                            </React.Fragment>
                        );
                    })}
                </div>
            )}
        </div>
    );
};

export default ClanMembers;
