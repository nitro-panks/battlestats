import { buildClanChartMemberActivitySignature } from '../clanChartActivity';
import type { ClanMemberData } from '../clanMembersShared';

const makeMember = (overrides: Partial<ClanMemberData>): ClanMemberData => ({
    name: 'Player One',
    is_hidden: false,
    pvp_ratio: 55,
    days_since_last_battle: 7,
    is_leader: false,
    is_pve_player: false,
    is_sleepy_player: false,
    is_ranked_player: false,
    is_clan_battle_player: false,
    clan_battle_win_rate: null,
    highest_ranked_league: null,
    ranked_hydration_pending: false,
    ranked_updated_at: null,
    activity_bucket: 'active_7d',
    ...overrides,
});

describe('buildClanChartMemberActivitySignature', () => {
    it('ignores icon-only async member updates', () => {
        const before = [
            makeMember({ name: 'Player One', is_ranked_player: false, highest_ranked_league: null, ranked_hydration_pending: true }),
            makeMember({ name: 'Player Two', activity_bucket: 'active_30d', days_since_last_battle: 12 }),
        ];
        const after = [
            makeMember({ name: 'Player Two', activity_bucket: 'active_30d', days_since_last_battle: 12, is_clan_battle_player: true, clan_battle_win_rate: 58.3 }),
            makeMember({ name: 'Player One', is_ranked_player: true, highest_ranked_league: 'Gold', ranked_hydration_pending: false }),
        ];

        expect(buildClanChartMemberActivitySignature(after)).toBe(buildClanChartMemberActivitySignature(before));
    });

    it('changes when chart-relevant activity data changes', () => {
        const before = [makeMember({ name: 'Player One', activity_bucket: 'active_7d', days_since_last_battle: 7 })];
        const after = [makeMember({ name: 'Player One', activity_bucket: 'cooling_90d', days_since_last_battle: 45 })];

        expect(buildClanChartMemberActivitySignature(after)).not.toBe(buildClanChartMemberActivitySignature(before));
    });
});