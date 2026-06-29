import React from 'react';
import type { Metadata } from 'next';
import ShipRouteView from '../../components/ShipRouteView';
import { getSiteUrl } from '../../lib/siteOrigin';


interface ShipPageProps {
    params: Promise<{
        shipSlug: string;
    }>;
    searchParams: Promise<{
        realm?: string;
    }>;
}


export async function generateMetadata({ params, searchParams }: ShipPageProps): Promise<Metadata> {
    const { shipSlug } = await params;
    const { realm } = await searchParams;
    const realmParam = realm && ['na', 'eu', 'asia'].includes(realm) ? realm : 'na';
    const decoded = decodeURIComponent(shipSlug);
    // Derive a display label from the slug (strip the leading "<id>-").
    const label = decoded.replace(/^\d+-?/, '').replace(/-/g, ' ').trim() || decoded;
    const titleLabel = label.replace(/\b\w/g, (c) => c.toUpperCase());
    const url = getSiteUrl(`/ship/${shipSlug}?realm=${realmParam}`);

    return {
        title: `Best ${titleLabel} players — Ship — WoWs Battlestats`,
        description: `Top World of Warships players in the ${titleLabel} over the last 30 days — win rate, battles, and standings on ${realmParam.toUpperCase()}.`,
        alternates: { canonical: url },
        openGraph: {
            title: `Best ${titleLabel} players — WoWs Battlestats`,
            description: `Top players in the ${titleLabel} on World of Warships.`,
            url,
            siteName: 'WoWs Battlestats',
            type: 'website',
        },
        twitter: {
            card: 'summary',
            title: `Best ${titleLabel} players — WoWs Battlestats`,
            description: `Top players in the ${titleLabel} on World of Warships.`,
        },
    };
}


const ShipPage = async ({ params }: ShipPageProps) => {
    const { shipSlug } = await params;
    return <ShipRouteView shipSlug={shipSlug} />;
};


export default ShipPage;
