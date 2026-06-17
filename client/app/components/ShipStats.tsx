import React, { useEffect, useState } from 'react';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { PLAYER_ROUTE_PANEL_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { withRealm } from '../lib/realmParams';

// ShipStats — the per-ship combat panel shown in the Activity tab when a row in
// the Battle History table is clicked (toggled; a second click on the same ship
// hides it). It operationalizes the previously-unsurfaced ships_stats_json
// combat fields (gunnery / torpedo / secondary accuracy, spotting, objective
// play, survival) documented in
// runbook-battle-history-data-operationalization-2026-06-16.md.
//
// Each metric charts the player's CAREER per-ship rate against the ship's
// 30-day POPULATION average. Role-irrelevant metrics (e.g. secondaries on a DD,
// torpedoes on most battleships) are omitted server-side, so this component
// renders only the clusters that matter for the selected ship.

// "Good" / "bad" delta colors mirror BattleHistoryCard's WrCell so the whole
// Activity tab reads consistently across light/dark.
const TONE_GOOD = '#74c476';
const TONE_BAD = '#a50f15';
const TONE_NEUTRAL = 'var(--text-muted)';

interface ShipStatMetric {
    key: string;
    label: string;
    unit: string;
    better: 'high' | 'low';
    user: number | null;
    average: number;
}

interface ShipStatCluster {
    name: string;
    metrics: ShipStatMetric[];
}

interface ShipCombatPayload {
    ship_id: number;
    ship_name: string;
    ship_tier: number | null;
    ship_type: string | null;
    window_days: number;
    sample_players: number;
    sample_battles: number;
    user_battles: number;
    has_user_data: boolean;
    clusters: ShipStatCluster[];
}

interface ShipStatsProps {
    playerName: string;
    realm: string;
    shipId: number;
    // Identity hints from the clicked table row — render the header instantly
    // before the fetch resolves; the payload refines them.
    shipName?: string;
    shipTier?: number | null;
    shipType?: string | null;
    onClose: () => void;
}

const formatMetricValue = (value: number | null, unit: string): string => {
    if (value == null) {
        return '—';
    }
    if (unit === '%') {
        return `${value.toFixed(1)}%`;
    }
    // Per-battle counters: large numbers (damage / xp) read better rounded with
    // separators; small rates (frags, caps) keep up to one decimal.
    const formatted = Math.abs(value) >= 1000
        ? Math.round(value).toLocaleString()
        : (Math.round(value * 10) / 10).toLocaleString();
    return unit ? `${formatted}${unit}` : formatted;
};

const MetricRow: React.FC<{ metric: ShipStatMetric }> = ({ metric }) => {
    const { user, average, better, unit, label } = metric;
    const scaleMax = Math.max(user ?? 0, average, 1) * 1.15;
    const userPct = user == null ? 0 : Math.min(100, (user / scaleMax) * 100);
    const avgPct = Math.min(100, (average / scaleMax) * 100);

    let deltaText = '';
    let deltaTone = TONE_NEUTRAL;
    if (user != null && average > 0) {
        const deltaPct = ((user - average) / average) * 100;
        const isBetter = better === 'high' ? deltaPct > 0 : deltaPct < 0;
        const magnitude = Math.abs(deltaPct);
        if (magnitude >= 0.5) {
            deltaTone = isBetter ? TONE_GOOD : TONE_BAD;
            deltaText = `${deltaPct > 0 ? '+' : '−'}${magnitude.toFixed(0)}%`;
        } else {
            deltaText = '≈ avg';
        }
    }

    return (
        // Left column (capped at 60% of the panel width): left-justified title +
        // comparison bar. Right column: the you · avg · Δ details, right-aligned.
        <div className="grid grid-cols-[60%_1fr] items-center gap-x-4 py-1.5">
            <div className="min-w-0">
                <div className="text-xs text-[var(--text-muted)]">{label}</div>
                {/* Comparison track: filled bar = the player; tick = the ship's
                    30-day population average. */}
                <div
                    className="relative mt-1 h-2 w-full overflow-hidden rounded-full"
                    style={{ backgroundColor: 'var(--accent-faint)' }}
                    role="img"
                    aria-label={`${label}: you ${formatMetricValue(user, unit)}, average ${formatMetricValue(average, unit)}`}
                >
                    <div
                        className="absolute inset-y-0 left-0 rounded-full"
                        style={{ width: `${userPct}%`, backgroundColor: 'var(--accent-secondary-mid)' }}
                    />
                    <div
                        className="absolute inset-y-[-2px] w-[2px]"
                        style={{ left: `calc(${avgPct}% - 1px)`, backgroundColor: 'var(--text-strong)', opacity: 0.7 }}
                        title={`Ship average: ${formatMetricValue(average, unit)}`}
                    />
                </div>
            </div>
            <div className="whitespace-nowrap text-right text-xs tabular-nums">
                <span className="font-semibold text-[var(--text-strong)]">{formatMetricValue(user, unit)}</span>
                <span className="text-[var(--text-muted)]">{' · avg '}{formatMetricValue(average, unit)}</span>
                {deltaText ? (
                    <span className="ml-1.5 font-semibold" style={{ color: deltaTone }}>{deltaText}</span>
                ) : null}
            </div>
        </div>
    );
};

const ShipStats: React.FC<ShipStatsProps> = ({
    playerName, realm, shipId, shipName, shipTier, shipType, onClose,
}) => {
    const [payload, setPayload] = useState<ShipCombatPayload | null>(null);
    const [state, setState] = useState<'loading' | 'ready' | 'empty' | 'error'>('loading');

    useEffect(() => {
        let cancelled = false;
        setPayload(null);
        setState('loading');

        fetchSharedJson<ShipCombatPayload>(
            withRealm(`/api/player/${encodeURIComponent(playerName)}/ship/${shipId}/combat-stats`, realm),
            { label: 'Ship combat stats', ttlMs: PLAYER_ROUTE_PANEL_FETCH_TTL_MS },
        )
            .then(({ data }) => {
                if (cancelled) return;
                setPayload(data);
                setState(data.clusters.length > 0 ? 'ready' : 'empty');
            })
            .catch(() => {
                if (cancelled) return;
                setState('error');
            });

        return () => { cancelled = true; };
    }, [playerName, realm, shipId]);

    const headerName = payload?.ship_name || shipName || `Ship ${shipId}`;
    const headerTier = payload?.ship_tier ?? shipTier ?? null;
    const headerType = payload?.ship_type ?? shipType ?? null;

    return (
        <div
            className="mt-5 rounded-md border border-[var(--accent-faint)] bg-[var(--bg-surface)] p-4"
            data-testid="ship-stats"
            aria-label={`Combat profile for ${headerName}`}
        >
            <div className="flex items-start justify-between gap-3">
                <div>
                    <div className="flex items-baseline gap-2">
                        <h3 className="text-sm font-semibold text-[var(--text-strong)]">{headerName}</h3>
                        {headerTier != null ? (
                            <span className="text-xs text-[var(--text-muted)]">Tier {headerTier}</span>
                        ) : null}
                        {headerType ? (
                            <span className="text-xs text-[var(--text-muted)]">· {headerType}</span>
                        ) : null}
                    </div>
                    {state === 'ready' && payload ? (
                        <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                            Your career profile vs the {payload.window_days}-day server average
                            {payload.sample_players > 0
                                ? ` (${payload.sample_players.toLocaleString()} captains, ${payload.sample_battles.toLocaleString()} battles)`
                                : ''}
                        </p>
                    ) : null}
                </div>
                <button
                    type="button"
                    onClick={onClose}
                    aria-label="Hide ship combat profile"
                    className="rounded px-2 py-0.5 text-sm text-[var(--text-muted)] transition-colors hover:bg-[var(--accent-faint)] hover:text-[var(--text-strong)]"
                >
                    ✕
                </button>
            </div>

            {state === 'loading' ? (
                <p className="mt-3 animate-pulse text-sm text-[var(--accent-light)]">Loading ship combat profile…</p>
            ) : null}

            {state === 'error' ? (
                <p className="mt-3 text-sm text-[var(--text-muted)]">Unable to load this ship&apos;s combat profile right now.</p>
            ) : null}

            {state === 'empty' ? (
                <p className="mt-3 text-sm text-[var(--text-muted)]">
                    Not enough recent server data for this ship yet to build a comparison.
                </p>
            ) : null}

            {state === 'ready' && payload ? (
                <div className="mt-3 space-y-4">
                    {payload.clusters.map((cluster) => (
                        <section key={cluster.name}>
                            <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--accent-secondary-mid)]">
                                {cluster.name}
                            </h4>
                            <div className="divide-y divide-[var(--accent-faint)]">
                                {cluster.metrics.map((metric) => (
                                    <MetricRow key={metric.key} metric={metric} />
                                ))}
                            </div>
                        </section>
                    ))}
                </div>
            ) : null}

            {state === 'ready' && payload && !payload.has_user_data ? (
                <p className="mt-3 text-xs text-[var(--text-muted)]">
                    No career stats found for you on this ship — showing the server average only.
                </p>
            ) : null}
        </div>
    );
};

export default ShipStats;
