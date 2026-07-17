"use client";

import React, { useMemo } from 'react';
import Link from 'next/link';
import ActivityIcon, { activityColor } from './ActivityIcon';
import HiddenAccountIcon from './HiddenAccountIcon';
import EfficiencyRankIcon, { resolveEfficiencyRankTier } from './EfficiencyRankIcon';
import LeaderCrownIcon from './LeaderCrownIcon';
import TwitchStreamerIcon from './TwitchStreamerIcon';
import PveEnjoyerIcon, { PVE_ENJOYER_ICON_ENABLED } from './PveEnjoyerIcon';
import RankedPlayerIcon from './RankedPlayerIcon';
import ClanBattleShieldIcon from './ClanBattleShieldIcon';
import TopShipBadges from './TopShipBadges';
import { collapseActivityBucket, type ClanMemberData, type CollapsedActivityBucketKey } from './clanMembersShared';
import { buildPlayerPath } from '../lib/entityRoutes';
import { useRealm } from '../context/RealmContext';
import { trackEvent } from '../lib/umami';

// Shared clan roster: member names, each carrying the classification-badge
// tail. Presentation varies by surface (`phaseStyle`): the clan page groups
// names into one flowing paragraph (✦ dividers) per collapsed activity phase
// (Active / Cooling Off / Gone dark) under an icon-and-color header; the
// player page's clan section splits the roster in two column grids — the
// active phase under its label, then a rule and one unlabeled alphabetical
// block of everyone else (the scatterplot above tells the finer-grained
// activity story per member). Grid column count adapts to the block's size
// (up to 4), so a 4-member clan reads as one row and rows grow from there.

interface ClanActivityRosterProps {
    members: ClanMemberData[];
    loading?: boolean;
    error?: string;
    // When set, the matching member renders as bold plain text (you-are-here),
    // not a self-link.
    highlightedPlayerName?: string;
    // Which surface drove a member click — clan page vs player-page section.
    source?: 'clan' | 'player';
    // 'headers' (default): one paragraph per activity phase, icon+label header.
    // 'split': active members under the Active label, an <hr>, then all other
    // members as one unlabeled alphabetical block; both blocks lay out as
    // size-adaptive column grids.
    phaseStyle?: 'headers' | 'split';
}

type PhaseKey = CollapsedActivityBucketKey | 'unknown';

const PHASES: Array<{ key: PhaseKey; label: string }> = [
    { key: 'active_7d', label: 'Active now' },
    { key: 'cooling_90d', label: 'Cooling Off' },
    { key: 'inactive_180d_plus', label: 'Gone dark' },
    { key: 'unknown', label: 'No recency' },
];

const byName = (a: ClanMemberData, b: ClanMemberData): number =>
    a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });

// Column grid for the split-style roster blocks: as many columns as members,
// capped at 4 (2 on narrow viewports), so a 4-member clan reads as one row of
// 4 and a 16-member clan as 4 rows of 4; larger clans grow rows. Literal
// class maps keep the dynamic counts visible to the Tailwind JIT.
const GRID_COLS_BASE: Record<number, string> = { 1: 'grid-cols-1', 2: 'grid-cols-2' };
const GRID_COLS_SM: Record<number, string> = { 1: 'sm:grid-cols-1', 2: 'sm:grid-cols-2', 3: 'sm:grid-cols-3', 4: 'sm:grid-cols-4' };
const rosterGridClass = (count: number): string => {
    const base = GRID_COLS_BASE[Math.max(1, Math.min(2, count))];
    const sm = GRID_COLS_SM[Math.max(1, Math.min(4, count))];
    return `grid ${base} ${sm} gap-x-4 gap-y-1`;
};

