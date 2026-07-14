'use client';

// Realm ship visualization — correlated to the ship-filter selection below it.
//
// The most-played ships of the CURRENTLY-SELECTED tier + type (the same bucket
// the inline ShipLeaderboard table shows), over the rolling trailing 30-day
// ship-standings window, in one of two views the visitor toggles (top-right,
// persisted per-browser):
//   • MAP  (default) — a treemap: each tile is one ship, SIZED BY BATTLES and
//     COLORED BY WIN RATE (the same `wrColor` scale the table uses).
//   • PLOT — a scatterplot of every ship in the bucket: battles on the x axis,
//     win rate on the y axis, dots COLORED BY WIN RATE (same scale).
// Both reflect the table's WR filter (All / top-50% / top-25%) too.
//
// This component is PRESENTATIONAL: it does not fetch. `ShipLeaderboard` owns the
// `/api/realm/<realm>/ships?tier&type&wr_pct` fetch (its restore/persist/poll
// logic) and hands the resolved bucket up through `PlayerSearch`, which passes it
// here. See runbook-landing-treemap-filter-correlation-2026-07-01.

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCircleInfo } from '@fortawesome/free-solid-svg-icons';
import * as d3 from 'd3';
import { useDisplayRealm, useRealm } from '../context/RealmContext';
import { buildShipPath } from '../lib/entityRoutes';
import { formatSeasonLabel } from '../lib/shipSeason';
import { shipClass } from '../lib/shipIdentity';
import wrColor from '../lib/wrColor';
import { formatCompactCount } from '../lib/chartTheme';
import { trackEvent } from '../lib/umami';
import { SHIP_TYPES, type ListShip, type ShipType, type Tier, type WrPct } from './ShipLeaderboard';

// How many tiles the treemap draws. A tier+type bucket can hold ~20-40 ships;
// capping to the most-played keeps tile labels legible (the full set is always
// available in the table below).
const TILE_LIMIT = 25;

// The two visualizations this component can render for the same bucket: the
// battle-sized/WR-colored treemap ('map', default) or a scatterplot of every
// ship in the bucket ('plot'). The choice is a per-browser preference
// (localStorage), read once after mount so SSR stays deterministic (mirrors
// ShipLeaderboard's stored-prefs pattern).
type LandingShipView = 'map' | 'plot';
const LANDING_SHIP_VIEW_KEY = 'bs-landing-ship-view';

function readStoredShipView(): LandingShipView | null {
    if (typeof window === 'undefined') return null;
    try {
        const raw = window.localStorage.getItem(LANDING_SHIP_VIEW_KEY);
        return raw === 'map' || raw === 'plot' ? raw : null;
    } catch {
        return null;
    }
}

const TYPE_LABEL: Record<string, string> = {
    Destroyer: 'DD',
    Cruiser: 'CA',
    Battleship: 'BB',
    AirCarrier: 'CV',
    Submarine: 'SS',
};

// Plural class label for the heading ("Battleships", "Aircraft Carriers").
const pluralTypeLabel = (type: ShipType | null): string => {
    if (!type) return 'ships';
    const label = shipClass(type)?.label ?? type;
    return label.endsWith('s') ? label : `${label}s`;
};

interface HoverState {
    ship: string;
    type: string | null;
    tier: number | null;
    battles: number;
    winRate: number;
    x: number;
    y: number;
}

interface RealmTopShipsTreemapSVGProps {
    // The bucket to draw — the resolved ship list + filter context from
    // ShipLeaderboard (via PlayerSearch). `ships` is already the tier+type (and
    // WR-percentile) selection; this component only visualizes it.
    ships: ListShip[];
    tier: Tier | null;
    type: ShipType | null;
    wrPct: WrPct;
    windowStart?: string;  // date-only ISO (UTC midnight), inclusive
    windowEnd?: string;    // date-only ISO (UTC midnight), exclusive (== captured_on)
    loading?: boolean;     // first load / filter switch in flight
    pending?: boolean;     // cold WR-percentile bucket being computed server-side
    empty?: boolean;       // bucket has no ships (e.g. T9 sub/CV easter egg)
    // Clicking a tile whose tier+type the inline ShipLeaderboard can represent
    // (T8/9/10 + a canonical type) drills there in place; anything else keeps the
    // /ship/<id> route fallback.
    onSelect?: (sel: { id: number; name: string; tier: Tier; type: ShipType }) => void;
}

