import React from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faBed, faCrown, faRobot, faShieldHalved, faStar } from '@fortawesome/free-solid-svg-icons';
import { getRankedLeagueColor, getRankedLeagueTooltip } from './rankedLeague';
import type { ClanMemberData } from './clanMembersShared';
import type { RankedLeagueName } from './rankedLeague';
import HiddenAccountIcon from './HiddenAccountIcon';
import EfficiencyRankIcon, { resolveEfficiencyRankTier } from './EfficiencyRankIcon';

interface ClanMembersProps {
    members: ClanMemberData[];
    onSelectMember: (memberName: string) => void;
    layout?: 'inline' | 'stacked';
    loading?: boolean;
    error?: string;
}

const wrColor = (r: number | null): string => {
    if (r == null) return '#c6dbef';
    if (r > 65) return '#810c9e';
    if (r >= 60) return '#D042F3';
    if (r >= 56) return '#3182bd';
    if (r >= 54) return '#74c476';
    if (r >= 52) return '#a1d99b';
    if (r >= 50) return '#fed976';
    if (r >= 45) return '#fd8d3c';
    return '#a50f15';
};

const formatRecency = (daysSinceLastBattle: number | null): string => {
    if (daysSinceLastBattle == null) return 'activity unknown';
    if (daysSinceLastBattle === 0) return 'played today';
    if (daysSinceLastBattle === 1) return '1 day idle';
    return `${daysSinceLastBattle} days idle`;
};

const LeaderCrown = () => (
    <FontAwesomeIcon
        icon={faCrown}
        className="text-[11px] text-amber-500"
        title="Clan leader"
        aria-label="Clan leader"
    />
);

const PveRobot = () => (
    <FontAwesomeIcon
        icon={faRobot}
        className="text-[11px] text-slate-500"
        title="pve enjoyer"
        aria-label="pve enjoyer"
    />
);

const SleepyBed = () => (
    <FontAwesomeIcon
        icon={faBed}
        className="text-[11px] text-slate-400"
        title="inactive for over a year"
        aria-label="inactive for over a year"
    />
);

const RankedStar: React.FC<{ league: RankedLeagueName | null }> = ({ league }) => (
    <FontAwesomeIcon
        icon={faStar}
        className="text-[11px]"
        style={{ color: getRankedLeagueColor(league) }}
        title={getRankedLeagueTooltip(league)}
        aria-label={getRankedLeagueTooltip(league)}
    />
);

const ClanBattleShield: React.FC<{ winRate: number | null }> = ({ winRate }) => (
    <FontAwesomeIcon
        icon={faShieldHalved}
        className="text-[11px]"
        style={{ color: wrColor(winRate) }}
        title={winRate == null ? 'clan battle enjoyer' : `clan battle enjoyer · ${winRate.toFixed(1)}% WR`}
        aria-label={winRate == null ? 'clan battle enjoyer' : `clan battle enjoyer ${winRate.toFixed(1)} percent WR`}
    />
);

const ClanMembers: React.FC<ClanMembersProps> = ({ members, onSelectMember, layout = 'inline', loading = false, error = '' }) => {
    const pendingEfficiencyCount = members.filter((member) => member.efficiency_hydration_pending).length;
    const isWarmingEfficiencyRanks = pendingEfficiencyCount > 0;

    return (
        <div>
            <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Clan Members</h3>
            {loading && <p className="text-sm text-[var(--text-secondary)]">Syncing clan members...</p>}
            {!loading && error ? <p className="text-sm text-[var(--text-secondary)]">{error}</p> : null}
            {!loading && !error && isWarmingEfficiencyRanks ? (
                <p className="text-sm text-[var(--text-secondary)]">
                    {`Updating Battlestats rank icons for ${pendingEfficiencyCount} clan member${pendingEfficiencyCount === 1 ? '' : 's'}...`}
                </p>
            ) : null}
            {!loading && members.length === 0 && <p className="text-sm text-[var(--text-secondary)]">No clan members found.</p>}
            {!loading && members.length > 0 && (
                <div className={layout === 'stacked' ? 'mt-2 space-y-1 text-sm text-[var(--accent-light)]' : 'mt-2 text-sm leading-7 text-[var(--accent-light)]'}>
                    {members.map((member) => (
                        <React.Fragment key={member.name}>
                            {(() => {
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
                                            <span style={{ color: wrColor(member.pvp_ratio) }} aria-hidden="true">{"\u25C6"}</span>
                                            {member.name}
                                            <HiddenAccountIcon className="text-[11px] text-[var(--accent-light)]" />
                                            {member.is_leader && <LeaderCrown />}
                                            {member.is_pve_player && <PveRobot />}
                                            {member.is_sleepy_player && <SleepyBed />}
                                            {member.is_ranked_player && <RankedStar league={member.highest_ranked_league} />}
                                            {member.is_clan_battle_player && <ClanBattleShield winRate={member.clan_battle_win_rate} />}
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
                                        <span style={{ color: wrColor(member.pvp_ratio) }} aria-hidden="true">{"\u25C6"}</span>
                                        {member.name}
                                        {member.is_leader && <LeaderCrown />}
                                        {member.is_pve_player && <PveRobot />}
                                        {member.is_sleepy_player && <SleepyBed />}
                                        {member.is_ranked_player && <RankedStar league={member.highest_ranked_league} />}
                                        {member.is_clan_battle_player && <ClanBattleShield winRate={member.clan_battle_win_rate} />}
                                        {efficiencyRankTier === 'E' ? <EfficiencyRankIcon tier={efficiencyRankTier} percentile={member.efficiency_rank_percentile} populationSize={member.efficiency_rank_population_size} size="inline" /> : null}
                                        <span className="text-xs font-normal text-[var(--text-secondary)]">{formatRecency(member.days_since_last_battle)}</span>
                                    </button>
                                );
                            })()}
                        </React.Fragment>
                    ))}
                </div>
            )}
        </div>
    );
};

export default ClanMembers;
