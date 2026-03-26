export interface TierTypeTile {
    ship_type: string;
    ship_tier: number;
    count: number;
}

export interface TierTypeTrendPoint {
    ship_type: string;
    avg_tier: number;
    count: number;
}

export interface TierTypePlayerCell {
    ship_type: string;
    ship_tier: number;
    pvp_battles: number;
    wins: number;
    win_ratio: number;
}

export interface TierTypePayload {
    metric: 'tier_type';
    label: string;
    x_label: string;
    y_label: string;
    tracked_population: number;
    tiles: TierTypeTile[];
    trend: TierTypeTrendPoint[];
    player_cells: TierTypePlayerCell[];
}

export interface TierRow {
    ship_tier: number;
    pvp_battles: number;
    wins: number;
    win_ratio: number;
}

export interface TypeRow {
    ship_type: string;
    pvp_battles: number;
    wins: number;
    win_ratio: number;
}

const roundWinRatio = (wins: number, battles: number): number => {
    if (battles <= 0) {
        return 0;
    }

    return Math.round((wins / battles) * 100) / 100;
};

export const deriveTypeRowsFromTierTypePayload = (payload: TierTypePayload): TypeRow[] => {
    const aggregates = new Map<string, { pvp_battles: number; wins: number; }>();

    payload.player_cells.forEach((row) => {
        const aggregate = aggregates.get(row.ship_type) ?? { pvp_battles: 0, wins: 0 };
        aggregate.pvp_battles += row.pvp_battles;
        aggregate.wins += row.wins;
        aggregates.set(row.ship_type, aggregate);
    });

    return [...aggregates.entries()]
        .map(([ship_type, aggregate]) => ({
            ship_type,
            pvp_battles: aggregate.pvp_battles,
            wins: aggregate.wins,
            win_ratio: roundWinRatio(aggregate.wins, aggregate.pvp_battles),
        }))
        .sort((left, right) => right.pvp_battles - left.pvp_battles);
};

export const deriveTierRowsFromTierTypePayload = (payload: TierTypePayload): TierRow[] => {
    const aggregates = new Map<number, { pvp_battles: number; wins: number; }>();

    payload.player_cells.forEach((row) => {
        const aggregate = aggregates.get(row.ship_tier) ?? { pvp_battles: 0, wins: 0 };
        aggregate.pvp_battles += row.pvp_battles;
        aggregate.wins += row.wins;
        aggregates.set(row.ship_tier, aggregate);
    });

    const rows: TierRow[] = [];
    for (let shipTier = 11; shipTier >= 1; shipTier -= 1) {
        const aggregate = aggregates.get(shipTier) ?? { pvp_battles: 0, wins: 0 };
        rows.push({
            ship_tier: shipTier,
            pvp_battles: aggregate.pvp_battles,
            wins: aggregate.wins,
            win_ratio: roundWinRatio(aggregate.wins, aggregate.pvp_battles),
        });
    }

    return rows;
};