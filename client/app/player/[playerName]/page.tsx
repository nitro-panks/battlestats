import React from 'react';
import type { Metadata } from 'next';
import PlayerRouteView from '../../components/PlayerRouteView';
import { getSiteUrl } from '../../lib/siteOrigin';


interface PlayerPageProps {
    params: Promise<{
        playerName: string;
    }>;
    searchParams: Promise<{
        realm?: string;
    }>;
}


export async function generateMetadata({ params, searchParams }: PlayerPageProps): Promise<Metadata> {
    const { playerName } = await params;
    const { realm } = await searchParams;
    const realmParam = realm && ['na', 'eu'].includes(realm) ? realm : 'na';
    const decoded = decodeURIComponent(playerName);
    const url = getSiteUrl(`/player/${playerName}?realm=${realmParam}`);

    return {
        title: `${decoded} — WoWs Battlestats`,
        description: `World of Warships statistics for ${decoded} — win rate, battles, survival rate, ships, ranked, and more.`,
        alternates: { canonical: url },
        openGraph: {
            title: `${decoded} — WoWs Battlestats`,
            description: `Player statistics for ${decoded} on World of Warships.`,
            url,
            siteName: 'WoWs Battlestats',
            type: 'profile',
        },
        twitter: {
            card: 'summary',
            title: `${decoded} — WoWs Battlestats`,
            description: `Player statistics for ${decoded} on World of Warships.`,
        },
    };
}


const PlayerPage = async ({ params }: PlayerPageProps) => {
    const { playerName } = await params;
    return <PlayerRouteView playerName={playerName} />;
};


export default PlayerPage;