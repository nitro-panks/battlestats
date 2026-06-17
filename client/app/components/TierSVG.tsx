import { createShipBarChart } from './shipBarPlot';
import type { TierRow } from './playerProfileChartData';

const TierSVG = createShipBarChart<TierRow>({
    rowKey: (row) => String(row.ship_tier),
    detailTitle: (row) => `Tier ${row.ship_tier}`,
    cssPrefix: 'tier',
    compactHeightCap: 300,
    compactLeftMargin: 42,
    endpoint: (playerId) => `/api/fetch/tier_data/${playerId}/`,
    fetchLabel: (playerId) => `Tier data ${playerId}`,
    fetchErrorMessage: 'Error fetching tier data:',
    unexpectedPayloadMessage: 'Unexpected tier data payload:',
    sortRows: (rows) => [...rows].sort((left, right) => right.ship_tier - left.ship_tier),
    defaultSvgHeight: 334,
});

export default TierSVG;
