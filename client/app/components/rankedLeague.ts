export type RankedLeagueName = 'Bronze' | 'Silver' | 'Gold';

const RANKED_LEAGUE_COLORS: Record<RankedLeagueName, string> = {
    Bronze: '#cd7f32',
    Silver: '#94a3b8',
    Gold: '#d4af37',
};

// The career-scoped `getHighestRankedLeagueName` reducer that used to live here
// was removed with the current-season criteria change: both the flag and the
// league now arrive server-computed on the payload (is_ranked_player +
// highest_ranked_league), scoped to the current ranked season. Spec:
// agents/work-items/ranked-enjoyer-current-season-spec.md

export const getRankedLeagueColor = (league: RankedLeagueName | null | undefined): string => {
    if (!league) {
        return '#d4af37';
    }

    return RANKED_LEAGUE_COLORS[league];
};

export const getRankedLeagueTooltip = (league: RankedLeagueName | null | undefined): string => {
    if (!league) {
        return 'ranked this season';
    }

    return `ranked this season (${league})`;
};
