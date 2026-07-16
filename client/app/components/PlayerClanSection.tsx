"use client";

import React from 'react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import DeferredSection from './DeferredSection';
import LoadingPanel from './LoadingPanel';
import { resilientDynamicImport } from './resilientDynamicImport';
import { useClanMembers } from './useClanMembers';
import ClanActivityRoster from './ClanActivityRoster';
import { buildClanPath, buildPlayerPath } from '../lib/entityRoutes';
import { useRealm } from '../context/RealmContext';
import { useTheme } from '../context/ThemeContext';
import { trackEvent } from '../lib/umami';

// Player-page clan section (below the insights tabs): a compact version of the
// clan page — the clan activity scatterplot, then the roster as one flowing
// paragraph of names per collapsed activity phase (ClanActivityRoster, shared
// with the clan page). Replaces the retired left clan rail as the player
// page's clan surface.

const ClanSVG = dynamic(() => resilientDynamicImport(() => import('./ClanSVG'), 'PlayerClanSection-ClanSVG'), {
    ssr: false,
    loading: () => <LoadingPanel tone="muted" label="Loading clan chart..." minHeight={440} />,
});

interface PlayerClanSectionProps {
    clanId: number;
    clanName: string;
    clanTag: string | null;
    playerId: number;
    playerName: string;
}

const PlayerClanSection: React.FC<PlayerClanSectionProps> = ({ clanId, clanName, clanTag, playerId, playerName }) => {
    const router = useRouter();
    const { realm } = useRealm();
    const { theme } = useTheme();
    const { members, loading, error } = useClanMembers(clanId);

    const handleChartSelectMember = (memberName: string) => {
        trackEvent('clan-member-click', { realm, source: 'player' });
        router.push(buildPlayerPath(memberName, realm));
    };

    return (
        <section className="relative mt-10 border-t border-[var(--border)] pt-6" data-testid="player-clan-section">
            <h2 className="text-xl font-semibold tracking-tight">
                <Link
                    href={buildClanPath(clanId, clanName || 'Clan', realm)}
                    className="text-[var(--accent-mid)] underline-offset-4 hover:underline"
                    aria-label={`Open clan page for ${clanName || 'clan'}`}
                >
                    {clanTag ? `[${clanTag}] ` : ''}{clanName || 'Clan'}
                </Link>
            </h2>

            <DeferredSection
                minHeight={560}
                placeholder={<LoadingPanel tone="muted" label="Preparing clan overview..." minHeight={560} />}
                playerId={playerId}
                rootMargin="240px 0px"
                sectionId="player-clan-section"
            >
                {/* Pull the chart left by its y-axis margin so the axis sits flush
                    with the section's left edge, matching the clan page. */}
                <div className="mt-4 md:-ml-[38px]">
                    <ClanSVG
                        clanId={clanId}
                        svgWidth={850}
                        svgHeight={440}
                        membersData={members}
                        theme={theme}
                        highlightedPlayerName={playerName}
                        onSelectMember={handleChartSelectMember}
                        // Lift the log/linear toggle onto the clan-heading line
                        // (the section is the positioning ancestor).
                        scaleToggleClassName="absolute right-0 top-[27px]"
                    />
                </div>

                <div className="mt-6">
                    <ClanActivityRoster
                        members={members}
                        loading={loading}
                        error={error}
                        highlightedPlayerName={playerName}
                        source="player"
                        // No phase headers under the scatterplot — the chart's
                        // legend already names the phases; each paragraph leads
                        // with its activity icon instead.
                        phaseStyle="icon-lead"
                    />
                </div>
            </DeferredSection>
        </section>
    );
};

export default PlayerClanSection;
