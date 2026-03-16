import React from 'react';
import ClanRouteView from '../../components/ClanRouteView';


interface ClanPageProps {
    params: Promise<{
        clanSlug: string;
    }>;
}


const ClanPage = async ({ params }: ClanPageProps) => {
    const { clanSlug } = await params;
    return <ClanRouteView clanSlug={clanSlug} />;
};


export default ClanPage;