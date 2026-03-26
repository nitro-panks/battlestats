export interface RankedHeatmapTile {
    x_index: number;
    y_index: number;
    count: number;
}

export interface RankedHeatmapTrendPoint {
    x_index: number;
    y: number;
    count: number;
}

export interface RankedHeatmapPayloadShape {
    x_edges: number[];
    y_domain: {
        min: number;
        max: number;
        bin_width?: number | null;
    };
}

export const getRankedHeatmapXDomain = (payload: RankedHeatmapPayloadShape): [number, number] => {
    const minEdge = payload.x_edges[0] ?? 1;
    const maxEdge = payload.x_edges[payload.x_edges.length - 1] ?? Math.max(minEdge + 1, minEdge * 2);
    return [Math.max(1, minEdge), Math.max(maxEdge, minEdge + 1)];
};

export const getRankedHeatmapTileBounds = (payload: RankedHeatmapPayloadShape, tile: RankedHeatmapTile) => {
    const binWidth = payload.y_domain.bin_width ?? 1;
    const xMin = payload.x_edges[tile.x_index];
    const xMax = payload.x_edges[tile.x_index + 1];
    const yMin = payload.y_domain.min + (tile.y_index * binWidth);
    const yMax = yMin + binWidth;

    return {
        xMin,
        xMax,
        yMin,
        yMax,
    };
};

export const getRankedHeatmapTrendX = (payload: RankedHeatmapPayloadShape, point: RankedHeatmapTrendPoint): number => {
    const xMin = payload.x_edges[point.x_index];
    const xMax = payload.x_edges[point.x_index + 1];
    if (xMin == null || xMax == null) {
        return payload.x_edges[0] ?? 1;
    }

    return Math.sqrt(xMin * xMax);
};