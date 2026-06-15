'use client';

// Realm top-ships treemap.
//
// The 25 most-played ships on the active realm over the ROLLING TRAILING 14-DAY
// SHIP-STANDINGS WINDOW (the same window the /ship leaderboard + profile medals
// read — 1:1 with the player lists), as a treemap: each tile is one ship, sized
// by battles, COLORED BY SHIP TYPE and SHADED BY TIER (lighter = low tier, darker
// = high tier). Fed by `/api/realm/<realm>/top-ships`, recomputed nightly: it
// aggregates BattleEvent over the latest snapshot's window and joins Ship for
// type/tier.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import * as d3 from 'd3';
import { fetchSharedJson } from '../lib/sharedJsonFetch';
import { useTheme } from '../context/ThemeContext';
import { chartColors } from '../lib/chartTheme';
import { useRealm, useDisplayRealm } from '../context/RealmContext';
import { buildShipPath } from '../lib/entityRoutes';
import { formatSeasonLabel } from '../lib/shipSeason';
import { trackEvent } from '../lib/umami';
import { SHIP_TYPES, type ShipType, type Tier } from './ShipLeaderboard';

interface TopShip {
    ship_id: number;
    ship_name: string;
    ship_type: string | null;
    tier: number | null;
    battles: number;
}

type ShipMode = 'random' | 'ranked';

interface RealmTopShips {
    realm: string;
    window_days?: number;
    mode?: ShipMode;
    captured_on?: string | null;  // latest snapshot run date (== window_end), null if none yet
    window_start?: string;  // date-only ISO (UTC midnight), inclusive
    window_end?: string;    // date-only ISO (UTC midnight), exclusive (== captured_on)
    ships: TopShip[];
}

const SHIP_MODES: ShipMode[] = ['random', 'ranked'];
const SHIP_MODE_LABEL: Record<ShipMode, string> = { random: 'Random', ranked: 'Ranked' };

const SHIP_LIMIT = 25;
// 1h client TTL. The payload changes once per night (rolling trailing window,
// recomputed with the nightly snapshot); a short client TTL keeps a long-open tab
// from showing the previous day's window for long. The backend serves it from a
// warm window-end-tagged cache.
const TOP_SHIPS_FETCH_TTL_MS = 3_600_000;

const TYPE_LABEL: Record<string, string> = {
    Destroyer: 'DD',
    Cruiser: 'CA',
    Battleship: 'BB',
    AirCarrier: 'CV',
    Submarine: 'SS',
};

// Shade the type color by tier around its own lightness: low tier lighter,
// high tier darker — keeps the type hue recognizable while encoding tier.
const shadeByTier = (base: string, tier: number | null): string => {
    const c = d3.hsl(base);
    const t = (tier == null ? 6 : Math.max(1, Math.min(11, tier))) / 11;
    c.l = Math.max(0.16, Math.min(0.86, c.l * (1.28 - 0.56 * t)));
    return c.toString();
};

const fetchRealmTopShips = (realm: string, mode: ShipMode): Promise<RealmTopShips> =>
    fetchSharedJson<RealmTopShips>(
        `/api/realm/${encodeURIComponent(realm)}/top-ships?mode=${mode}&limit=${SHIP_LIMIT}`,
        {
            label: `RealmTopShips:${realm}:${mode}`,
            ttlMs: TOP_SHIPS_FETCH_TTL_MS,
            cacheKey: `top-ships:${realm}:${mode}:${SHIP_LIMIT}`,
        },
    ).then(({ data }) => data);

interface HoverState {
    ship: string;
    type: string | null;
    tier: number | null;
    battles: number;
    x: number;
    y: number;
}

interface RealmTopShipsTreemapSVGProps {
    // When provided, clicking a tile whose tier+type the inline ShipLeaderboard
    // can represent (T8/9/10 + a canonical type) drills there in place instead of
    // navigating to /ship/<id>. Tiles outside that range keep the route fallback.
    onSelect?: (sel: { id: number; name: string; tier: Tier; type: ShipType }) => void;
}

