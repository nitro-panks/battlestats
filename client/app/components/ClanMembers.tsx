import React, { useCallback } from 'react';
import type { ClanMemberData } from './clanMembersShared';
import HiddenAccountIcon from './HiddenAccountIcon';
import EfficiencyRankIcon, { resolveEfficiencyRankTier } from './EfficiencyRankIcon';
import LeaderCrownIcon from './LeaderCrownIcon';
import TwitchStreamerIcon from './TwitchStreamerIcon';
import PveEnjoyerIcon, { PVE_ENJOYER_ICON_ENABLED } from './PveEnjoyerIcon';
import ActivityIcon, { ACTIVITY_SHORT_LABEL, activityColor } from './ActivityIcon';
import type { ActivityBucketKey, CollapsedActivityBucketKey } from './clanMembersShared';
import RankedPlayerIcon from './RankedPlayerIcon';
import ClanBattleShieldIcon from './ClanBattleShieldIcon';
import TopShipBadges from './TopShipBadges';
import wrColor from '../lib/wrColor';
import { useFlipAnimation } from './useFlipAnimation';
import { trackEvent } from '../lib/umami';
import { useRealm } from '../context/RealmContext';

// The clan-page activity table shows one column per presented activity phase.
// The backend's five raw states route into the three phases (Active ≤30d,
// Cooling 31–180d, Gone dark 181d+); `icon` is the phase icon shown in the
// column header. Per-row phase icons are redundant with the header now that
// every column is a single phase, so none are rendered.
interface ActivityColumnSpec {
    key: string;
    label: string;
    icon: CollapsedActivityBucketKey;
    memberBuckets: ActivityBucketKey[];
}

const COLUMN_SPECS: ActivityColumnSpec[] = [
    { key: 'active', label: 'Active now', icon: 'active_7d', memberBuckets: ['active_7d', 'active_30d'] },
    { key: 'cooling', label: 'Cooling off', icon: 'cooling_90d', memberBuckets: ['cooling_90d', 'dormant_180d', 'unknown'] },
    { key: 'dark', label: 'Gone dark', icon: 'inactive_180d_plus', memberBuckets: ['inactive_180d_plus'] },
];

// Stacked rail: one bounding box per presented activity phase, in recency
// order. `key` is the box's legend icon; `buckets` are the raw member states
// routed into it (the cooling box also absorbs any unknown recency, as the
// columns layout does). Empty boxes are skipped at render.
interface StatusBoxSpec {
    key: CollapsedActivityBucketKey;
    buckets: ActivityBucketKey[];
}

const STATUS_BOXES: StatusBoxSpec[] = [
    { key: 'active_7d', buckets: ['active_7d', 'active_30d'] },
    { key: 'cooling_90d', buckets: ['cooling_90d', 'dormant_180d', 'unknown'] },
    { key: 'inactive_180d_plus', buckets: ['inactive_180d_plus'] },
];

interface ClanMembersProps {
    members: ClanMemberData[];
    onSelectMember: (memberName: string) => void;
    // 'inline'  — horizontal flowing list (clan page legacy)
    // 'stacked' — vertical rows (player page rail)
    // 'columns' — one column per activity state, grouped + empty states skipped
    layout?: 'inline' | 'stacked' | 'columns';
    loading?: boolean;
    error?: string;
    // Which surface this roster is rendered on — distinguishes a click on the
    // clan page from one in the player-page clan section (both share this leaf).
    source?: 'clan' | 'player';
    // When set, the row whose name matches is rendered as the current player:
    // a non-interactive "you are here" marker instead of a self-link.
    highlightedPlayerName?: string;
}

interface MemberContentProps {
    member: ClanMemberData;
    layout: 'inline' | 'stacked';
    isCurrentPlayer: boolean;
    // When the surface already groups by activity (the columns layout), the
    // per-row activity icon is redundant with the column header, so it's hidden.
    showActivity: boolean;
    onSelectMember: (memberName: string) => void;
}

