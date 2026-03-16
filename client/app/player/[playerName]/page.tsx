import React from 'react';
import PlayerRouteView from '../../components/PlayerRouteView';


interface PlayerPageProps {
    params: Promise<{
        playerName: string;
    }>;
}


const PlayerPage = async ({ params }: PlayerPageProps) => {
    const { playerName } = await params;
    return <PlayerRouteView playerName={playerName} />;
};


export default PlayerPage;