interface CorrelationDomain {
    min: number;
    max: number;
    bin_width: number;
}

export interface CorrelationIndexTile {
    x_index: number;
    y_index: number;
    count: number;
}

export interface CorrelationIndexTrendPoint {
    x_index: number;
    y: number;
    count: number;
}

export interface CorrelationPayloadShape {
    x_domain: CorrelationDomain;
    y_domain: CorrelationDomain;
}

export const getCorrelationTileBounds = (payload: CorrelationPayloadShape, tile: CorrelationIndexTile) => {
    const xMin = payload.x_domain.min + (tile.x_index * payload.x_domain.bin_width);
    const xMax = xMin + payload.x_domain.bin_width;
    const yMin = payload.y_domain.min + (tile.y_index * payload.y_domain.bin_width);
    const yMax = yMin + payload.y_domain.bin_width;

    return {
        xMin,
        xMax,
        yMin,
        yMax,
    };
};

export const getCorrelationTrendX = (point: CorrelationIndexTrendPoint, xDomain: CorrelationDomain): number => {
    return xDomain.min + (point.x_index * xDomain.bin_width) + (xDomain.bin_width / 2);
};