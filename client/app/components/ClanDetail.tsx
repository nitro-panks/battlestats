import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import DeferredSection from './DeferredSection';
import { resilientDynamicImport } from './resilientDynamicImport';
import { useClanMembers } from './useClanMembers';
import { useClanMemberTiers } from './useClanMemberTiers';
import { useRealm } from '../context/RealmContext';
import { useTheme } from '../context/ThemeContext';
import { incrementChartFetches, decrementChartFetches } from '../lib/sharedJsonFetch';
import { trackEvent } from '../lib/umami';

interface ClanDetailProps {
    clan: {
        clan_id: number;
        name: string;
        tag: string;
        members_count: number;
    };
    onSelectMember: (memberName: string) => void;
}

import LoadingPanel from './LoadingPanel';

const ClanSVG = dynamic(() => resilientDynamicImport(() => import('./ClanSVG'), 'ClanDetail-ClanSVG'), {
    ssr: false,
    loading: () => <LoadingPanel tone="muted" label="Loading clan chart..." minHeight={440} />,
});

const Clan3DSVG = dynamic(() => resilientDynamicImport(() => import('./Clan3DSVG'), 'ClanDetail-Clan3DSVG'), {
    ssr: false,
    loading: () => <LoadingPanel tone="muted" label="Loading 3D clan chart..." minHeight={480} />,
});

const ClanBattleSeasons = dynamic(() => resilientDynamicImport(() => import('./ClanBattleSeasons'), 'ClanDetail-ClanBattleSeasons'), {
    ssr: false,
    loading: () => <LoadingPanel tone="muted" label="Loading clan battle seasons..." minHeight={240} />,
});

import ClanActivityRoster from './ClanActivityRoster';

const ClanDetail: React.FC<ClanDetailProps> = ({ clan, onSelectMember }) => {
    const { theme } = useTheme();
    const { realm } = useRealm();
    const [chartMode, setChartMode] = useState<'2d' | '3d'>('2d');

    // Pre-signal chart loading so hooks that check chartFetchesInFlight
    // during their first effect see > 0 and defer to the clan chart.
    // ClanSVG is dynamically imported, so its own increment fires late.
    // useLayoutEffect fires before all useEffects, bridging the gap:
    // increment here, then release after ClanSVG has had time to mount.
    const preSignalReleasedRef = useRef(false);
    useLayoutEffect(() => {
        incrementChartFetches();
        preSignalReleasedRef.current = false;
    }, []);
    useEffect(() => {
        // Release pre-signal after a tick — ClanSVG will have called
        // incrementChartFetches in its own useEffect by now.
        const id = requestAnimationFrame(() => {
            preSignalReleasedRef.current = true;
            decrementChartFetches();
        });
        return () => {
            cancelAnimationFrame(id);
            if (!preSignalReleasedRef.current) {
                decrementChartFetches();
            }
        };
    }, []);

    const { members, loading: membersLoading, error: membersError } = useClanMembers(clan.clan_id);
    const { data: memberTiers, loading: tiersLoading } = useClanMemberTiers(clan.clan_id);

    // Determine if 3D is available: >= 50% of members have KDR
    const kdrCoverage = memberTiers.length > 0
        ? memberTiers.filter((m) => m.kdr != null).length / memberTiers.length
        : 0;
    const is3DAvailable = kdrCoverage >= 0.5 && !tiersLoading;

    return (
        <div className="bg-[var(--bg-page)] p-6">
            <div className="mb-3 pb-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <h1 className="text-3xl font-semibold tracking-tight text-[var(--text-primary)]">
                        [{clan.tag}] {clan.name}
                    </h1>
                </div>
                <p className="mt-1 text-sm text-[var(--text-secondary)]">
                    {clan.members_count} members
                </p>
            </div>

            {/* Body content fills the site column (layout.tsx owns the width). */}
            <div>
            {/* 2D/3D toggle — desktop only */}
            <div className="hidden md:flex items-center gap-1 mb-3">
                <div className="inline-flex rounded-md border border-[var(--border)] text-xs font-medium">
                    <button
                        type="button"
                        onClick={() => { if (chartMode !== '2d') { setChartMode('2d'); trackEvent('clan-chart-2d', { realm }); } }}
                        className={`px-3 py-1 rounded-l-md transition-colors ${chartMode === '2d'
                                ? 'bg-[var(--accent-mid)] text-white'
                                : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]'
                            }`}
                    >
                        2D
                    </button>
                    <button
                        type="button"
                        onClick={() => { if (is3DAvailable && chartMode !== '3d') { setChartMode('3d'); trackEvent('clan-chart-3d', { realm }); } }}
                        disabled={!is3DAvailable}
                        title={!is3DAvailable ? 'KDR data not yet available' : 'View 3D scatter with KDR'}
                        className={`px-3 py-1 rounded-r-md transition-colors ${chartMode === '3d'
                                ? 'bg-[var(--accent-mid)] text-white'
                                : is3DAvailable
                                    ? 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]'
                                    : 'text-[var(--text-secondary)] opacity-40 cursor-not-allowed'
                            }`}
                    >
                        3D
                    </button>
                </div>
                {chartMode === '3d' && (
                    <span className="text-xs text-[var(--text-secondary)] ml-2">
                        Drag to rotate · Z-axis: KDR
                    </span>
                )}
                {!is3DAvailable && !tiersLoading && memberTiers.length > 0 && (
                    <span className="text-xs text-[var(--text-secondary)] ml-2">
                        KDR data available for {Math.round(kdrCoverage * 100)}% of members
                    </span>
                )}
            </div>

            {/* Pull the chart left by the y-axis margin so the axis sits flush
                with the body's left edge (full-bleed) instead of looking indented. */}
            <div className="mt-4 md:-ml-[38px]">
                {chartMode === '2d' ? (
                    <ClanSVG clanId={clan.clan_id} onSelectMember={onSelectMember} svgWidth={850} svgHeight={440} membersData={members} theme={theme} />
                ) : (
                    <Clan3DSVG clanId={clan.clan_id} onSelectMember={onSelectMember} svgWidth={850} svgHeight={480} membersData={members} memberTiers={memberTiers} theme={theme} />
                )}
            </div>

            <DeferredSection
                className="mt-6"
                minHeight={96}
                placeholder={<LoadingPanel tone="muted" label="Preparing clan members..." minHeight={96} />}
            >
                <div>
                    <ClanActivityRoster members={members} loading={membersLoading} error={membersError} source="clan" />
                </div>
            </DeferredSection>

            <DeferredSection
                className="mt-8"
                minHeight={240}
                placeholder={<LoadingPanel tone="muted" label="Preparing clan battle seasons..." minHeight={240} />}
            >
                <div>
                    <ClanBattleSeasons clanId={clan.clan_id} memberCount={clan.members_count} />
                </div>
            </DeferredSection>

            </div>
        </div>
    );
};

export default ClanDetail;
