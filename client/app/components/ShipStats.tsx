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

// Skill brackets, by overall account random win rate (see backend). The user
// can compare against all players, the top 50%, or the top 25%.
type SkillBracket = 'all' | 'top50' | 'top25';
const BRACKET_OPTIONS: { id: SkillBracket; label: string }[] = [
    { id: 'all', label: 'All' },
    { id: 'top50', label: 'Top 50%' },
    { id: 'top25', label: 'Top 25%' },
];

interface ShipStatMetric {
    key: string;
    label: string;
    unit: string;
    better: 'high' | 'low';
    user: number | null;
    averages: Record<SkillBracket, number | null>;
}

interface ShipStatCluster {
    name: string;
    metrics: ShipStatMetric[];
}

interface BracketMeta {
    players: number;
    battles: number;
}

interface ShipCombatPayload {
    ship_id: number;
    ship_name: string;
    ship_tier: number | null;
    ship_type: string | null;
    window_days: number;
    min_account_battles: number;
    brackets: Record<SkillBracket, BracketMeta>;
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

const MetricRow: React.FC<{ metric: ShipStatMetric; bracket: SkillBracket }> = ({ metric, bracket }) => {
    const { user, better, unit, label } = metric;
    const average = metric.averages[bracket];
    const scaleMax = Math.max(user ?? 0, average ?? 0, 1) * 1.15;
    const userPct = user == null ? 0 : Math.min(100, (user / scaleMax) * 100);
    const avgPct = average == null ? null : Math.min(100, (average / scaleMax) * 100);

    let deltaText = '';
    let deltaTone = TONE_NEUTRAL;
    if (user != null && average != null && average > 0) {
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
                {/* Comparison track. The player's fill is one color up to the
                    ship average, then switches (green when exceeding the average
                    is good, red when it isn't) for the surplus beyond it — so the
                    color change itself marks the average. A high-contrast tick
                    with a panel-colored halo, extending past the bar, reinforces
                    the average position in both light and dark mode. */}
                <div
                    className="relative mt-2 mb-1 h-2.5 w-full"
                    role="img"
                    aria-label={`${label}: you ${formatMetricValue(user, unit)}, average ${formatMetricValue(average, unit)}`}
                >
                    <div
                        className="absolute inset-0 overflow-hidden rounded-full"
                        style={{ backgroundColor: 'var(--accent-faint)' }}
                    >
                        {/* Fill up to the average (or to the player value if below). */}
                        <div
                            className="absolute inset-y-0 left-0"
                            style={{
                                width: `${avgPct == null ? userPct : Math.min(userPct, avgPct)}%`,
                                backgroundColor: 'var(--accent-secondary-mid)',
                            }}
                        />
                        {/* Surplus beyond the average, tone-coded by performance. */}
                        {avgPct != null && userPct > avgPct ? (
                            <div
                                className="absolute inset-y-0"
                                style={{
                                    left: `${avgPct}%`,
                                    width: `${userPct - avgPct}%`,
                                    backgroundColor: better === 'high' ? TONE_GOOD : TONE_BAD,
                                }}
                            />
                        ) : null}
                    </div>
                    {avgPct != null ? (
                        <div
                            className="absolute top-[-3px] bottom-[-3px] w-[2px] rounded-full"
                            style={{
                                left: `calc(${avgPct}% - 1px)`,
                                backgroundColor: 'var(--text-strong)',
                                boxShadow: '0 0 0 1.5px var(--bg-surface)',
                            }}
                            title={`Ship average: ${formatMetricValue(average, unit)}`}
                        />
                    ) : null}
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
    const [bracket, setBracket] = useState<SkillBracket>('all');

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
                            {payload.brackets[bracket].players > 0
                                ? ` (${payload.brackets[bracket].players.toLocaleString()} captains, ${payload.brackets[bracket].battles.toLocaleString()} battles)`
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
                <>
                    {/* Compare against all players, or only the higher-skill
                        brackets (by overall account win rate). */}
                    <div className="mt-3 flex items-center gap-2">
                        <span className="text-[11px] uppercase tracking-wide text-[var(--text-muted)]">Compare vs</span>
                        <div className="inline-flex overflow-hidden rounded-md border border-[var(--accent-faint)] text-xs" role="group" aria-label="Skill bracket">
                            {BRACKET_OPTIONS.map((opt) => {
                                const isActive = bracket === opt.id;
                                return (
                                    <button
                                        key={opt.id}
                                        type="button"
                                        onClick={() => setBracket(opt.id)}
                                        aria-pressed={isActive}
                                        className={`px-2.5 py-1 transition-colors ${
                                            isActive
                                                ? 'bg-[var(--accent-secondary-mid)] font-semibold text-[var(--bg-card)]'
                                                : 'text-[var(--accent-secondary-mid)] hover:bg-[var(--accent-faint)]'
                                        }`}
                                    >
                                        {opt.label}
                                    </button>
                                );
                            })}
                        </div>
                    </div>

                    <div className="mt-3 space-y-4">
                        {payload.clusters.map((cluster) => (
                            <section key={cluster.name}>
                                <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--accent-mid)]">
                                    {cluster.name}
                                </h4>
                                <div className="divide-y divide-[var(--accent-faint)]">
                                    {cluster.metrics.map((metric) => (
                                        <MetricRow key={metric.key} metric={metric} bracket={bracket} />
                                    ))}
                                </div>
                            </section>
                        ))}
                    </div>
                </>
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
