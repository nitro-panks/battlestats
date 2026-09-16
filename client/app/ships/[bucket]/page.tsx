import React from 'react';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import ShipBucketRouteView from './ShipBucketRouteView';
import { getSiteUrl } from '../../lib/siteOrigin';
import {
    buildShipBucketPath,
    parseShipBucketSegment,
    parseWrPctParam,
    shipBucketLabel,
} from '../../lib/entityRoutes';
import { parseSort } from '../../lib/tableSort';
import { resolveOgRealm } from '../../lib/ogCard';

// Shareable ship standings: /ships/t10-battleships?realm=na&wr=50&sort=win_rate
//
// Why this route exists rather than query params on `/`: the landing page is
// prerendered static, and reading searchParams in its metadata would turn the
// site's most-hit route into a per-request render for the sake of a share
// button. A dedicated route keeps `/` static and, as a second benefit, makes the
// tier x type buckets individually indexable.
//
// The bucket lives in the path because it is identity; the percentile and column
// sort are view state and stay in the query string, excluded from the canonical
// so those pages do not fragment into hundreds of near-duplicates.
//
// Runbook: agents/runbooks/runbook-shareable-ship-leaderboard-2026-08-20.md

// Columns the ship list can be sorted by, mirrored in app/og/route.tsx. An
// unrecognised value falls back to the server's natural order rather than
// throwing: these params arrive from wherever a link was pasted.
const SHIP_LIST_SORT_KEYS = ['ship_name', 'battles', 'avg_damage', 'kills_per_battle', 'win_rate'] as const;
type ListSortKey = (typeof SHIP_LIST_SORT_KEYS)[number];

interface ShipBucketPageProps {
    params: Promise<{ bucket: string }>;
    searchParams: Promise<{ realm?: string; wr?: string; sort?: string; dir?: string }>;
}

export async function generateMetadata({ params, searchParams }: ShipBucketPageProps): Promise<Metadata> {
    const { bucket: segment } = await params;
    const parsed = parseShipBucketSegment(segment);
    if (!parsed) {
        return { title: 'Ship standings — WoWs Battlestats' };
    }

    const { tier, type } = parsed;
    const query = await searchParams;
    const realm = resolveOgRealm(query.realm);
    const wrPct = parseWrPctParam(query.wr);
    const sort = parseSort<Record<string, unknown>>(query.sort, query.dir, SHIP_LIST_SORT_KEYS, ['ship_name']);

    const label = shipBucketLabel(tier, type);
    const realmLabel = realm.toUpperCase();

    // Canonical drops the view state: /ships/t10-battleships is one page whether
    // you arrived sorted by win rate or by damage.
    const canonical = getSiteUrl(`/ships/${segment}`);

    const ogParams = new URLSearchParams({ kind: 'shiplist', bucket: segment, realm });
    if (wrPct !== null) {
        ogParams.set('wr', String(wrPct));
    }
    if (sort) {
        ogParams.set('sort', String(sort.key));
        ogParams.set('dir', sort.dir);
    }
    const ogImage = getSiteUrl(`/og?${ogParams.toString()}`);

    const title = `Best ${label} — Ship Standings — WoWs Battlestats`;
    const description = `The best ${label.toLowerCase()} in World of Warships on ${realmLabel} — win rate, battles, average damage, and kills per battle over the current rolling window.`;
    const socialTitle = `Best ${label} — ${realmLabel}`;

    return {
        title,
        description,
        alternates: { canonical },
        openGraph: {
            title: socialTitle,
            description,
            url: getSiteUrl(buildShipBucketPath({ tier, type, realm, wrPct, sort: sort ? String(sort.key) : null, dir: sort?.dir ?? null })),
            siteName: 'WoWs Battlestats',
            type: 'website',
            images: [{ url: ogImage, width: 1200, height: 630, alt: socialTitle }],
        },
        twitter: {
            card: 'summary_large_image',
            title: socialTitle,
            description,
            images: [ogImage],
        },
    };
}

const ShipBucketPage = async ({ params, searchParams }: ShipBucketPageProps) => {
    const { bucket: segment } = await params;
    const parsed = parseShipBucketSegment(segment);
    // A malformed bucket is a dead link, not a board of arbitrary ships.
    if (!parsed) {
        notFound();
    }

    const query = await searchParams;
    const sort = parseSort<Record<string, unknown>>(query.sort, query.dir, SHIP_LIST_SORT_KEYS, ['ship_name']);

    return (
        <ShipBucketRouteView
            initial={{
                tier: parsed.tier,
                type: parsed.type,
                wrPct: parseWrPctParam(query.wr),
                sort: sort as { key: ListSortKey; dir: 'asc' | 'desc' } | null,
            }}
        />
    );
};

export default ShipBucketPage;
