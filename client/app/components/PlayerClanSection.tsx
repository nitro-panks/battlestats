"use client";

import React, { useMemo } from 'react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import DeferredSection from './DeferredSection';
import LoadingPanel from './LoadingPanel';
import { resilientDynamicImport } from './resilientDynamicImport';
import { useClanMembers } from './useClanMembers';
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
import { buildClanPath, buildPlayerPath } from '../lib/entityRoutes';
import { useRealm } from '../context/RealmContext';
import { useTheme } from '../context/ThemeContext';
import { trackEvent } from '../lib/umami';

// Player-page clan section (below the insights tabs): a compact version of the
// clan page — the clan activity scatterplot, then the roster as one flowing
// paragraph of names per collapsed activity phase instead of a vertical list.
// Replaces the retired left clan rail as the player page's clan surface.

const ClanSVG = dynamic(() => resilientDynamicImport(() => import('./ClanSVG'), 'PlayerClanSection-ClanSVG'), {
    ssr: false,
    loading: () => <LoadingPanel tone="muted" label="Loading clan chart..." minHeight={440} />,
});

interface PlayerClanSectionProps {
    clanId: number;
    clanName: string;
    clanTag: string | null;
    playerId: number;
    playerName: string;
}

type PhaseKey = CollapsedActivityBucketKey | 'unknown';

const PHASES: Array<{ key: PhaseKey; label: string }> = [
    { key: 'active_7d', label: 'Active now' },
    { key: 'cooling_90d', label: 'Cooling Off' },
    { key: 'inactive_180d_plus', label: 'Gone dark' },
    { key: 'unknown', label: 'No recency' },
];

const PlayerClanSection: React.FC<PlayerClanSectionProps> = ({ clanId, clanName, clanTag, playerId, playerName }) => {
    const router = useRouter();
    const { realm } = useRealm();
    const { theme } = useTheme();
    const { members, loading, error } = useClanMembers(clanId);

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

    const handleMemberClick = () => trackEvent('clan-member-click', { realm, source: 'player' });

    const handleChartSelectMember = (memberName: string) => {
        trackEvent('clan-member-click', { realm, source: 'player' });
        router.push(buildPlayerPath(memberName, realm));
    };

    return (
        <section className="mt-10 border-t border-[var(--border)] pt-6" data-testid="player-clan-section">
            <h2 className="text-xl font-semibold tracking-tight">
                <Link
                    href={buildClanPath(clanId, clanName || 'Clan', realm)}
                    className="text-[var(--accent-mid)] underline-offset-4 hover:underline"
                    aria-label={`Open clan page for ${clanName || 'clan'}`}
                >
                    {clanTag ? `[${clanTag}] ` : ''}{clanName || 'Clan'}
                </Link>
            </h2>

            <DeferredSection
                minHeight={560}
                placeholder={<LoadingPanel tone="muted" label="Preparing clan overview..." minHeight={560} />}
                playerId={playerId}
                rootMargin="240px 0px"
                sectionId="player-clan-section"
            >
                {/* Pull the chart left by its y-axis margin so the axis sits flush
                    with the section's left edge, matching the clan page. */}
                <div className="mt-4 md:-ml-[38px]">
                    <ClanSVG
                        clanId={clanId}
                        svgWidth={938}
                        svgHeight={440}
                        membersData={members}
                        theme={theme}
                        highlightedPlayerName={playerName}
                        onSelectMember={handleChartSelectMember}
                    />
                </div>

                <div className="mt-6 space-y-6">
                    {loading && members.length === 0 ? (
                        <p className="text-sm text-[var(--text-secondary)]">Loading clan members...</p>
                    ) : error && members.length === 0 ? (
                        <p className="text-sm text-[var(--text-secondary)]">{error}</p>
                    ) : (
                        PHASES.map(({ key, label }) => {
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
                                        // Same classification-badge dispatch as the ClanMembers
                                        // rows (badge dispatch is intentionally per-surface); the
                                        // activity icon is omitted — the paragraph header carries
                                        // the phase.
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
                                                {member.name === playerName ? (
                                                    <span className="inline-flex items-center gap-1 font-semibold text-[var(--text-primary)]">
                                                        <span>{member.name}</span>
                                                        {badges}
                                                    </span>
                                                ) : member.is_hidden ? (
                                                    // Hidden accounts are not clickable, matching the
                                                    // clan-page roster.
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
                        })
                    )}
                </div>
            </DeferredSection>
        </section>
    );
};

export default PlayerClanSection;
