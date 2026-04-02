import React from 'react';
import type { Metadata } from 'next';
import ClanRouteView from '../../components/ClanRouteView';
import { getSiteUrl } from '../../lib/siteOrigin';


interface ClanPageProps {
    params: Promise<{
        clanSlug: string;
    }>;
    searchParams: Promise<{
        realm?: string;
    }>;
}


export async function generateMetadata({ params, searchParams }: ClanPageProps): Promise<Metadata> {
    const { clanSlug } = await params;
    const { realm } = await searchParams;
    const realmParam = realm && ['na', 'eu'].includes(realm) ? realm : 'na';
    const decoded = decodeURIComponent(clanSlug);
    const label = decoded.replace(/^\d+-/, '') || decoded;
    const url = getSiteUrl(`/clan/${clanSlug}?realm=${realmParam}`);

    return {
        title: `${label} — Clan — WoWs Battlestats`,
        description: `World of Warships clan statistics for ${label} — members, win rate, clan battles, and more.`,
        alternates: { canonical: url },
        openGraph: {
            title: `${label} — Clan — WoWs Battlestats`,
            description: `Clan statistics for ${label} on World of Warships.`,
            url,
            siteName: 'WoWs Battlestats',
            type: 'website',
        },
        twitter: {
            card: 'summary',
            title: `${label} — Clan — WoWs Battlestats`,
            description: `Clan statistics for ${label} on World of Warships.`,
        },
    };
}


const ClanPage = async ({ params }: ClanPageProps) => {
    const { clanSlug } = await params;
    return <ClanRouteView clanSlug={clanSlug} />;
};


export default ClanPage;