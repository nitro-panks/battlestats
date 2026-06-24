"use client";

import React, { useEffect, useState } from 'react';
import { useRouter, useSelectedLayoutSegment } from 'next/navigation';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import ClanSVG from './ClanSVG';
import DeferredSection from './DeferredSection';
import LoadingPanel from './LoadingPanel';
import { resilientDynamicImport } from './resilientDynamicImport';
import { useClanMembers } from './useClanMembers';
import { buildClanPath, buildPlayerPath } from '../lib/entityRoutes';
import type { PlayerData } from './entityTypes';
import { fetchSharedJson, isAbortError } from '../lib/sharedJsonFetch';
import { PLAYER_ROUTE_FETCH_TTL_MS } from '../lib/playerRouteFetch';
import { PLAYER_NEXT_REFRESH_HEADER, PLAYER_REFRESH_PENDING_HEADER } from './usePlayerLiveRefresh';
import { useTheme } from '../context/ThemeContext';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';

// The clan rail lives in the PARENT layout (app/player/layout.tsx), ABOVE the
// `[playerName]` segment, so clicking another clan member soft-navigates the
// main well only — the rail stays mounted and the "current player" marker just
// moves. Spike + rationale: runbook-player-rail-soft-nav-2026-06-23.md.

const ClanMembers = dynamic(() => resilientDynamicImport(() => import('./ClanMembers'), 'PlayerRail-ClanMembers'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading clan members..." minHeight={96} />,
});

// Last-good clan identity for the rail. Retained across the inter-player fetch
// gap (and across a failed/404 new-player fetch) so a same-clan swap never
// blanks the rail — only updated when a new player's payload resolves.
interface ClanIdentity {
    clanId: number;
    clanName: string;
    clanTag: string | null;
    playerId: number;
}

const PlayerRailLayout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const router = useRouter();
    const { realm } = useRealm();
    const { theme } = useTheme();

    // The active player is the child route segment (raw/URL-encoded). Decoding
    // it gives the marker name; it updates synchronously on navigation, so the
    // marker moves the instant a member is clicked — before any fetch resolves.
    const segment = useSelectedLayoutSegment();
    const activePlayerName = segment ? decodeURIComponent(segment) : null;

    const [clanIdentity, setClanIdentity] = useState<ClanIdentity | null>(null);

    // Resolve the active player's clan from the shared player payload. This
    // dedupes onto PlayerRouteView's identical critical fetch (same URL =>
    // same cacheKey); the page's fetch wins the in-flight race (child effects
    // run before parent), so this never fires a second network request. We
    // request the same response headers + TTL defensively, so the dedup story
    // holds regardless of which subscriber creates the entry.
    useEffect(() => {
        if (!activePlayerName) {
            return;
        }

        const controller = new AbortController();
        let cancelled = false;

        const loadClan = async () => {
            try {
                const { data } = await fetchSharedJson<PlayerData>(
                    withRealm(`/api/player/${encodeURIComponent(activePlayerName)}/`, realm),
                    {
                        label: `Player rail ${activePlayerName}`,
                        ttlMs: PLAYER_ROUTE_FETCH_TTL_MS,
                        priority: 'high',
                        signal: controller.signal,
                        responseHeaders: [PLAYER_REFRESH_PENDING_HEADER, PLAYER_NEXT_REFRESH_HEADER],
                    },
                );
                if (cancelled) {
                    return;
                }
                setClanIdentity({
                    clanId: data.clan_id || 0,
                    clanName: data.clan_name || '',
                    clanTag: data.clan_tag ?? null,
                    playerId: data.player_id,
                });
            } catch (fetchError) {
                // Navigated away / aborted: benign. A real 404/5xx: the WELL
                // shows the error; the rail retains the prior clan identity so
                // it never blanks mid-swap.
                if (isAbortError(fetchError)) {
                    return;
                }
                console.error('Error loading clan rail:', fetchError);
            }
        };

        void loadClan();
        return () => {
            cancelled = true;
            controller.abort();
        };
    }, [activePlayerName, realm]);

    const clanId = clanIdentity?.clanId || null;
    const { members: clanMembers, loading: clanMembersLoading, error: clanMembersError } = useClanMembers(clanId);

    const handleSelectMember = (memberName: string) => router.push(buildPlayerPath(memberName, realm));

    return (
        <div className="relative bg-[var(--bg-page)] p-6">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[350px_1fr]">
                {/* Left rail: clan info (below player info on mobile) */}
                <div className="order-2 lg:order-1">
                    {clanIdentity === null ? (
                        // First load only — before the very first payload resolves.
                        // Avoids a flash of "No Clan" while the clan_id is unknown.
                        <LoadingPanel label="Loading clan..." minHeight={280} />
                    ) : clanId && clanIdentity ? (
                        <>
                            <div className="mb-4 pb-1">
                                <Link
                                    href={buildClanPath(clanId, clanIdentity.clanName || 'Clan', realm)}
                                    className="mt-1 text-xl font-semibold text-[var(--accent-mid)] underline-offset-4 hover:underline"
                                    aria-label={`Open clan page for ${clanIdentity.clanName || 'clan'}`}
                                >
                                    {clanIdentity.clanTag ? `[${clanIdentity.clanTag}] ` : ''}{clanIdentity.clanName || 'Clan'}
                                </Link>
                            </div>
                            <div id="clan_plot_container" className="mb-5 min-h-[280px]" data-perf-section="clan-plot">
                                <ClanSVG
                                    clanId={clanId}
                                    onSelectMember={handleSelectMember}
                                    highlightedPlayerName={activePlayerName ?? undefined}
                                    svgHeight={280}
                                    membersData={clanMembers}
                                    theme={theme}
                                />
                            </div>
                            <DeferredSection
                                className="pt-5"
                                minHeight={clanMembers.length > 0 ? Math.min(700, Math.max(96, clanMembers.length * 26 + 48)) : 96}
                                placeholder={<LoadingPanel label="Preparing clan members..." minHeight={96} />}
                                playerId={clanIdentity.playerId}
                                rootMargin="80px 0px"
                                sectionId="clan-members"
                            >
                                <div id="clan_members_container" className="pl-8">
                                    <ClanMembers
                                        members={clanMembers}
                                        loading={clanMembersLoading}
                                        error={clanMembersError}
                                        onSelectMember={handleSelectMember}
                                        layout="stacked"
                                        highlightedPlayerName={activePlayerName ?? undefined}
                                        source="player"
                                    />
                                </div>
                            </DeferredSection>
                        </>
                    ) : (
                        <>
                            <div className="mb-4 pb-1">
                                <h2 className="mt-1 text-xl font-semibold text-[var(--accent-mid)]">No Clan</h2>
                            </div>
                            <p className="text-sm text-[var(--accent-light)]">No clan data available</p>
                        </>
                    )}
                </div>

                {/* Right rail: the main player well (on top on mobile). */}
                <div className="order-1 min-w-0 text-left lg:order-2 lg:pl-4">
                    {children}
                </div>
            </div>
        </div>
    );
};

export default PlayerRailLayout;
