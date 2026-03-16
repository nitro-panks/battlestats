import type { ActivityBucketKey, ClanMemberData } from './clanMembersShared';

export interface ClanChartMemberActivity {
    normalizedName: string;
    activity_bucket: ActivityBucketKey;
    days_since_last_battle: number | null;
}

const normalizeName = (value: string): string => value.trim().toLowerCase();

export const buildClanChartMemberActivity = (membersData: ClanMemberData[]): ClanChartMemberActivity[] => {
    return [...membersData]
        .map((member) => ({
            normalizedName: normalizeName(member.name),
            activity_bucket: member.activity_bucket,
            days_since_last_battle: member.days_since_last_battle ?? null,
        }))
        .sort((left, right) => left.normalizedName.localeCompare(right.normalizedName));
};

export const buildClanChartMemberActivitySignature = (membersData: ClanMemberData[]): string => {
    return buildClanChartMemberActivity(membersData)
        .map((member) => `${member.normalizedName}:${member.activity_bucket}:${member.days_since_last_battle ?? 'null'}`)
        .join('|');
};