const RealmTopShipsTreemapSVG: React.FC<RealmTopShipsTreemapSVGProps> = ({ onSelect }) => {
    const { realm } = useRealm();
    // Mirror the latest onSelect into a ref so the D3 click handler (bound inside
    // the render effect below) reads the current callback without the render
    // effect depending on it — avoids rebuilding the treemap when it changes.
    const onSelectRef = useRef(onSelect);
    useEffect(() => { onSelectRef.current = onSelect; });
    // Hydration-safe realm for the heading/aria-label rendered in the SSG shell
    // (the live `realm` drives fetches; this only feeds rendered text).
    const displayRealm = useDisplayRealm();
    const router = useRouter();
    const { theme } = useTheme();
    const palette = chartColors[theme];
    const containerRef = useRef<HTMLDivElement | null>(null);
    const svgRef = useRef<SVGSVGElement | null>(null);
    const [data, setData] = useState<RealmTopShips | null>(null);
    const [width, setWidth] = useState(0);
    const [hover, setHover] = useState<HoverState | null>(null);
    const [mode, setMode] = useState<ShipMode>('random');

    const typeColor = useMemo(() => (type: string | null): string => {
        switch (type) {
            case 'Destroyer': return palette.shipDD;
            case 'Cruiser': return palette.shipCA;
            case 'Battleship': return palette.shipBB;
            case 'AirCarrier': return palette.shipCV;
            case 'Submarine': return palette.shipSS;
            default: return palette.shipDefault;
        }
    }, [palette]);

    // Resting fill for a tile. In dark mode tiles are knocked a bit darker so
    // the lighten-on-hover highlight reads with more contrast.
    const tileFill = useCallback((ship: TopShip): string => {
        const base = shadeByTier(typeColor(ship.ship_type), ship.tier);
        if (theme !== 'dark') return base;
        const c = d3.hsl(base);
        c.l = Math.max(0.10, c.l * 0.85);
        return c.toString();
    }, [typeColor, theme]);

    useEffect(() => {
        let cancelled = false;
        fetchRealmTopShips(realm, mode)
            .then((d) => { if (!cancelled) setData(d); })
            .catch(() => { if (!cancelled) setData(null); });
        return () => { cancelled = true; };
    }, [realm, mode]);

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

    // Rolling-window range label (e.g. "1–14 Jun") from the payload's date-only
    // bounds. `window_end` is the exclusive end (== the snapshot's captured_on);
    // formatSeasonLabel already steps back a day for the last included date.
    const windowLabel = useMemo(() => {
        if (!data?.window_start || !data?.window_end) return null;
        const startMs = Date.parse(data.window_start);
        const endMs = Date.parse(data.window_end);
        if (Number.isNaN(startMs) || Number.isNaN(endMs)) return null;
        return formatSeasonLabel(startMs, endMs);
    }, [data?.window_start, data?.window_end]);

    useEffect(() => {
        if (!svgRef.current || !data || width <= 0 || data.ships.length === 0) return;

        const root = d3.hierarchy({ children: data.ships } as { children: TopShip[] })
            .sum((d: TopShip) => d.battles || 0)
            .sort((a: { value?: number }, b: { value?: number }) => (b.value ?? 0) - (a.value ?? 0));

        d3.treemap().size([width, height]).paddingInner(2).round(true)(root);
        const leaves = root.leaves();

        const svg = d3.select(svgRef.current);
        svg.selectAll('*').remove();
        svg.attr('viewBox', `0 0 ${width} ${height}`).attr('width', '100%').attr('height', height);

        const g = svg.selectAll('g').data(leaves).join('g')
            .attr('transform', (d: { x0: number; y0: number }) => `translate(${d.x0},${d.y0})`);

        g.append('rect')
            .attr('width', (d: { x0: number; x1: number }) => Math.max(0, d.x1 - d.x0))
            .attr('height', (d: { y0: number; y1: number }) => Math.max(0, d.y1 - d.y0))
            .attr('rx', 2)
            .attr('fill', (d: { data: TopShip }) => tileFill(d.data))
            .attr('stroke', 'var(--bg-card)')
            .attr('stroke-width', 1)
            .style('cursor', 'pointer')
            .on('click', function onClick(this: SVGRectElement, _event: MouseEvent, d: { data: TopShip }) {
                const { ship_id, ship_name, tier, ship_type } = d.data;
                // The leaderboard only covers T8/9/10 + the five canonical types;
                // anything else (sub-T8, null tier/type, unknown type) keeps the
                // /ship/<id> route so no tile becomes a dead click.
                const supported = !!onSelectRef.current
                    && (tier === 8 || tier === 9 || tier === 10)
                    && ship_type != null && (SHIP_TYPES as readonly string[]).includes(ship_type);
                if (supported) {
                    trackEvent('treemap-ship', { ship_id, ship_name, mode, realm, target: 'leaderboard' });
                    onSelectRef.current!({ id: ship_id, name: ship_name, tier: tier as Tier, type: ship_type as ShipType });
                } else {
                    trackEvent('treemap-ship', { ship_id, ship_name, mode, realm, target: 'route' });
                    router.push(buildShipPath(ship_id, ship_name, realm));
                }
            })
            .on('mousemove', function onMove(this: SVGRectElement, event: MouseEvent, d: { data: TopShip }) {
                const rect = containerRef.current?.getBoundingClientRect();
                setHover({
                    ship: d.data.ship_name,
                    type: d.data.ship_type,
                    tier: d.data.tier,
                    battles: d.data.battles,
                    x: rect ? event.clientX - rect.left : 0,
                    y: rect ? event.clientY - rect.top : 0,
                });
                if (theme === 'dark') {
                    // Dark by default, light on hover: lighten only the hovered
                    // tile's own fill, leaving the rest of the map untouched.
                    const c = d3.hsl(tileFill(d.data));
                    c.l = Math.min(0.95, c.l + 0.22);
                    d3.select(this).attr('fill', c.toString());
                } else {
                    svg.selectAll('rect').attr('opacity', 0.4);
                    d3.select(this).attr('opacity', 1);
                }
            })
            .on('mouseleave', function onLeave(this: SVGRectElement, _event: MouseEvent, d: { data: TopShip }) {
                setHover(null);
                if (theme === 'dark') {
                    d3.select(this).attr('fill', tileFill(d.data));
                } else {
                    svg.selectAll('rect').attr('opacity', 1);
                }
            });

        // Labels on tiles with enough room. Text color picks contrast off the
        // tile's own lightness so it reads on both light and dark shades.
        g.each(function labelTile(this: SVGGElement, d: { x0: number; x1: number; y0: number; y1: number; data: TopShip }) {
            const w = d.x1 - d.x0;
            const h = d.y1 - d.y0;
            if (w < 46 || h < 24) return;
            const fill = tileFill(d.data);
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
                    .text(`${d.data.battles.toLocaleString()} · T${d.data.tier ?? '?'}`);
            }
        });
    }, [data, width, height, palette, typeColor, realm, router, theme, tileFill, mode]);

    return (
        <section
            className="w-full"
            aria-label="Realm top ships treemap"
        >
            <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
                <div className="flex items-center gap-3">
                    <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                        {displayRealm.toUpperCase()} most-played ships · Last 14 days{windowLabel ? ` · ${windowLabel}` : ''}
                    </h2>
                    <div className="flex items-center gap-1 text-xs" role="group" aria-label="Battle mode">
                        {SHIP_MODES.map((m) => (
                            <button
                                key={m}
                                type="button"
                                onClick={() => { if (mode !== m) { setMode(m); trackEvent(m === 'random' ? 'treemap-random' : 'treemap-ranked', { realm }); } }}
                                aria-pressed={mode === m}
                                className={`rounded px-2 py-0.5 transition-colors ${
                                    mode === m
                                        ? 'bg-[var(--accent-mid)] text-[var(--bg-card)] font-semibold'
                                        : 'text-[var(--text-muted)] hover:text-[var(--text-strong)]'
                                }`}
                            >
                                {SHIP_MODE_LABEL[m]}
                            </button>
                        ))}
                    </div>
                </div>
            </div>
            <div ref={containerRef} className="relative w-full max-w-[900px]">
                <svg ref={svgRef} role="img" aria-label={`${displayRealm} top ${SHIP_LIMIT} most-played ships over the rolling trailing 14-day ship-standings window`} />
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
                            {hover.battles.toLocaleString()} battles · {TYPE_LABEL[hover.type ?? ''] ?? hover.type ?? '—'} · T{hover.tier ?? '?'}
                        </div>
                    </div>
                )}
            </div>
        </section>
    );
};

export default RealmTopShipsTreemapSVG;
