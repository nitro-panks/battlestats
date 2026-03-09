import React from 'react';
import dynamic from 'next/dynamic';
import DeferredSection from './DeferredSection';

interface PlayerDetailProps {
    player: {
        id: number;
        name: string;
        player_id: number;
        total_battles: number;
        pvp_battles: number;
        pvp_wins: number;
        pvp_losses: number;
        pvp_ratio: number;
        pvp_survival_rate: number;
        wins_survival_rate: number | null;
        creation_date: string;
        days_since_last_battle: number;
        last_battle_date: string;
        recent_games: object;
        is_hidden: boolean;
        stats_updated_at: string;
        last_fetch: string;
        last_lookup: string | null;
        clan: number;
        clan_name: string;
        clan_tag: string | null;
        clan_id: number;
    };
    onBack: () => void;
    onSelectMember: (memberName: string) => void;
    onSelectClan: (clanId: number, clanName: string) => void;
    isLoading?: boolean;
}

const LoadingPanel: React.FC<{ label: string; minHeight?: number }> = ({ label, minHeight = 220 }) => (
    <div
        className="flex animate-pulse items-center justify-center rounded-md border border-[#dbe9f6] bg-[#f7fbff] text-sm text-[#6baed6]"
        style={{ minHeight }}
    >
        {label}
    </div>
);

const ClanSVG = dynamic(() => import('./ClanSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading clan chart..." minHeight={280} />,
});

const ClanMembers = dynamic(() => import('./ClanMembers'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading clan members..." minHeight={96} />,
});

const RandomsSVG = dynamic(() => import('./RandomsSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading top ships..." minHeight={500} />,
});

const RankedSeasons = dynamic(() => import('./RankedSeasons'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading ranked seasons..." minHeight={220} />,
});

const TierSVG = dynamic(() => import('./TierSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading tier chart..." minHeight={334} />,
});

const TypeSVG = dynamic(() => import('./TypeSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading ship type chart..." minHeight={210} />,
});

const WRDistributionSVG = dynamic(() => import('./WRDistributionSVG'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading win rate distribution..." minHeight={240} />,
});

const selectColorByWR = (winRatio: number): string => {
    if (winRatio > 65) return "#810c9e";  // super unicum
    if (winRatio >= 60) return "#D042F3";  // unicum
    if (winRatio >= 56) return "#3182bd";  // great
    if (winRatio >= 54) return "#74c476";  // very good
    if (winRatio >= 52) return "#a1d99b";  // good
    if (winRatio >= 50) return "#fed976";  // average
    if (winRatio >= 45) return "#fd8d3c";  // below average
    return "#a50f15";                       // bad
};