const MemberContent: React.FC<MemberContentProps> = ({ member, layout, isCurrentPlayer, showActivity, onSelectMember }) => {
    const efficiencyRankTier = !member.is_hidden
        ? resolveEfficiencyRankTier(member.efficiency_rank_tier, member.has_efficiency_rank_icon)
        : null;

    // Name + classification badges + activity icon are identical across every
    // row variant (link / hidden / current player); only the wrapper differs.
    // The activity icon (rise-to-bed sun/moon metaphor) replaces the old raw
    // "Nd idle" text and subsumes the separate gone-dark bed badge.
    const rowBody = (
        <>
            <span className="shrink-0" style={{ color: wrColor(member.pvp_ratio) }} aria-hidden="true">{"◆"}</span>
            {showActivity && <ActivityIcon bucket={member.activity_bucket} size="inline" />}
            {/* In the fixed-width rail (stacked) a long name truncates so the
                trailing classification icons keep their space; elsewhere it flows. */}
            <span className={layout === 'stacked' ? 'min-w-0 truncate' : undefined}>{member.name}</span>
            {member.is_hidden && <HiddenAccountIcon className="text-[11px] text-[var(--accent-light)]" />}
            {member.is_leader && <LeaderCrownIcon size="inline" />}
            {member.is_streamer && <TwitchStreamerIcon size="inline" />}
            {PVE_ENJOYER_ICON_ENABLED && member.is_pve_player && <PveEnjoyerIcon size="inline" />}
            {member.is_ranked_player && <RankedPlayerIcon league={member.highest_ranked_league} size="inline" />}
            {member.is_clan_battle_player && <ClanBattleShieldIcon winRate={member.clan_battle_win_rate} size="inline" />}
            {efficiencyRankTier === 'E' ? <EfficiencyRankIcon tier={efficiencyRankTier} percentile={member.efficiency_rank_percentile} populationSize={member.efficiency_rank_population_size} size="inline" /> : null}
            <TopShipBadges badges={member.ship_badges} realm={member.realm} size="inline" />
        </>
    );

    const baseLayout = layout === 'stacked' ? 'flex w-full min-w-0 items-center gap-1' : 'mr-3 inline-flex items-center gap-1';

    // Current player: the page you're already on. Render a non-interactive
    // "you are here" marker (no self-link) in the same gold the clan activity
    // chart uses to mark this same player, so the dot and the row agree. In the
    // stacked rail it reads as a full-width active band with a gold edge.
    if (isCurrentPlayer) {
        const markerLayout = layout === 'stacked'
            ? 'flex w-full min-w-0 items-center gap-1 overflow-hidden rounded-r-md border-l-2 border-[var(--champion-edge)] bg-[var(--champion-tint)] py-0.5 pl-2 pr-2'
            : 'mr-3 inline-flex items-center gap-1 rounded-md border border-[var(--champion-edge)] bg-[var(--champion-tint)] px-1.5 py-0.5';
        return (
            <span
                className={`${markerLayout} cursor-default font-semibold text-[var(--text-strong)]`}
                aria-current="page"
                title="You're viewing this player"
            >
                {rowBody}
            </span>
        );
    }

    if (member.is_hidden) {
        return (
            <span
                className={`${baseLayout} font-medium text-[var(--text-secondary)]`}
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
        >
            {rowBody}
        </button>
    );
};

const ClanMembers: React.FC<ClanMembersProps> = ({ members, onSelectMember, layout = 'inline', loading = false, error = '', highlightedPlayerName, source = 'clan' }) => {
    const { realm } = useRealm();
    const normalizedCurrentPlayer = highlightedPlayerName?.trim().toLowerCase() || null;
    // One attach point for clan-roster member clicks: this leaf renders the
    // roster on both the clan page and the player page's clan section, so
    // tracking here covers both without double-counting the landing player grid.
    // `source` tells which surface drove the click ('clan' vs 'player').
    const handleSelectMember = useCallback((memberName: string) => {
        trackEvent('clan-member-click', { realm, source });
        onSelectMember(memberName);
    }, [onSelectMember, realm, source]);

    // FLIP animation: only meaningful in stacked layout where rows have a
    // vertical position. Inline layout flows horizontally and rewrap is
    // already visually ambient.
    const animatedKeys = layout === 'stacked' ? members.map((m) => m.name) : [];
    const { register } = useFlipAnimation(animatedKeys);
    const makeRowRef = useCallback((name: string) => (el: HTMLDivElement | null) => {
        register(name, el);
    }, [register]);

    // Columns layout: one column per phase — active (≤30d), cooling (31–180d,
    // plus any unknown recency), and gone dark. Empty columns are skipped, and
    // members keep the backend's activity sort.
    const activityColumns = (layout === 'columns' ? COLUMN_SPECS : [])
        .map((spec) => ({
            ...spec,
            members: members.filter((member) => spec.memberBuckets.includes(member.activity_bucket)),
        }))
        .filter((column) => column.members.length > 0);

    // Stacked rail (player page): group the roster into ruled per-phase boxes
    // carrying a header status-icon legend (so the per-row activity icon is
    // dropped). Empty boxes drop out; 'unknown' recency folds into the cooling
    // box, matching the columns layout.
    const statusBoxesFlat = (layout === 'stacked' ? STATUS_BOXES : [])
        .map((box) => ({
            ...box,
            members: members.filter((member) => box.buckets.includes(member.activity_bucket)),
        }))
        .filter((box) => box.members.length > 0);

    return (
        <div>
            {/* The player rail drops the label; its clan name above already
                identifies the roster. The clan page keeps it. */}
            {layout !== 'stacked' && <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Clan Members</h3>}
            {loading && <p className="text-sm text-[var(--text-secondary)]">Syncing clan members...</p>}
            {!loading && error ? <p className="text-sm text-[var(--text-secondary)]">{error}</p> : null}
            {!loading && members.length === 0 && <p className="text-sm text-[var(--text-secondary)]">No clan members found.</p>}
            {!loading && members.length > 0 && layout === 'columns' && (
                <div
                    className="mt-3 grid gap-x-6 gap-y-6 overflow-x-auto text-sm text-[var(--accent-light)]"
                    style={{ gridTemplateColumns: `repeat(${activityColumns.length}, minmax(150px, 1fr))` }}
                >
                    {activityColumns.map((column) => (
                        <div key={column.key} className="min-w-0">
                            <div className="mb-1.5 flex items-center gap-1.5 border-b border-transparent pb-1">
                                <ActivityIcon bucket={column.icon} size="header" />
                                <span className="text-xs font-semibold uppercase tracking-wide text-[var(--text-secondary)]">{column.label}</span>
                                <span className="text-xs font-normal text-[var(--text-secondary)]">{column.members.length}</span>
                            </div>
                            <div className="space-y-0.5">
                                {column.members.map((member) => (
                                    <div key={member.name}>
                                        <MemberContent member={member} layout="stacked" isCurrentPlayer={false} showActivity={false} onSelectMember={handleSelectMember} />
                                    </div>
                                ))}
                            </div>
                        </div>
                    ))}
                </div>
            )}
            {!loading && members.length > 0 && layout === 'stacked' && (
                <div>
                    {statusBoxesFlat.map((box, boxIndex) => {
                        // The old horizontal rules are gone but their 1px slots remain
                        // (transparent borders), so the group rhythm is unchanged.
                        const isFirst = boxIndex === 0;
                        return (
                            <div key={box.key} className={`w-full border-b border-transparent py-2 pb-[11px]${isFirst ? ' border-t' : ''}`}>
                                {/* Status legend: a header row — the box's activity icon
                                    followed by a short status word in the status color —
                                    flush left with the rule above it, standing in for the
                                    per-row icons below. */}
                                <div className="mb-1 flex items-center gap-1.5">
                                    <ActivityIcon bucket={box.key} size="header" />
                                    <span className="text-xs font-semibold" style={{ color: activityColor(box.key) }}>{ACTIVITY_SHORT_LABEL[box.key]}</span>
                                </div>
                                <div className="space-y-1 pl-[20px] text-sm text-[var(--accent-light)]">
                                    {box.members.map((member) => {
                                        const isCurrentPlayer = normalizedCurrentPlayer != null
                                            && member.name.trim().toLowerCase() === normalizedCurrentPlayer;
                                        return (
                                            <div key={member.name} ref={makeRowRef(member.name)} className="will-change-transform">
                                                <MemberContent member={member} layout="stacked" isCurrentPlayer={isCurrentPlayer} showActivity={false} onSelectMember={handleSelectMember} />
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
            {!loading && members.length > 0 && layout === 'inline' && (
                <div className="mt-2 text-sm leading-7 text-[var(--accent-light)]">
                    {members.map((member) => {
                        const isCurrentPlayer = normalizedCurrentPlayer != null
                            && member.name.trim().toLowerCase() === normalizedCurrentPlayer;
                        return (
                            <React.Fragment key={member.name}>
                                <MemberContent member={member} layout="inline" isCurrentPlayer={isCurrentPlayer} showActivity onSelectMember={handleSelectMember} />
                            </React.Fragment>
                        );
                    })}
                </div>
            )}
        </div>
    );
};

export default ClanMembers;
