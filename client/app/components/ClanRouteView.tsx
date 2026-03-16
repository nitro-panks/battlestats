"use client";

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import ClanDetail from './ClanDetail';
import type { LandingClan } from './entityTypes';
import { buildPlayerPath, parseClanIdFromRouteSegment } from '../lib/entityRoutes';


const LoadingPanel: React.FC<{ label: string; minHeight?: number }> = ({ label, minHeight = 220 }) => (
    <div
        className="flex animate-pulse items-center justify-center rounded-md border border-[#dbe9f6] bg-[#f7fbff] text-sm text-[#6baed6]"
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


interface ClanRouteViewProps {
    clanSlug: string;
}


const ClanRouteView: React.FC<ClanRouteViewProps> = ({ clanSlug }) => {
    const router = useRouter();
    const clanId = parseClanIdFromRouteSegment(clanSlug);
    const [clanData, setClanData] = useState<LandingClan | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState('');

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
                const response = await fetch(`http://localhost:8888/api/clans/${clanId}/`);
                const data = await readJsonOrThrow<LandingClan>(response, `Clan ${clanId}`);
                if (!cancelled) {
                    setClanData(data);
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
    }, [clanId]);

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