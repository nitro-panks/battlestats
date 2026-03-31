"use client";

import React, { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import ClanDetail from './ClanDetail';
import type { LandingClan } from './entityTypes';
import { buildPlayerPath, parseClanIdFromRouteSegment } from '../lib/entityRoutes';
import { trackEntityDetailView } from '../lib/visitAnalytics';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';


const LoadingPanel: React.FC<{ label: string; minHeight?: number }> = ({ label, minHeight = 220 }) => (
    <div
        className="flex animate-pulse items-center justify-center rounded-md border border-[var(--border)] bg-[var(--bg-surface)] text-sm text-[var(--accent-light)]"
        style={{ minHeight }}
    >
        {label}
    </div>
);


const readJsonOrThrow = async <T,>(response: Response, label: string): Promise<T> => {
    const contentType = response.headers.get('content-type') || '';

    if (!response.ok) {
        const body = await response.text();
        throw new Error(`${label} failed with ${response.status}: ${body.slice(0, 120)}`);
    }

    if (!contentType.toLowerCase().includes('application/json')) {
        const body = await response.text();
        throw new Error(`${label} returned non-JSON content: ${body.slice(0, 120)}`);
    }

    return response.json() as Promise<T>;
};


const normalizeClanRoutePayload = (data: Partial<LandingClan> | null | undefined, fallbackClanId: number): LandingClan | null => {
    if (!data || typeof data !== 'object') {
        return null;
    }

    const clanId = Number(data.clan_id || fallbackClanId);
    if (!Number.isInteger(clanId) || clanId <= 0) {
        return null;
    }

    return {
        clan_id: clanId,
        name: typeof data.name === 'string' ? data.name : 'Clan',
        tag: typeof data.tag === 'string' ? data.tag : '',
        members_count: Number(data.members_count || 0),
        clan_wr: data.clan_wr ?? null,
        total_battles: data.total_battles ?? null,
        active_members: data.active_members ?? null,
    };
};


interface ClanRouteViewProps {
    clanSlug: string;
}


const ClanRouteView: React.FC<ClanRouteViewProps> = ({ clanSlug }) => {
    const router = useRouter();
    const { realm } = useRealm();
    const clanId = parseClanIdFromRouteSegment(clanSlug);
    const [clanData, setClanData] = useState<LandingClan | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState('');
    const trackedClanIdRef = useRef<number | null>(null);

    useEffect(() => {
        if (clanId == null) {
            setClanData(null);
            setIsLoading(false);
            setError('Clan not found.');
            return;
        }

        let cancelled = false;

        const loadClan = async () => {
            setIsLoading(true);
            setError('');

            try {
                const response = await fetch(withRealm(`/api/clan/${clanId}`, realm));
                const data = await readJsonOrThrow<Partial<LandingClan>>(response, `Clan ${clanId}`);
                const normalizedClan = normalizeClanRoutePayload(data, clanId);
                if (!cancelled) {
                    setClanData(normalizedClan);
                    setError(normalizedClan ? '' : 'Clan not found.');
                }
            } catch (fetchError) {
                console.error('Error loading clan route:', fetchError);
                if (!cancelled) {
                    setClanData(null);
                    setError('Clan not found.');
                }
            } finally {
                if (!cancelled) {
                    setIsLoading(false);
                }
            }
        };

        void loadClan();
        return () => {
            cancelled = true;
        };
    }, [clanId, realm]);

    useEffect(() => {
        if (!clanData) {
            trackedClanIdRef.current = null;
            return;
        }

        if (trackedClanIdRef.current === clanData.clan_id) {
            return;
        }

        trackedClanIdRef.current = clanData.clan_id;
        void trackEntityDetailView({
            entityType: 'clan',
            entityId: clanData.clan_id,
            entityName: clanData.name,
            entitySlug: clanSlug,
        });
    }, [clanData, clanSlug]);

    if (isLoading) {
        return <LoadingPanel label="Loading clan profile..." minHeight={280} />;
    }

    if (!clanData) {
        return <p className="p-6 text-sm text-red-600">{error || 'Clan not found.'}</p>;
    }

    return (
        <ClanDetail
            clan={clanData}
            onBack={() => router.push('/')}
            onSelectMember={(memberName) => router.push(buildPlayerPath(memberName))}
        />
    );
};


export default ClanRouteView;