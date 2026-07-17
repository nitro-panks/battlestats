import { createShipBarChart } from './shipBarPlot';
import type { TierRow } from './playerProfileChartData';

// Vertical columns: tiers ascend left→right along the x axis, so the fetch
// path sorts ascending (prop-fed callers must hand rows in x-axis order too).
const TierSVG = createShipBarChart<TierRow>({
    rowKey: (row) => String(row.ship_tier),
    detailTitle: (row) => `Tier ${row.ship_tier}`,
    cssPrefix: 'tier',
    orientation: 'vertical',
    compactHeightCap: 300,
    compactLeftMargin: 42,
    endpoint: (playerId) => `/api/fetch/tier_data/${playerId}/`,
    fetchLabel: (playerId) => `Tier data ${playerId}`,
    fetchErrorMessage: 'Error fetching tier data:',
    unexpectedPayloadMessage: 'Unexpected tier data payload:',
    sortRows: (rows) => [...rows].sort((left, right) => left.ship_tier - right.ship_tier),
    defaultSvgHeight: 334,
});

export default TierSVG;