const RealmTopShipsTreemapSVG: React.FC<RealmTopShipsTreemapSVGProps> = ({
    ships,
    tier,
    type,
    wrPct,
    windowStart,
    windowEnd,
    loading = false,
    pending = false,
    empty = false,
    onSelect,
}) => {
    const { realm } = useRealm();
    // Mirror the latest onSelect into a ref so the D3 click handler reads the
    // current callback without the render effect depending on it.
    const onSelectRef = useRef(onSelect);
    useEffect(() => { onSelectRef.current = onSelect; });
    // Hydration-safe realm for the heading/aria-label rendered in the SSG shell.
    const displayRealm = useDisplayRealm();
    const router = useRouter();
    const containerRef = useRef<HTMLDivElement | null>(null);
    const svgRef = useRef<SVGSVGElement | null>(null);
    const [width, setWidth] = useState(0);
    const [hover, setHover] = useState<HoverState | null>(null);
    // Which view to draw. Default 'map' on both server and first client render
    // (deterministic SSR); the stored preference is applied in a mount effect,
    // mirroring ShipLeaderboard's stored-prefs restore. `setView` writes through
    // to localStorage so the choice sticks for subsequent loads.
    const [view, setViewState] = useState<LandingShipView>('map');
    useEffect(() => {
        const stored = readStoredShipView();
        if (stored) setViewState(stored);
    }, []);
    const setView = (next: LandingShipView) => {
        setViewState(next);
        try {
            window.localStorage.setItem(LANDING_SHIP_VIEW_KEY, next);
        } catch {
            // Ignore storage failures (private mode / quota) — the view still switches.
        }
    };

    // The tiles: most-played ships in the bucket, capped for legibility (the
    // treemap needs room for tile labels). The scatterplot has no label
    // constraint, so it plots the FULL bucket (`ships`) — "the same ships",
    // just without the map's legibility cap.
    const tiles = useMemo(
        () => [...ships].sort((a, b) => b.battles - a.battles).slice(0, TILE_LIMIT),
        [ships],
    );
    // Scatter points: the full bucket, keeping only ships with a battle count to
    // place on the x axis. Empty exactly when `tiles` is (both derive from `ships`).
    const points = useMemo(
        () => ships.filter((s) => (s.battles || 0) > 0),
        [ships],
    );
    // The data the active view draws — drives the render effect + empty-state box.
    const activeCount = view === 'map' ? tiles.length : points.length;

    useEffect(() => {
        if (!containerRef.current) return undefined;
        const ro = new ResizeObserver((entries) => {
            setWidth(Math.round(entries[0]?.contentRect.width ?? 0));
        });
        ro.observe(containerRef.current);
        return () => ro.disconnect();
    }, []);

    const height = useMemo(
        () => Math.max(280, Math.min(440, Math.round(width * 0.4))),
        [width],
    );

    // Rolling-window range label (e.g. "1–30 Jun") from the payload's date-only
    // bounds. `windowEnd` is the exclusive end; formatSeasonLabel steps back a day
    // for the last included date.
    const windowLabel = useMemo(() => {
        if (!windowStart || !windowEnd) return null;
        const startMs = Date.parse(windowStart);
        const endMs = Date.parse(windowEnd);
        if (Number.isNaN(startMs) || Number.isNaN(endMs)) return null;
        return formatSeasonLabel(startMs, endMs);
    }, [windowStart, windowEnd]);

    const bucketLabel = useMemo(() => {
        if (tier == null || type == null) return null;
        return `T${tier} ${pluralTypeLabel(type)}`;
    }, [tier, type]);

    // Dim the current view while a filter switch / cold percentile bucket is in
    // flight so the redraw reads as an update rather than a flash of the previous
    // bucket.
    const dim = (loading || pending) && activeCount > 0;

    useEffect(() => {
        const data = view === 'map' ? tiles : points;
        if (!svgRef.current || width <= 0 || data.length === 0) {
            // Clear any prior render (e.g. bucket went empty, or a view switch) so
            // a stale chart doesn't linger under the empty/loading box. Also
            // collapse the svg's height to 0 — otherwise the height attr from a
            // prior populated render sticks around and STACKS on top of the empty
            // box, pushing the ship leaderboard below down by a chart's height.
            if (svgRef.current) {
                d3.select(svgRef.current).selectAll('*').remove();
                d3.select(svgRef.current).attr('height', 0);
            }
            return;
        }

        const svg = d3.select(svgRef.current);
        svg.selectAll('*').remove();
        svg.attr('viewBox', `0 0 ${width} ${height}`).attr('width', '100%').attr('height', height);

        // Shared: what a tile/dot click does. The leaderboard only covers T8/9/10 +
        // the five canonical types; anything else keeps the /ship/<id> route so no
        // mark is a dead click.
        const activate = (s: ListShip) => {
            const { ship_id, ship_name, tier: shipTier, ship_type } = s;
            const supported = !!onSelectRef.current
                && (shipTier === 8 || shipTier === 9 || shipTier === 10)
                && ship_type != null && (SHIP_TYPES as readonly string[]).includes(ship_type);
            if (supported) {
                trackEvent('treemap-ship', { ship_id, ship_name, realm, target: 'leaderboard', view });
                onSelectRef.current!({ id: ship_id, name: ship_name, tier: shipTier as Tier, type: ship_type as ShipType });
            } else {
                trackEvent('treemap-ship', { ship_id, ship_name, realm, target: 'route', view });
                router.push(buildShipPath(ship_id, ship_name, realm));
            }
        };
        // Shared: record hover for the HTML tooltip overlay.
        const showHover = (s: ListShip, event: MouseEvent) => {
            const rect = containerRef.current?.getBoundingClientRect();
            setHover({
                ship: s.ship_name,
                type: s.ship_type,
                tier: s.tier,
                battles: s.battles,
                winRate: s.win_rate,
                x: rect ? event.clientX - rect.left : 0,
                y: rect ? event.clientY - rect.top : 0,
            });
        };

        if (view === 'plot') {
            // Scatterplot: battles (x) vs win rate (y), dots colored by WR. Axis
            // margins the treemap doesn't need; styling mirrors the other axed D3
            // charts (var(--*) tokens so it reads correctly in both themes).
            const margin = { top: 12, right: 16, bottom: 36, left: 48 };
            const innerW = Math.max(0, width - margin.left - margin.right);
            const innerH = Math.max(0, height - margin.top - margin.bottom);

            const x = d3.scaleLinear()
                .domain([0, d3.max(points, (d: ListShip) => d.battles) || 1])
                .nice()
                .range([0, innerW]);

            const wrExtent = d3.extent(points, (d: ListShip) => d.win_rate) as [number, number];
            const wrMin = wrExtent[0] ?? 45;
            const wrMax = wrExtent[1] ?? 55;
            const wrPad = Math.max(1, (wrMax - wrMin) * 0.12);
            const y = d3.scaleLinear()
                .domain([wrMin - wrPad, wrMax + wrPad])
                .nice()
                .range([innerH, 0]);

            const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

            // Horizontal gridlines for reading WR off the y axis.
            const grid = g.append('g')
                .call(d3.axisLeft(y).ticks(6).tickSize(-innerW).tickFormat(() => ''));
            grid.select('.domain').remove();
            grid.selectAll('line')
                .attr('stroke', 'var(--border)')
                .attr('stroke-opacity', 0.55);

            const xAxis = g.append('g')
                .attr('transform', `translate(0,${innerH})`)
                .style('color', 'var(--text-muted)')
                .call(d3.axisBottom(x).ticks(5).tickFormat((v: number) => formatCompactCount(v)).tickSizeOuter(0));
            xAxis.selectAll('text').attr('font-size', 10).attr('fill', 'var(--text-muted)');

            const yAxis = g.append('g')
                .style('color', 'var(--text-muted)')
                .call(d3.axisLeft(y).ticks(6).tickFormat((v: number) => `${v}%`).tickSizeOuter(0));
            yAxis.selectAll('text').attr('font-size', 10).attr('fill', 'var(--text-muted)');

            g.append('text')
                .attr('x', innerW / 2).attr('y', innerH + 30)
                .attr('text-anchor', 'middle').attr('font-size', 10).attr('fill', 'var(--text-muted)')
                .text('Battles');
            g.append('text')
                .attr('transform', 'rotate(-90)')
                .attr('x', -innerH / 2).attr('y', -36)
                .attr('text-anchor', 'middle').attr('font-size', 10).attr('fill', 'var(--text-muted)')
                .text('Win rate');

            g.selectAll('circle.pt')
                .data(points)
                .join('circle')
                .attr('class', 'pt')
                .attr('cx', (d: ListShip) => x(d.battles))
                .attr('cy', (d: ListShip) => y(d.win_rate))
                .attr('r', 5)
                .attr('fill', (d: ListShip) => wrColor(d.win_rate))
                .attr('stroke', 'var(--bg-card)')
                .attr('stroke-width', 1)
                .style('cursor', 'pointer')
                .on('click', function onClick(this: SVGCircleElement, _event: MouseEvent, d: ListShip) {
                    activate(d);
                })
                .on('mousemove', function onMove(this: SVGCircleElement, event: MouseEvent, d: ListShip) {
                    showHover(d, event);
                    g.selectAll('circle.pt').attr('opacity', 0.4);
                    d3.select(this).attr('opacity', 1);
                })
                .on('mouseleave', function onLeave(this: SVGCircleElement) {
                    setHover(null);
                    g.selectAll('circle.pt').attr('opacity', 1);
                });
            return;
        }

        // MAP (treemap).
        const root = d3.hierarchy({ children: tiles } as { children: ListShip[] })
            .sum((d: ListShip) => d.battles || 0)
            .sort((a: { value?: number }, b: { value?: number }) => (b.value ?? 0) - (a.value ?? 0));

        d3.treemap().size([width, height]).paddingInner(2).round(true)(root);
        const leaves = root.leaves();

        const g = svg.selectAll('g').data(leaves).join('g')
            .attr('transform', (d: { x0: number; y0: number }) => `translate(${d.x0},${d.y0})`);

        g.append('rect')
            .attr('width', (d: { x0: number; x1: number }) => Math.max(0, d.x1 - d.x0))
            .attr('height', (d: { y0: number; y1: number }) => Math.max(0, d.y1 - d.y0))
            .attr('rx', 2)
            .attr('fill', (d: { data: ListShip }) => wrColor(d.data.win_rate))
            .attr('stroke', 'var(--bg-card)')
            .attr('stroke-width', 1)
            .style('cursor', 'pointer')
            .on('click', function onClick(this: SVGRectElement, _event: MouseEvent, d: { data: ListShip }) {
                activate(d.data);
            })
            .on('mousemove', function onMove(this: SVGRectElement, event: MouseEvent, d: { data: ListShip }) {
                showHover(d.data, event);
                svg.selectAll('rect').attr('opacity', 0.55);
                d3.select(this).attr('opacity', 1);
            })
            .on('mouseleave', function onLeave(this: SVGRectElement) {
                setHover(null);
                svg.selectAll('rect').attr('opacity', 1);
            });

        // Labels on tiles with enough room. Text color picks contrast off the
        // tile's own lightness so it reads on both light and dark WR shades.
        g.each(function labelTile(this: SVGGElement, d: { x0: number; x1: number; y0: number; y1: number; data: ListShip }) {
            const w = d.x1 - d.x0;
            const h = d.y1 - d.y0;
            if (w < 46 || h < 24) return;
            const fill = wrColor(d.data.win_rate);
            const textColor = d3.hsl(fill).l > 0.62 ? '#1a1a1a' : '#f5f5f5';
            const maxChars = Math.max(3, Math.floor((w - 8) / 6.2));
            const name = d.data.ship_name.length > maxChars
                ? `${d.data.ship_name.slice(0, maxChars - 1)}…`
                : d.data.ship_name;
            const node = d3.select(this);
            node.append('text')
                .attr('x', 5).attr('y', 15)
                .attr('font-size', 11).attr('font-weight', 600).attr('fill', textColor)
                .style('pointer-events', 'none')
                .text(name);
            if (h >= 38) {
                node.append('text')
                    .attr('x', 5).attr('y', 29)
                    .attr('font-size', 10).attr('fill', textColor).attr('opacity', 0.85)
                    .style('pointer-events', 'none')
                    .text(`${d.data.battles.toLocaleString()} · ${d.data.win_rate.toFixed(1)}%`);
            }
        });
    }, [view, tiles, points, width, height, realm, router]);

    return (
        <section
            className="mx-auto w-full max-w-[830px]"
            aria-label="Realm ship chart"
        >
            <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
                <div className="flex items-center gap-3">
                    <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                        {displayRealm.toUpperCase()} most-played{bucketLabel ? ` ${bucketLabel}` : ' ships'}{wrPct ? ` · top ${wrPct}%` : ''}{windowLabel ? ` · ${windowLabel}` : ''}
                    </h2>
                    <div className="group relative inline-flex items-center">
                        <button
                            type="button"
                            className="inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] text-[var(--accent-light)] transition-colors hover:text-[var(--accent-mid)] focus:outline-none focus-visible:text-[var(--accent-mid)]"
                            aria-label="About the ship treemap and its eligibility window"
                        >
                            <FontAwesomeIcon icon={faCircleInfo} className="text-[10px]" aria-hidden="true" />
                        </button>
                        <div className="pointer-events-none absolute left-0 top-full z-20 mt-2 hidden w-[27rem] max-w-[calc(100vw-2rem)] rounded-md border border-[var(--border)] bg-[var(--bg-page)] px-3 py-3 text-left text-xs normal-case tracking-normal text-[var(--text-primary)] shadow-lg group-hover:block group-focus-within:block">
                            <p className="font-semibold uppercase tracking-wide text-[var(--accent-mid)]">Ship {view === 'plot' ? 'scatterplot' : 'treemap'}</p>
                            <p className="mt-2 text-[11px] leading-5 text-[var(--text-secondary)]">The most-played ships of the tier &amp; type selected below. <span className="font-semibold text-[var(--accent-mid)]">Map</span> draws each as a tile sized by battles; <span className="font-semibold text-[var(--accent-mid)]">Plot</span> charts each by battles (x) and win rate (y). Both color by win rate and follow the filters (tier, type, WR) below; tap a ship to open its leaderboard.</p>
                            <p className="mt-2 text-[11px] leading-5 text-[var(--text-secondary)]"><span className="font-semibold text-[var(--accent-mid)]">Eligibility window:</span> a rolling, trailing 30-day ship-standings window recomputed nightly — the same window the ship leaderboards and profile medals read. The dates shown are its current bounds.</p>
                        </div>
                    </div>
                </div>
                {/* Map / Plot view toggle — persisted per-browser. Right-aligned by
                    the header's justify-between. */}
                <div
                    className="inline-flex items-center gap-0.5 rounded-md border border-[var(--border)] p-0.5"
                    role="group"
                    aria-label="Chart view"
                >
                    {(['map', 'plot'] as const).map((mode) => (
                        <button
                            key={mode}
                            type="button"
                            onClick={() => {
                                if (mode !== view) {
                                    trackEvent('landing-chart-view', { view: mode, realm });
                                }
                                setView(mode);
                            }}
                            aria-pressed={view === mode}
                            className={`rounded px-2.5 py-1 text-xs font-semibold uppercase tracking-wide transition-colors ${
                                view === mode
                                    ? 'bg-[var(--accent-faint)] text-[var(--accent-mid)]'
                                    : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                            }`}
                        >
                            {mode === 'map' ? 'Map' : 'Plot'}
                        </button>
                    ))}
                </div>
            </div>
            <div ref={containerRef} className="relative w-full">
                <svg
                    ref={svgRef}
                    role="img"
                    aria-label={`${displayRealm} most-played ${bucketLabel ?? 'ships'} over the rolling trailing 30-day ship-standings window, shown as a ${view === 'plot' ? 'battles-vs-win-rate scatterplot' : 'treemap'}`}
                    style={{ opacity: dim ? 0.55 : 1, transition: 'opacity 150ms ease' }}
                />
                {activeCount === 0 && (
                    // Same height as a populated chart so the content below (the
                    // ship leaderboard) never shifts when a bucket is empty. T9
                    // sub/CV (no such ships exist) render a plain empty box for now;
                    // loading/cold states keep a short caption.
                    <div
                        className="flex items-center justify-center rounded-md border border-dashed border-[var(--border)] text-sm text-[var(--text-muted)]"
                        style={{ height }}
                        aria-label={empty ? 'No ships for this selection' : undefined}
                    >
                        {empty
                            ? null
                            : (loading || pending)
                                ? 'Loading ships…'
                                : 'No ships to display.'}
                    </div>
                )}
                {hover && (
                    <div
                        className="pointer-events-none absolute z-10 rounded bg-[var(--bg-page)] px-2 py-1 text-xs shadow-md ring-1 ring-[var(--accent-faint)]"
                        style={{
                            left: Math.min(Math.max(hover.x + 10, 0), Math.max(width - 150, 0)),
                            top: Math.max(hover.y - 40, 0),
                        }}
                    >
                        <div className="font-semibold text-[var(--text-strong)]">{hover.ship}</div>
                        <div className="text-[var(--text-muted)]">
                            {hover.battles.toLocaleString()} battles · {hover.winRate.toFixed(1)}% WR · {TYPE_LABEL[hover.type ?? ''] ?? hover.type ?? '—'} · T{hover.tier ?? '?'}
                        </div>
                    </div>
                )}
            </div>
        </section>
    );
};

export default RealmTopShipsTreemapSVG;
