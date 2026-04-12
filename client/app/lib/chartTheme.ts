export type ChartTheme = 'light' | 'dark';

export const chartColors: Record<ChartTheme, {
    // Infrastructure
    chartBg: string;
    surface: string;
    axisText: string;
    axisLine: string;
    gridLine: string;
    gridLineBlue: string;
    labelText: string;
    labelStrong: string;
    labelMid: string;
    labelMuted: string;
    separator: string;
    barStroke: string;
    barBg: string;
    // Win-rate palette
    wrNull: string;
    wrElite: string;
    wrSuperUnicum: string;
    wrUnicum: string;
    wrVeryGood: string;
    wrGood: string;
    wrAboveAvg: string;
    wrAverage: string;
    wrBelowAvg: string;
    wrBad: string;
    // Activity bar palette
    activityActive: string;
    activityRecent: string;
    activityCooling: string;
    activityDormant: string;
    activityInactive: string;
    activityUnknown: string;
    // Heatmap / trend colors
    heatmapAboveTrend: string;
    heatmapBelowTrend: string;
    heatmapUnavailable: string;
    heatmapCellText: string;
    heatmapCountText: string;
    // Ship type palette
    shipDD: string;
    shipCA: string;
    shipBB: string;
    shipCV: string;
    shipSS: string;
    shipDefault: string;
    // Metric line palette
    metricWR: string;
    metricBattles: string;
    metricSurvival: string;
    metricScore: string;
// Accent / UI colors used inside SVG
    accentLink: string;
    accentMid: string;
}> = {
    light: {
        // Infrastructure
        chartBg: '#ffffff',
        surface: '#f7fbff',
        axisText: '#475569',
        axisLine: '#cbd5e1',
        gridLine: '#e5e7eb',
        gridLineBlue: '#dbeafe',
        labelText: '#6b7280',
        labelStrong: '#0f172a',
        labelMid: '#475569',
        labelMuted: '#64748b',
        separator: '#94a3b8',
        barStroke: '#ffffff',
        barBg: '#dde5ed',
        // Win-rate palette
        wrNull: '#c6dbef',
        wrElite: '#810c9e',
        wrSuperUnicum: '#D042F3',
        wrUnicum: '#3182bd',
        wrVeryGood: '#74c476',
        wrGood: '#a1d99b',
        wrAboveAvg: '#fed976',
        wrAverage: '#fd8d3c',
        wrBelowAvg: '#e6550d',
        wrBad: '#a50f15',
        // Activity bar palette
        activityActive: '#08519c',
        activityRecent: '#3182bd',
        activityCooling: '#6baed6',
        activityDormant: '#9ecae1',
        activityInactive: '#d9e2ec',
        activityUnknown: '#e5e7eb',
        // Heatmap / trend colors
        heatmapAboveTrend: '#166534',
        heatmapBelowTrend: '#991b1b',
        heatmapUnavailable: '#64748b',
        heatmapCellText: '#084594',
        heatmapCountText: '#475569',
        // Ship type palette
        shipDD: '#0f766e',
        shipCA: '#2563eb',
        shipBB: '#a16207',
        shipCV: '#b91c1c',
        shipSS: '#7c3aed',
        shipDefault: '#475569',
        // Metric line palette
        metricWR: '#4292c6',
        metricBattles: '#2171b5',
        metricSurvival: '#0f766e',
        metricScore: '#2171b5',
// Accent / UI colors used inside SVG
        accentLink: '#084594',
        accentMid: '#2171b5',
    },
    dark: {
        // Infrastructure
        chartBg: '#0d1117',
        surface: '#161b22',
        axisText: '#8b949e',
        axisLine: '#30363d',
        gridLine: '#21262d',
        gridLineBlue: '#162032',
        labelText: '#8b949e',
        labelStrong: '#e6edf3',
        labelMid: '#8b949e',
        labelMuted: '#6b7280',
        separator: '#30363d',
        barStroke: '#0d1117',
        barBg: '#2d333b',
        // Win-rate palette
        wrNull: '#4b6a8a',
        wrElite: '#810c9e',
        wrSuperUnicum: '#D042F3',
        wrUnicum: '#3182bd',
        wrVeryGood: '#74c476',
        wrGood: '#a1d99b',
        wrAboveAvg: '#fed976',
        wrAverage: '#fd8d3c',
        wrBelowAvg: '#e6550d',
        wrBad: '#a50f15',
        // Activity bar palette
        activityActive: '#4292c6',
        activityRecent: '#6baed6',
        activityCooling: '#9ecae1',
        activityDormant: '#4b5563',
        activityInactive: '#2d3748',
        activityUnknown: '#1f2937',
        // Heatmap / trend colors
        heatmapAboveTrend: '#4ade80',
        heatmapBelowTrend: '#f87171',
        heatmapUnavailable: '#4b5563',
        heatmapCellText: '#79c0ff',
        heatmapCountText: '#8b949e',
        // Ship type palette
        shipDD: '#2dd4bf',
        shipCA: '#60a5fa',
        shipBB: '#fbbf24',
        shipCV: '#f87171',
        shipSS: '#a78bfa',
        shipDefault: '#6b7280',
        // Metric line palette
        metricWR: '#79c0ff',
        metricBattles: '#58a6ff',
        metricSurvival: '#2dd4bf',
        metricScore: '#58a6ff',
// Accent / UI colors used inside SVG
        accentLink: '#79c0ff',
        accentMid: '#58a6ff',
    },
};