const ClanActivityRoster: React.FC<ClanActivityRosterProps> = ({ members, loading = false, error = '', highlightedPlayerName, source = 'clan', phaseStyle = 'headers' }) => {
    const { realm } = useRealm();

    const membersByPhase = useMemo(() => {
        const groups: Record<PhaseKey, ClanMemberData[]> = {
            active_7d: [],
            cooling_90d: [],
            inactive_180d_plus: [],
            unknown: [],
        };
        members.forEach((member) => {
            groups[collapseActivityBucket(member.activity_bucket)].push(member);
        });
        (Object.keys(groups) as PhaseKey[]).forEach((key) => {
            groups[key].sort(byName);
        });
        return groups;
    }, [members]);

    const handleMemberClick = () => trackEvent('clan-member-click', { realm, source });

    if (loading && members.length === 0) {
        return <p className="text-sm text-[var(--text-secondary)]" data-testid="clan-activity-roster">Loading clan members...</p>;
    }
    if (error && members.length === 0) {
        return <p className="text-sm text-[var(--text-secondary)]" data-testid="clan-activity-roster">{error}</p>;
    }
    if (members.length === 0) {
        return <p className="text-sm text-[var(--text-secondary)]" data-testid="clan-activity-roster">No clan members found.</p>;
    }

    // Name + classification-badge tail. `fit` constrains the label to its
    // container (grid cell): the name truncates with an ellipsis instead of
    // overflowing the column.
    const renderMemberLabel = (member: ClanMemberData, fit = false) => {
        const efficiencyRankTier = !member.is_hidden
            ? resolveEfficiencyRankTier(member.efficiency_rank_tier, member.has_efficiency_rank_icon)
            : null;
        const wrapFit = fit ? ' max-w-full' : '';
        const nameFit = fit ? 'min-w-0 truncate' : undefined;
        // Same classification-badge dispatch as the player-header tray; the
        // per-member activity icon is omitted — the phase header (clan page)
        // or the scatterplot (player page) carries recency.
        const badges = (
            <>
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
        if (member.name === highlightedPlayerName) {
            return (
                <span className={`inline-flex items-center gap-1${wrapFit} font-semibold text-[var(--text-primary)]`}>
                    <span className={nameFit} title={fit ? member.name : undefined}>{member.name}</span>
                    {badges}
                </span>
            );
        }
        if (member.is_hidden) {
            // Hidden accounts are named but never clickable.
            return (
                <span className={`inline-flex items-center gap-1${wrapFit} text-[var(--text-secondary)]`}>
                    <span className={nameFit} title={fit ? member.name : undefined}>{member.name}</span>
                    {badges}
                </span>
            );
        }
        return (
            <span className={`inline-flex items-center gap-1${wrapFit}`}>
                <Link
                    href={buildPlayerPath(member.name, realm)}
                    onClick={handleMemberClick}
                    className={`text-[var(--accent-mid)] underline-offset-2 hover:underline${nameFit ? ` ${nameFit}` : ''}`}
                    title={fit ? member.name : undefined}
                >
                    {member.name}
                </Link>
                {badges}
            </span>
        );
    };

    // Flowing-paragraph form (clan page): ✦ divider between names.
    const renderMember = (member: ClanMemberData, index: number) => (
        <React.Fragment key={member.name}>
            {index > 0 ? (
                <span className="mx-1.5 text-xs text-[var(--text-secondary)]" aria-hidden="true">✦</span>
            ) : null}
            {renderMemberLabel(member)}
        </React.Fragment>
    );

    if (phaseStyle === 'split') {
        const activeMembers = membersByPhase.active_7d;
        // Everyone not currently active — cooling, gone dark, and unknown —
        // re-sorted into one alphabetical block; deliberately unlabeled (the
        // rule is the whole distinction).
        const otherMembers = [
            ...membersByPhase.cooling_90d,
            ...membersByPhase.inactive_180d_plus,
            ...membersByPhase.unknown,
        ].sort(byName);
        return (
            // One text step up from the phase-grouped text-sm — this roster is
            // the section's only text surface, so the names carry more size.
            <div data-testid="clan-activity-roster">
                {activeMembers.length > 0 ? (
                    <>
                        <h3
                            // pt-3 opens a little air between the scatterplot
                            // above and the label (padding, not margin — a
                            // margin here collapses into the section wrapper's
                            // own mt and changes nothing).
                            className="pt-3 flex items-center gap-2 text-base font-semibold uppercase tracking-wide"
                            style={{ color: activityColor('active_7d') }}
                        >
                            <ActivityIcon bucket="active_7d" size="header" />
                            <span>Active Members ({activeMembers.length})</span>
                        </h3>
                        <ul className={`mt-2.5 ${rosterGridClass(activeMembers.length)} text-base leading-7`} data-testid="clan-roster-active">
                            {activeMembers.map((member) => (
                                <li key={member.name} className="min-w-0">
                                    {renderMemberLabel(member, true)}
                                </li>
                            ))}
                        </ul>
                    </>
                ) : null}
                {activeMembers.length > 0 && otherMembers.length > 0 ? (
                    <hr className="my-4 border-[var(--border)]" />
                ) : null}
                {otherMembers.length > 0 ? (
                    <ul className={`${rosterGridClass(otherMembers.length)} text-base leading-7`} data-testid="clan-roster-others">
                        {otherMembers.map((member) => (
                            <li key={member.name} className="min-w-0">
                                {renderMemberLabel(member, true)}
                            </li>
                        ))}
                    </ul>
                ) : null}
            </div>
        );
    }

    return (
        <div className="space-y-6" data-testid="clan-activity-roster">
            {PHASES.map(({ key, label }) => {
                const phaseMembers = membersByPhase[key];
                if (phaseMembers.length === 0) {
                    return null;
                }
                return (
                    <div key={key} data-testid={`clan-phase-${key}`}>
                        <h3
                            className="flex items-center gap-2 text-base font-semibold uppercase tracking-wide"
                            style={{ color: key === 'unknown' ? 'var(--text-secondary)' : activityColor(key) }}
                        >
                            {key !== 'unknown' ? <ActivityIcon bucket={key} size="header" /> : null}
                            <span>{label} ({phaseMembers.length})</span>
                        </h3>
                        <p className="mt-2.5 text-sm leading-6">
                            {phaseMembers.map(renderMember)}
                        </p>
                    </div>
                );
            })}
        </div>
    );
};

export default ClanActivityRoster;
