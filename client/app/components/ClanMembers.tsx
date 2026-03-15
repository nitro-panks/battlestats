import React, { useEffect, useRef, useState } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCrown, faRobot, faStar } from '@fortawesome/free-solid-svg-icons';
import { getRankedLeagueColor, getRankedLeagueTooltip, type RankedLeagueName } from './rankedLeague';

interface ClanMembersProps {
    clanId: number;
    onSelectMember: (memberName: string) => void;
    layout?: 'inline' | 'stacked';
}

interface ClanMemberData {
    name: string;
    is_hidden: boolean;
    pvp_ratio: number | null;
    days_since_last_battle: number | null;
    is_leader: boolean;
    is_pve_player: boolean;
    is_ranked_player: boolean;
    highest_ranked_league: RankedLeagueName | null;
    ranked_hydration_pending: boolean;
    ranked_updated_at: string | null;
    activity_bucket: 'active_7d' | 'active_30d' | 'cooling_90d' | 'dormant_180d' | 'inactive_180d_plus' | 'unknown';
}

const RANKED_HYDRATION_POLL_LIMIT = 6;
const RANKED_HYDRATION_POLL_INTERVAL_MS = 2500;

const isAbortError = (error: unknown): boolean => {
    return error instanceof DOMException && error.name === 'AbortError';
};

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

const RankedStar: React.FC<{ league: RankedLeagueName | null }> = ({ league }) => (
    <FontAwesomeIcon
        icon={faStar}
        className="text-[11px]"
        style={{ color: getRankedLeagueColor(league) }}
        title={getRankedLeagueTooltip(league)}
        aria-label={getRankedLeagueTooltip(league)}
    />
);

const ClanMembers: React.FC<ClanMembersProps> = ({ clanId, onSelectMember, layout = 'inline' }) => {
    const [members, setMembers] = useState<ClanMemberData[]>([]);
    const [loading, setLoading] = useState(true);
    const rankedHydrationAttemptsRef = useRef(0);

    useEffect(() => {
        let timeoutId: ReturnType<typeof setTimeout> | null = null;
        let activeController: AbortController | null = null;
        rankedHydrationAttemptsRef.current = 0;

        const fetchMembers = async (showLoading: boolean, attempt: number) => {
            if (showLoading) {
                setLoading(true);
            }

            activeController?.abort();
            const controller = new AbortController();
            activeController = controller;

            try {
                const response = await fetch(`http://localhost:8888/api/fetch/clan_members/${clanId}/`, {
                    signal: controller.signal,
                });
                if (!response.ok) {
                    throw new Error(`Failed to fetch clan members for clan ${clanId}`);
                }

                const data: ClanMemberData[] = await response.json();
                if (controller.signal.aborted) {
                    return;
                }

                setMembers(data);

                const hasPendingRankedHydration = data.some((member) => member.ranked_hydration_pending);
                if (hasPendingRankedHydration && attempt < RANKED_HYDRATION_POLL_LIMIT) {
                    rankedHydrationAttemptsRef.current = attempt + 1;
                    timeoutId = setTimeout(() => {
                        void fetchMembers(false, attempt + 1);
                    }, RANKED_HYDRATION_POLL_INTERVAL_MS);
                }
            } catch (error) {
                if (isAbortError(error)) {
                    return;
                }

                console.error('Error fetching clan members:', error);
            } finally {
                if (showLoading && !controller.signal.aborted) {
                    setLoading(false);
                }
            }
        };

        void fetchMembers(true, 0);

        return () => {
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
            activeController?.abort();
        };
    }, [clanId]);

    return (
        <div>
            <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-600">Clan Members</h3>
            {loading && <p className="text-sm text-gray-500">Syncing clan members...</p>}
            {!loading && members.length === 0 && <p className="text-sm text-gray-500">No clan members found.</p>}
            {!loading && members.length > 0 && (
                <div className={layout === 'stacked' ? 'mt-2 space-y-1 text-sm text-[#4292c6]' : 'mt-2 text-sm leading-7 text-[#4292c6]'}>
                    {members.map((member, index) => (
                        <React.Fragment key={member.name}>
                            {member.is_hidden ? (
                                <span
                                    className={layout === 'stacked'
                                        ? 'flex items-center gap-1 font-medium text-gray-500'
                                        : 'mr-3 inline-flex items-center gap-1 font-medium text-gray-500'}
                                    title={formatRecency(member.days_since_last_battle)}
                                >
                                    <span style={{ color: wrColor(member.pvp_ratio) }} aria-hidden="true">{"\u25C6"}</span>
                                    {member.name}
                                    {member.is_leader && <LeaderCrown />}
                                    {member.is_pve_player && <PveRobot />}
                                    {member.is_ranked_player && <RankedStar league={member.highest_ranked_league} />}
                                    <span className="text-xs font-normal text-gray-400">{formatRecency(member.days_since_last_battle)}</span>
                                </span>
                            ) : (
                                <button
                                    onClick={() => onSelectMember(member.name)}
                                    className={layout === 'stacked'
                                        ? 'flex items-center gap-1 font-medium text-[#084594] underline-offset-2 hover:underline hover:text-[#2171b5]'
                                        : 'mr-3 inline-flex items-center gap-1 font-medium text-[#084594] underline-offset-2 hover:underline hover:text-[#2171b5]'}
                                    aria-label={`Show player ${member.name}`}
                                    title={formatRecency(member.days_since_last_battle)}
                                >
                                    <span style={{ color: wrColor(member.pvp_ratio) }} aria-hidden="true">{"\u25C6"}</span>
                                    {member.name}
                                    {member.is_leader && <LeaderCrown />}
                                    {member.is_pve_player && <PveRobot />}
                                    {member.is_ranked_player && <RankedStar league={member.highest_ranked_league} />}
                                    <span className="text-xs font-normal text-gray-400">{formatRecency(member.days_since_last_battle)}</span>
                                </button>
                            )}
                        </React.Fragment>
                    ))}
                </div>
            )}
        </div>
    );
};

export default ClanMembers;
