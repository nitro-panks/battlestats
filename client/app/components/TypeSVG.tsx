import { createShipBarChart } from './shipBarPlot';
import type { TypeRow } from './playerProfileChartData';

const TypeSVG = createShipBarChart<TypeRow>({
    rowKey: (row) => row.ship_type,
    detailTitle: (row) => row.ship_type,
    cssPrefix: 'type',
    compactHeightCap: 192,
    compactLeftMargin: 62,
    endpoint: (playerId) => `/api/fetch/type_data/${playerId}/`,
    fetchLabel: (playerId) => `Type data ${playerId}`,
    fetchErrorMessage: 'Error fetching type data:',
    unexpectedPayloadMessage: 'Unexpected type data payload:',
    sortRows: (rows) => [...rows].sort((left, right) => right.pvp_battles - left.pvp_battles),
    defaultSvgHeight: 210,
});

export default TypeSVG;
