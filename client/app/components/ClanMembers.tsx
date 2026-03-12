import React, { useEffect, useState } from 'react';

interface ClanMembersProps {
    clanId: number;
    onSelectMember: (memberName: string) => void;
}

interface ClanMemberData {
    name: string;
    is_hidden: boolean;
    pvp_ratio: number | null;
    days_since_last_battle: number | null;
    activity_bucket: 'active_7d' | 'active_30d' | 'cooling_90d' | 'dormant_180d' | 'inactive_180d_plus' | 'unknown';
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

const ClanMembers: React.FC<ClanMembersProps> = ({ clanId, onSelectMember }) => {
    const [members, setMembers] = useState<ClanMemberData[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchMembers = async () => {
            setLoading(true);
            try {
                const response = await fetch(`http://localhost:8888/api/fetch/clan_members/${clanId}/`);
                const data = await response.json();
                setMembers(data);
            } catch (error) {
                console.error('Error fetching clan members:', error);
            } finally {
                setLoading(false);
            }
        };

        fetchMembers();
    }, [clanId]);

    return (
        <div>
            <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-600">Clan Members</h3>
            {loading && <p className="text-sm text-gray-500">Syncing clan members...</p>}
            {!loading && members.length === 0 && <p className="text-sm text-gray-500">No clan members found.</p>}
            {!loading && members.length > 0 && (
                <p className="mt-2 text-sm leading-7 text-[#4292c6]">
                    {members.map((member, index) => (
                        <React.Fragment key={member.name}>
                            {member.is_hidden ? (
                                <span
                                    className="mr-3 inline-flex items-center gap-1 font-medium text-gray-500"
                                    title={formatRecency(member.days_since_last_battle)}
                                >
                                    <span style={{ color: wrColor(member.pvp_ratio) }} aria-hidden="true">{"\u25C6"}</span>
                                    {member.name}
                                    <span className="text-xs font-normal text-gray-400">{formatRecency(member.days_since_last_battle)}</span>
                                </span>
                            ) : (
                                <button
                                    onClick={() => onSelectMember(member.name)}
                                    className="mr-3 inline-flex items-center gap-1 font-medium text-[#084594] underline-offset-2 hover:underline hover:text-[#2171b5]"
                                    aria-label={`Show player ${member.name}`}
                                    title={formatRecency(member.days_since_last_battle)}
                                >
                                    <span style={{ color: wrColor(member.pvp_ratio) }} aria-hidden="true">{"\u25C6"}</span>
                                    {member.name}
                                    <span className="text-xs font-normal text-gray-400">{formatRecency(member.days_since_last_battle)}</span>
                                </button>
                            )}
                        </React.Fragment>
                    ))}
                </p>
            )}
        </div>
    );
};

export default ClanMembers;
