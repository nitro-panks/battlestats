"use client";

import React, { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import RealmTopShipsTreemapSVG from './RealmTopShipsTreemapSVG';
import ShipLeaderboard, { type ShipBucket, type ShipLeaderboardHandle } from './ShipLeaderboard';
import { buildPlayerPath } from '../lib/entityRoutes';
import { useRealm } from '../context/RealmContext';

const PlayerSearch: React.FC = () => {
    const { realm } = useRealm();
    const router = useRouter();
    const shipLeaderboardRef = useRef<ShipLeaderboardHandle>(null);
    // The ship bucket the leaderboard has resolved for the active filters. The
    // treemap renders off this (same tier+type+WR selection), so the two surfaces
    // stay in lockstep without the treemap issuing its own fetch. Null until the
    // leaderboard's first emit — the treemap shows a loading state meanwhile.
    const [bucket, setBucket] = useState<ShipBucket | null>(null);

    // A landing search resolves to the canonical player route — the player view
    // now lives only at /player/<name> (with its clan rail in the route layout,
    // see app/player/layout.tsx + the soft-nav runbook). The visible header
    // search box already navigates there; this redirect covers the SEO
    // SearchAction deep-link (/?q=<name>) and any bookmarked query. Realm comes
    // from context (not the server) so a bare ?q= keeps the stored preference,
    // and replace() so Back doesn't bounce between /player and /?q=.
    useEffect(() => {
        if (typeof window === 'undefined') {
            return;
        }
        const query = (new URLSearchParams(window.location.search).get('q') || '').trim();
        if (!query) {
            return;
        }
        router.replace(buildPlayerPath(query, realm));
    }, [router, realm]);

    return (
        <div className="p-4 lg:px-0">
            {/* Realm most-played-ships treemap. On lg the horizontal padding is
                dropped so the landing content aligns to the same [248,1252] band
                as the player page + header/footer (page.tsx supplies the inset). */}
            <div className="mt-2 pt-6">
                <RealmTopShipsTreemapSVG
                    ships={bucket?.ships ?? []}
                    tier={bucket?.tier ?? null}
                    type={bucket?.type ?? null}
                    wrPct={bucket?.wrPct ?? null}
                    windowStart={bucket?.windowStart}
                    windowEnd={bucket?.windowEnd}
                    loading={bucket ? bucket.loading : true}
                    pending={bucket?.pending ?? false}
                    empty={bucket?.empty ?? false}
                    onSelect={(sel) => shipLeaderboardRef.current?.selectShip(sel)}
                />
            </div>

            {/* Inline ship leaderboard: filter by tier+type, rank ships by
                win rate, drill into any ship's player board in place. It owns the
                bucket fetch and emits it up via onBucket so the treemap above
                renders the same selection. A treemap tile click hands off here via
                the ref (in place); tiles the board can't represent fall back to
                /ship/<id>. */}
            <ShipLeaderboard ref={shipLeaderboardRef} onBucket={setBucket} />
        </div>
    );
};

export default PlayerSearch;