const PlayerDetail: React.FC<PlayerDetailProps> = ({
    player,
    onBack,
    onSelectMember,
    onSelectClan,
    isLoading = false,
}) => {
    return (
        <div className="relative overflow-hidden bg-white p-6">
            {isLoading ? (
                <div className="absolute inset-0 z-20 flex items-start justify-center bg-white/70 pt-6">
                    <div className="rounded-md border border-gray-200 bg-white px-3 py-1 text-sm font-medium text-gray-700 shadow-sm">
                        Loading player...
                    </div>
                </div>
            ) : null}
            <div className="grid grid-cols-[340px_1fr] gap-4">
                {/* First Column */}
                <div>
                    <div className="mb-4 pb-1">
                        {player.clan_id ? (
                            <button
                                type="button"
                                onClick={() => onSelectClan(player.clan_id, player.clan_name || "Clan")}
                                className="mt-1 text-xl font-semibold text-[#2171b5] underline-offset-4 hover:underline"
                                aria-label={`Open clan page for ${player.clan_name || "clan"}`}
                            >
                                {player.clan_tag ? `[${player.clan_tag}] ` : ''}{player.clan_name || 'Clan'}
                            </button>
                        ) : (
                            <h2 className="mt-1 text-xl font-semibold text-[#2171b5]">No Clan</h2>
                        )}
                    </div>
                    {player.clan_id ? (
                        <>
                            <div id="clan_plot_container" className="mb-5">
                                <ClanSVG
                                    clanId={player.clan_id}
                                    onSelectMember={onSelectMember}
                                    highlightedPlayerName={player.name}
                                    svgHeight={280}
                                />
                                <p className="mt-2 text-xs text-[#6baed6]">Pulsing ring marks {player.name} on the clan chart.</p>
                            </div>
                            <DeferredSection
                                className="pt-5"
                                minHeight={96}
                                placeholder={<LoadingPanel label="Preparing clan members..." minHeight={96} />}
                            >
                                <div id="clan_members_container">
                                    <ClanMembers clanId={player.clan_id} onSelectMember={onSelectMember} />
                                </div>
                            </DeferredSection>
                        </>
                    ) : (
                        <p className="text-sm text-gray-500">No clan data available</p>
                    )}
                </div>

                {/* Second Column */}
                <div className="min-w-0 text-left border-l border-[#c6dbef] pl-4">
                    <div className="mb-3 border-b border-[#c6dbef] pb-3">
                        <h1 className="text-3xl font-semibold tracking-tight text-[#084594]">
                            {player.name}
                        </h1>
                        <p className="mt-1 text-sm text-[#4292c6]">
                            Last played {player.days_since_last_battle} days ago
                        </p>
                    </div>

                    {player.is_hidden ? (
                        <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-3">
                            <p className="text-sm font-medium text-amber-800">
                                This player&apos;s stats are hidden.
                            </p>
                            <p className="mt-1 text-xs text-amber-700">
                                The player has set their profile to private. Detailed statistics and charts are not available.
                            </p>
                        </div>
                    ) : (
                        <>
                            <div className="mt-4 grid grid-cols-3 gap-3">
                                <div
                                    className="rounded-md bg-[#eff3ff] p-3"
                                    style={{ border: `1px solid ${selectColorByWR(player.pvp_ratio)}` }}
                                >
                                    <p className="text-xs uppercase tracking-wide text-[#4292c6]">Win Rate</p>
                                    <p className="mt-1 text-2xl font-semibold text-[#084594]">{player.pvp_ratio}%</p>
                                </div>
                                <div className="rounded-md bg-[#eff3ff] p-3">
                                    <p className="text-xs uppercase tracking-wide text-[#4292c6]">PvP Battles</p>
                                    <p className="mt-1 text-2xl font-semibold text-[#084594]">{player.pvp_battles.toLocaleString()}</p>
                                </div>
                                <div className="rounded-md bg-[#eff3ff] p-3">
                                    <p className="text-xs uppercase tracking-wide text-[#4292c6]">Survival</p>
                                    <p className="mt-1 text-2xl font-semibold text-[#084594]">{player.pvp_survival_rate}%</p>
                                </div>
                            </div>

                            <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-1 text-sm text-[#4292c6]">
                                <p>Total Battles: <span className="font-medium text-[#2171b5]">{player.total_battles.toLocaleString()}</span></p>
                                <p>PvP Wins: <span className="font-medium text-[#2171b5]">{player.pvp_wins.toLocaleString()}</span></p>
                                <p>Last Battle Date: <span className="font-medium text-[#2171b5]">{player.last_battle_date}</span></p>
                                <p>PvP Losses: <span className="font-medium text-[#2171b5]">{player.pvp_losses.toLocaleString()}</span></p>
                            </div>

                            <DeferredSection
                                className="mt-4"
                                minHeight={264}
                                placeholder={<LoadingPanel label="Preparing win rate distribution..." minHeight={264} />}
                            >
                                <div>
                                    <h3 className="text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Win Rate Distribution</h3>
                                    <p className="mb-2 text-xs text-[#6baed6]">Where this player falls among all tracked players (100+ battles).</p>
                                    <WRDistributionSVG playerWR={player.pvp_ratio} />
                                </div>
                            </DeferredSection>

                            <div className="mt-4">
                                <h3 className="text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Top Ships (Random Battles)</h3>
                                <p className="mb-2 text-xs text-[#6baed6]">Compares wins and total battles for the most-played ships.</p>
                                <RandomsSVG playerId={player.player_id} isLoading={isLoading} />
                            </div>
                            <div className="mt-4">
                                <h3 className="text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Ranked Seasons</h3>
                                <p className="mb-3 text-xs text-[#6baed6]">Historical ranked season performance, including league finish.</p>
                                <RankedSeasons playerId={player.player_id} isLoading={isLoading} />
                            </div>
                            <DeferredSection
                                className="mt-8"
                                minHeight={360}
                                placeholder={<LoadingPanel label="Preparing tier chart..." minHeight={360} />}
                            >
                                <div>
                                    <h3 className="text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Performance by Tier</h3>
                                    <p className="mb-2 text-xs text-[#6baed6]">Battle volume and win rate grouped by ship tier.</p>
                                    <TierSVG playerId={player.player_id} />
                                </div>
                            </DeferredSection>
                            <DeferredSection
                                className="mt-4"
                                minHeight={236}
                                placeholder={<LoadingPanel label="Preparing ship type chart..." minHeight={236} />}
                            >
                                <div>
                                    <h3 className="text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Performance by Ship Type</h3>
                                    <p className="mb-2 text-xs text-[#6baed6]">Battle volume and win rate across classes.</p>
                                    <TypeSVG playerId={player.player_id} />
                                </div>
                            </DeferredSection>
                        </>
                    )}
                </div>
            </div>
            <div className="mt-8 border-t border-[#c6dbef] pt-5">
                <button
                    type="button"
                    onClick={onBack}
                    className="inline-flex items-center rounded-md border border-[#2171b5] px-4 py-2 text-sm font-medium text-[#2171b5] transition-colors hover:bg-[#eff3ff]"
                    aria-label="Return to landing page"
                >
                    Back
                </button>
            </div>
        </div>
    );

};

export default PlayerDetail;
