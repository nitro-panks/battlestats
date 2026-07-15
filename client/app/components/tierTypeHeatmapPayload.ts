import type { TierTypePayload, TierTypeTile } from './playerProfileChartData';

export interface ResolvedTierTypeTile extends TierTypeTile {
    ship_type: string;
    ship_tier: number;
}

export const getTierTypeShipTypes = (payload: TierTypePayload): string[] => payload.x_labels;

export const getTierTypeTiers = (payload: TierTypePayload): number[] => payload.y_values;

export const resolveTierTypeTile = (payload: TierTypePayload, tile: TierTypeTile): ResolvedTierTypeTile | null => {
    const shipType = payload.x_labels[tile.x_index];
    const shipTier = payload.y_values[tile.y_index];
    if (shipType == null || shipTier == null) {
        return null;
    }

    return {
        ...tile,
        ship_type: shipType,
        ship_tier: shipTier,
    };
};

export const resolveTierTypeTiles = (payload: TierTypePayload): ResolvedTierTypeTile[] => payload.tiles
    .map((tile) => resolveTierTypeTile(payload, tile))
    .filter((tile): tile is ResolvedTierTypeTile => tile !== null);