import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import DeferredSection from './DeferredSection';
import { resilientDynamicImport } from './resilientDynamicImport';
import { useClanMembers } from './useClanMembers';
import { useRealm } from '../context/RealmContext';
import { useTheme } from '../context/ThemeContext';
import ClanTierDistributionSVG from './ClanTierDistributionSVG';
import { incrementChartFetches, decrementChartFetches } from '../lib/sharedJsonFetch';

interface ClanDetailProps {
    clan: {
        clan_id: number;
        name: string;
        tag: string;
        members_count: number;
    };
    onBack: () => void;
    onSelectMember: (memberName: string) => void;
}

import LoadingPanel from './LoadingPanel';

const ClanSVG = dynamic(() => resilientDynamicImport(() => import('./ClanSVG'), 'ClanDetail-ClanSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading clan chart..." minHeight={440} />,
});

const ClanBattleSeasons = dynamic(() => resilientDynamicImport(() => import('./ClanBattleSeasons'), 'ClanDetail-ClanBattleSeasons'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading clan battle seasons..." minHeight={240} />,
});

const ClanMembers = dynamic(() => resilientDynamicImport(() => import('./ClanMembers'), 'ClanDetail-ClanMembers'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading clan members..." minHeight={96} />,
});

const ClanDetail: React.FC<ClanDetailProps> = ({ clan, onBack, onSelectMember }) => {
    const { theme } = useTheme();
    const { realm } = useRealm();
    const [shareState, setShareState] = useState<'idle' | 'copied' | 'failed'>('idle');

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

    useEffect(() => {
        if (shareState === 'idle') {
            return;
        }

        const timeoutId = window.setTimeout(() => {
            setShareState('idle');
        }, 1800);

        return () => window.clearTimeout(timeoutId);
    }, [shareState]);

    const handleShare = async () => {
        try {
            const url = new URL(window.location.href);
            if (!url.searchParams.has('realm')) {
                url.searchParams.set('realm', realm);
            }
            await navigator.clipboard.writeText(url.toString());
            setShareState('copied');
        } catch (error) {
            console.error('Failed to copy clan URL:', error);
            setShareState('failed');
        }
    };

    return (
        <div className="bg-[var(--bg-page)] p-6">
            <div className="mb-3 pb-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <h1 className="text-3xl font-semibold tracking-tight text-[var(--text-primary)]">
                        [{clan.tag}] {clan.name}
                    </h1>
                    <div className="flex items-center gap-2 self-start">
                        <button
                            type="button"
                            onClick={handleShare}
                            className="rounded-md border border-[var(--border)] px-3 py-1.5 text-sm font-medium text-[var(--text-primary)] transition-colors hover:bg-[var(--bg-hover)]"
                            aria-label="Copy shareable clan URL"
                        >
                            Share
                        </button>
                        {shareState === 'copied' ? (
                            <span className="text-xs font-medium text-[var(--accent-mid)]">Copied</span>
                        ) : null}
                        {shareState === 'failed' ? (
                            <span className="text-xs font-medium text-red-600 dark:text-red-400">Copy failed</span>
                        ) : null}
                    </div>
                </div>
                <p className="mt-1 text-sm text-[var(--text-secondary)]">
                    {clan.members_count} members
                </p>
            </div>

            <div className="mt-4">
                <ClanSVG clanId={clan.clan_id} onSelectMember={onSelectMember} svgWidth={900} svgHeight={440} membersData={members} theme={theme} />
            </div>

            <div className="mt-8 border-t border-[var(--border)] pt-4">
                <h3 className="text-lg font-bold text-[var(--accent)] mb-4">Tier Distribution</h3>
                <ClanTierDistributionSVG clanId={clan.clan_id} theme={theme} />
            </div>

            <DeferredSection
                className="mt-6 border-t border-[var(--border)] pt-4"
                minHeight={96}
                placeholder={<LoadingPanel label="Preparing clan members..." minHeight={96} />}
            >
                <div>
                    <ClanMembers members={members} loading={membersLoading} error={membersError} onSelectMember={onSelectMember} />
                </div>
            </DeferredSection>

            <DeferredSection
                className="mt-8"
                minHeight={240}
                placeholder={<LoadingPanel label="Preparing clan battle seasons..." minHeight={240} />}
            >
                <div>
                    <ClanBattleSeasons clanId={clan.clan_id} memberCount={clan.members_count} />
                </div>
            </DeferredSection>

            <button
                onClick={onBack}
                className="mt-5 rounded-md border border-[var(--border)] px-4 py-2 text-sm font-medium text-[var(--text-primary)] hover:bg-[var(--bg-hover)]"
            >
                Back
            </button>
        </div>
    );
};

export default ClanDetail;
