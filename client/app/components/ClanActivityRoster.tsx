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

// Shared clan roster: one flowing paragraph of member names per collapsed
// activity phase (Active / Cooling Off / Gone dark), each under an
// icon-and-color phase header, names separated by a ✦ divider and carrying the
// classification-badge tail. Renders on the clan page and in the player page's
// clan section (which passes the viewed player for the non-link highlight).

interface ClanActivityRosterProps {
    members: ClanMemberData[];
    loading?: boolean;
    error?: string;
    // When set, the matching member renders as bold plain text (you-are-here),
    // not a self-link.
    highlightedPlayerName?: string;
    // Which surface drove a member click — clan page vs player-page section.
    source?: 'clan' | 'player';
}

type PhaseKey = CollapsedActivityBucketKey | 'unknown';

const PHASES: Array<{ key: PhaseKey; label: string }> = [
    { key: 'active_7d', label: 'Active now' },
    { key: 'cooling_90d', label: 'Cooling Off' },
    { key: 'inactive_180d_plus', label: 'Gone dark' },
    { key: 'unknown', label: 'No recency' },
];

const ClanActivityRoster: React.FC<ClanActivityRosterProps> = ({ members, loading = false, error = '', highlightedPlayerName, source = 'clan' }) => {
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
            groups[key].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
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
                        {phaseMembers.map((member, index) => {
                            const efficiencyRankTier = !member.is_hidden
                                ? resolveEfficiencyRankTier(member.efficiency_rank_tier, member.has_efficiency_rank_icon)
                                : null;
                            // Same classification-badge dispatch as the player-header
                            // tray; the activity icon is omitted — the paragraph
                            // header carries the phase.
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
                            return (
                                <React.Fragment key={member.name}>
                                    {index > 0 ? (
                                        <span className="mx-1.5 text-xs text-[var(--text-secondary)]" aria-hidden="true">✦</span>
                                    ) : null}
                                    {member.name === highlightedPlayerName ? (
                                        <span className="inline-flex items-center gap-1 font-semibold text-[var(--text-primary)]">
                                            <span>{member.name}</span>
                                            {badges}
                                        </span>
                                    ) : member.is_hidden ? (
                                        // Hidden accounts are named but never clickable.
                                        <span className="inline-flex items-center gap-1 text-[var(--text-secondary)]">
                                            <span>{member.name}</span>
                                            {badges}
                                        </span>
                                    ) : (
                                        <span className="inline-flex items-center gap-1">
                                            <Link
                                                href={buildPlayerPath(member.name, realm)}
                                                onClick={handleMemberClick}
                                                className="text-[var(--accent-mid)] underline-offset-2 hover:underline"
                                            >
                                                {member.name}
                                            </Link>
                                            {badges}
                                        </span>
                                    )}
                                </React.Fragment>
                            );
                        })}
                        </p>
                    </div>
                );
            })}
        </div>
    );
};

export default ClanActivityRoster;
