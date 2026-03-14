import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import ClanDetail from './ClanDetail';
import PlayerDetail from './PlayerDetail';
import { resilientDynamicImport } from './resilientDynamicImport';

interface LandingClan {
    clan_id: number;
    name: string;
    tag: string;
    members_count: number;
    clan_wr: number | null;
    total_battles: number | null;
    active_members?: number | null;
}

interface LandingPlayer {
    name: string;
    pvp_ratio: number | null;
    is_hidden?: boolean;
}

interface LandingActivityAttritionMonth {
    month: string;
    total_players: number;
    active_players: number;
    cooling_players: number;
    dormant_players: number;
    active_share: number;
}

interface LandingActivityAttritionSummary {
    latest_month: string;
    population_signal: 'growing' | 'stable' | 'shrinking';
    signal_delta_pct: number | null;
    recent_active_avg: number;
    prior_active_avg: number;
    recent_new_avg: number;
    prior_new_avg: number;
    months_compared: number;
}

interface LandingActivityAttritionData {
    metric: 'landing_activity_attrition';
    label: string;
    x_label: string;
    y_label: string;
    tracked_population: number;
    months: LandingActivityAttritionMonth[];
    summary: LandingActivityAttritionSummary;
}

const wrColor = (r: number | null): string => {
    if (r == null) return '#c6dbef';
    if (r > 65) return '#810c9e';
    if (r >= 60) return '#D042F3';
    if (r >= 56) return '#3182bd';
    if (r >= 54) return '#74c476';
    if (r >= 52) return '#a1d99b';
    if (r >= 50) return '#fed976';
    if (r >= 45) return '#fd8d3c';
    return '#a50f15';
};

interface PlayerData {
    id: number;
    name: string;
    player_id: number;
    kill_ratio: number | null;
    player_score: number | null;
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
    verdict: string | null;
}

const LoadingPanel: React.FC<{ label: string; minHeight?: number }> = ({ label, minHeight = 220 }) => (
    <div
        className="flex animate-pulse items-center justify-center rounded-md border border-[#dbe9f6] bg-[#f7fbff] text-sm text-[#6baed6]"
        style={{ minHeight }}
    >
        {label}
    </div>
);

const ClanTagGrid: React.FC<{
    clans: LandingClan[];
    onSelectClan: (clan: LandingClan) => void;
    ariaLabelPrefix: string;
}> = ({ clans, onSelectClan, ariaLabelPrefix }) => (
    <div
        className="mt-4 grid gap-x-4 gap-y-2 text-sm"
        style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(5.25rem, 1fr))' }}
    >
        {clans.map((clan) => (
            <button
                key={`${ariaLabelPrefix}-${clan.clan_id}`}
                type="button"
                onClick={() => onSelectClan(clan)}
                className="min-w-0 text-left font-medium underline-offset-2 hover:underline"
                style={{ color: wrColor(clan.clan_wr) }}
                aria-label={`${ariaLabelPrefix} clan ${clan.name}`}
            >
                [{clan.tag || '---'}]
            </button>
        ))}
    </div>
);

const PlayerNameGrid: React.FC<{
    players: LandingPlayer[];
    onSelectMember: (playerName: string) => void;
    ariaLabelPrefix: string;
}> = ({ players, onSelectMember, ariaLabelPrefix }) => (
    <div
        className="mt-4 grid max-w-[900px] gap-x-4 gap-y-2 text-sm"
        style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(9rem, 1fr))' }}
    >
        {players.map((player) => {
            const label = player.name;
            const color = wrColor(player.pvp_ratio);

            if (player.is_hidden) {
                return (
                    <span
                        key={`${ariaLabelPrefix}-${label}`}
                        className="inline-flex min-w-0 items-center gap-1 font-medium"
                        style={{ color }}
                        aria-label={`${label} has hidden stats`}
                        title={label}
                    >
                        <span style={{ color }} aria-hidden="true">{"\u25C6"}</span>
                        <span className="truncate">{label}</span>
                    </span>
                );
            }

            return (
                <button
                    key={`${ariaLabelPrefix}-${label}`}
                    type="button"
                    onClick={() => onSelectMember(label)}
                    className="inline-flex min-w-0 items-center gap-1 font-medium underline-offset-2 hover:underline"
                    style={{ color }}
                    aria-label={`${ariaLabelPrefix} player ${label}`}
                    title={label}
                >
                    <span style={{ color }} aria-hidden="true">{"\u25C6"}</span>
                    <span className="truncate">{label}</span>
                </button>
            );
        })}
    </div>
);

const LandingClanSVG = dynamic(
    () => resilientDynamicImport(() => import('./LandingClanSVG'), 'LandingClanSVG'),
    {
        ssr: false,
        loading: () => <LoadingPanel label="Loading clan landscape..." minHeight={360} />,
    },
);

const LandingActivityAttritionSVG = dynamic(
    () => resilientDynamicImport(() => import('./LandingActivityAttritionSVG'), 'LandingActivityAttritionSVG'),
    {
        ssr: false,
        loading: () => <LoadingPanel label="Loading activity and attrition..." minHeight={360} />,
    },
);

const PlayerExplorer = dynamic(() => resilientDynamicImport(() => import('./PlayerExplorer'), 'PlayerExplorer'), {
    ssr: false,
    loading: () => <LoadingPanel label="Loading player explorer..." minHeight={360} />,
});

const LANDING_LIMIT = 40;
const BEST_CLAN_MIN_TOTAL_BATTLES = 100000;
const BEST_CLAN_MIN_ACTIVE_SHARE = 0.3;
const SEARCH_SUGGESTION_LIMIT = 8;
const SEARCH_DEBOUNCE_MS = 180;
const CLAN_HYDRATION_POLL_LIMIT = 6;
const CLAN_HYDRATION_POLL_INTERVAL_MS = 2500;
const SEARCH_SUGGESTIONS_ID = 'player-search-suggestions';
const SHOW_PLAYER_EXPLORER = false;

type LandingClanMode = 'random' | 'best';

const isAbortError = (error: unknown): boolean => {
    return error instanceof DOMException && error.name === 'AbortError';
};

const PlayerSearch: React.FC = () => {
    const [searchTerm, setSearchTerm] = useState('');
    const [searchSuggestions, setSearchSuggestions] = useState<LandingPlayer[]>([]);
    const [highlightedSuggestionIndex, setHighlightedSuggestionIndex] = useState(-1);
    const [isSuggestionListOpen, setIsSuggestionListOpen] = useState(false);
    const [playerData, setPlayerData] = useState<PlayerData | null>(null);
    const [selectedClan, setSelectedClan] = useState<LandingClan | null>(null);
    const [error, setError] = useState('');
    const [isLoadingPlayer, setIsLoadingPlayer] = useState(false);
    const [clans, setClans] = useState<LandingClan[]>([]);
    const [clanMode, setClanMode] = useState<LandingClanMode>('random');
    const [recentClans, setRecentClans] = useState<LandingClan[]>([]);
    const [players, setPlayers] = useState<LandingPlayer[]>([]);
    const [recentPlayers, setRecentPlayers] = useState<LandingPlayer[]>([]);
    const [landingActivity, setLandingActivity] = useState<LandingActivityAttritionData | null>(null);
    const clanHydrationAttemptsRef = useRef<Record<string, number>>({});
    const suggestionHideTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const fetchLandingData = useCallback(async () => {
        try {
            const [activityRes, clansRes, recentClansRes, playersRes, recentRes] = await Promise.all([
                fetch('http://localhost:8888/api/landing/activity-attrition/'),
                fetch('http://localhost:8888/api/landing/clans/'),
                fetch('http://localhost:8888/api/landing/recent-clans/'),
                fetch('http://localhost:8888/api/landing/players/'),
                fetch('http://localhost:8888/api/landing/recent/'),
            ]);
            setLandingActivity(await activityRes.json());
            setClans(await clansRes.json());
            setRecentClans(await recentClansRes.json());
            setPlayers(await playersRes.json());
            setRecentPlayers(await recentRes.json());
        } catch (err) {
            console.error('Error fetching landing data:', err);
        }
    }, []);

    const handleBack = useCallback(() => {
        setPlayerData(null);
        setSelectedClan(null);
        setSearchTerm('');
        setError('');
        setIsLoadingPlayer(false);
        clanHydrationAttemptsRef.current = {};
        fetchLandingData();
    }, [fetchLandingData]);

    useEffect(() => {
        const onReset = () => handleBack();
        window.addEventListener('resetApp', onReset);
        return () => window.removeEventListener('resetApp', onReset);
    }, [handleBack]);

    useEffect(() => {
        fetchLandingData();
    }, [fetchLandingData]);

    useEffect(() => {
        if (suggestionHideTimeoutRef.current) {
            return () => {
                if (suggestionHideTimeoutRef.current) {
                    clearTimeout(suggestionHideTimeoutRef.current);
                }
            };
        }
    }, []);

    useEffect(() => {
        const trimmedSearch = searchTerm.trim();
        if (trimmedSearch.length < 2) {
            setSearchSuggestions([]);
            setHighlightedSuggestionIndex(-1);
            return;
        }

        const controller = new AbortController();

        const timeoutId = setTimeout(async () => {
            try {
                const response = await fetch(`http://localhost:8888/api/landing/player-suggestions/?q=${encodeURIComponent(trimmedSearch)}`, {
                    signal: controller.signal,
                });
                if (!response.ok) {
                    setSearchSuggestions([]);
                    setHighlightedSuggestionIndex(-1);
                    return;
                }

                const suggestions: LandingPlayer[] = await response.json();
                setSearchSuggestions(suggestions.slice(0, SEARCH_SUGGESTION_LIMIT));
                setHighlightedSuggestionIndex(suggestions.length > 0 ? 0 : -1);
            } catch (err) {
                if (isAbortError(err)) {
                    return;
                }

                setSearchSuggestions([]);
                setHighlightedSuggestionIndex(-1);
            }
        }, SEARCH_DEBOUNCE_MS);

        return () => {
            clearTimeout(timeoutId);
            controller.abort();
        };
    }, [searchTerm]);

    const fetchPlayerByName = async (playerName: string): Promise<PlayerData | null> => {
        const response = await fetch(`http://localhost:8888/api/player/${encodeURIComponent(playerName)}/`);
        if (!response.ok) {
            throw new Error(`Failed to fetch player ${playerName}`);
        }

        return response.json();
    };

    const handleSearch = async () => {
        setIsLoadingPlayer(true);
        try {
            const data = await fetchPlayerByName(searchTerm);
            setPlayerData(data);
            setError('');
            setSelectedClan(null);
            setIsSuggestionListOpen(false);
        } catch (err) {
            setError('Player not found');
            setPlayerData(null);
        } finally {
            setIsLoadingPlayer(false);
        }
    };

    const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        handleSearch();
    };

    const handleSelectClan = useCallback((clan: LandingClan) => {
        setSelectedClan(clan);
        setPlayerData(null);
        setError('');
    }, []);

    const handleSelectClanById = async (clanId: number, clanName: string) => {
        const existingClan = clans.find((clan) => clan.clan_id === clanId);
        if (existingClan) {
            handleSelectClan(existingClan);
            return;
        }

        try {
            const response = await fetch(`http://localhost:8888/api/clans/${clanId}/`);
            if (!response.ok) {
                throw new Error(`Failed to fetch clan ${clanId}`);
            }

            const data = await response.json();
            const hydratedClan: LandingClan = {
                clan_id: data.clan_id,
                name: data.name || clanName,
                tag: data.tag || '',
                members_count: data.members_count || 0,
                clan_wr: data.clan_wr ?? null,
                total_battles: data.total_battles ?? null,
            };
            handleSelectClan(hydratedClan);
        } catch (_err) {
            // Fall back to a minimal clan model so navigation still works.
            handleSelectClan({
                clan_id: clanId,
                name: clanName || 'Clan',
                tag: '',
                members_count: 0,
                clan_wr: null,
                total_battles: null,
            });
        }
    };

    const visibleLandingClans = useMemo(() => {
        if (clanMode === 'best') {
            return [...clans]
                .filter((clan) => {
                    if ((clan.total_battles ?? 0) < BEST_CLAN_MIN_TOTAL_BATTLES) {
                        return false;
                    }

                    const activeMembers = clan.active_members ?? 0;
                    return (activeMembers / Math.max(clan.members_count, 1)) >= BEST_CLAN_MIN_ACTIVE_SHARE;
                })
                .sort((left, right) => {
                    const leftWr = left.clan_wr ?? Number.NEGATIVE_INFINITY;
                    const rightWr = right.clan_wr ?? Number.NEGATIVE_INFINITY;
                    if (rightWr !== leftWr) {
                        return rightWr - leftWr;
                    }

                    const leftBattles = left.total_battles ?? Number.NEGATIVE_INFINITY;
                    const rightBattles = right.total_battles ?? Number.NEGATIVE_INFINITY;
                    if (rightBattles !== leftBattles) {
                        return rightBattles - leftBattles;
                    }

                    return left.name.localeCompare(right.name);
                })
                .slice(0, LANDING_LIMIT);
        }

        return clans.slice(0, LANDING_LIMIT);
    }, [clanMode, clans]);

    const handleSelectMember = async (memberName: string) => {
        setSearchTerm(memberName);
        setIsSuggestionListOpen(false);
        setSearchSuggestions([]);
        setHighlightedSuggestionIndex(-1);
        setIsLoadingPlayer(true);
        try {
            const data = await fetchPlayerByName(memberName);
            setPlayerData(data);
            setError('');
            setSelectedClan(null);
        } catch (err) {
            setError('Player not found');
        } finally {
            setIsLoadingPlayer(false);
        }
    };

    const handleSearchInputFocus = () => {
        if (searchSuggestions.length > 0) {
            setIsSuggestionListOpen(true);
        }
    };

    const handleSearchInputBlur = () => {
        suggestionHideTimeoutRef.current = setTimeout(() => {
            setIsSuggestionListOpen(false);
        }, 120);
    };

    const handleSuggestionMouseDown = (playerName: string) => {
        if (suggestionHideTimeoutRef.current) {
            clearTimeout(suggestionHideTimeoutRef.current);
            suggestionHideTimeoutRef.current = null;
        }
        void handleSelectMember(playerName);
    };

    const handleSearchInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
        if (!isSuggestionListOpen || searchSuggestions.length === 0) {
            return;
        }

        if (event.key === 'ArrowDown') {
            event.preventDefault();
            setHighlightedSuggestionIndex((currentIndex) => (
                currentIndex < searchSuggestions.length - 1 ? currentIndex + 1 : 0
            ));
            return;
        }

        if (event.key === 'ArrowUp') {
            event.preventDefault();
            setHighlightedSuggestionIndex((currentIndex) => (
                currentIndex > 0 ? currentIndex - 1 : searchSuggestions.length - 1
            ));
            return;
        }

        if (event.key === 'Enter' && highlightedSuggestionIndex >= 0) {
            event.preventDefault();
            void handleSelectMember(searchSuggestions[highlightedSuggestionIndex].name);
            return;
        }

        if (event.key === 'Escape') {
            setIsSuggestionListOpen(false);
            setHighlightedSuggestionIndex(-1);
        }
    };

    useEffect(() => {
        if (!playerData?.name) {
            return;
        }

        // Poll only when clan_id is present but clan_name is still missing
        // (clan record being hydrated in background). Skip for clanless players.
        const needsHydration = playerData.clan_id && !playerData.clan_name;
        if (!needsHydration) {
            return;
        }

        const playerName = playerData.name;
        const attemptsUsed = clanHydrationAttemptsRef.current[playerName] || 0;
        if (attemptsUsed >= CLAN_HYDRATION_POLL_LIMIT) {
            return;
        }

        const interval = setInterval(async () => {
            const currentAttempts = clanHydrationAttemptsRef.current[playerName] || 0;
            if (currentAttempts >= CLAN_HYDRATION_POLL_LIMIT) {
                clearInterval(interval);
                return;
            }

            clanHydrationAttemptsRef.current[playerName] = currentAttempts + 1;

            try {
                const refreshed = await fetchPlayerByName(playerName);
                if (!refreshed) {
                    return;
                }

                setPlayerData(refreshed);

                if (refreshed.clan_id && refreshed.clan_name) {
                    clearInterval(interval);
                }
            } catch (err) {
                if ((clanHydrationAttemptsRef.current[playerName] || 0) >= CLAN_HYDRATION_POLL_LIMIT) {
                    clearInterval(interval);
                }
            }
        }, CLAN_HYDRATION_POLL_INTERVAL_MS);

        return () => clearInterval(interval);
    }, [playerData?.name, playerData?.clan_id, playerData?.clan_name]);

    return (
        <div className="p-4">
            {playerData ? (
                <PlayerDetail
                    player={playerData}
                    onBack={handleBack}
                    onSelectMember={handleSelectMember}
                    onSelectClan={handleSelectClanById}
                    isLoading={isLoadingPlayer}
                />
            ) : selectedClan ? (
                <ClanDetail clan={selectedClan} onBack={handleBack} onSelectMember={handleSelectMember} />
            ) : (
                <div>
                    <form onSubmit={handleSubmit} className="space-y-2">
                        <label htmlFor="search" className="block text-sm font-medium text-[#2171b5]">Search:</label>
                        <div className="mt-1 flex flex-col gap-2 sm:flex-row sm:items-center">
                            <div
                                className="relative w-full sm:w-1/3"
                                role="combobox"
                                aria-expanded={isSuggestionListOpen && searchSuggestions.length > 0}
                                aria-controls={SEARCH_SUGGESTIONS_ID}
                                aria-haspopup="listbox"
                            >
                                <input
                                    type="text"
                                    id="search"
                                    value={searchTerm}
                                    onChange={(e) => {
                                        setSearchTerm(e.target.value);
                                        setIsSuggestionListOpen(true);
                                    }}
                                    onFocus={handleSearchInputFocus}
                                    onBlur={handleSearchInputBlur}
                                    onKeyDown={handleSearchInputKeyDown}
                                    autoComplete="off"
                                    aria-autocomplete="list"
                                    aria-controls={SEARCH_SUGGESTIONS_ID}
                                    aria-activedescendant={highlightedSuggestionIndex >= 0 ? `player-search-suggestion-${highlightedSuggestionIndex}` : undefined}
                                    className="block w-full px-3 py-2 border border-[#c6dbef] rounded-md shadow-sm focus:outline-none focus:ring-[#4292c6] focus:border-[#4292c6] sm:text-sm"
                                />
                                {isSuggestionListOpen && searchSuggestions.length > 0 && (
                                    <ul
                                        id={SEARCH_SUGGESTIONS_ID}
                                        className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-md border border-[#c6dbef] bg-white py-1 shadow-lg"
                                        role="listbox"
                                    >
                                        {searchSuggestions.map((player, index) => {
                                            const isHighlighted = index === highlightedSuggestionIndex;
                                            return (
                                                <li id={`player-search-suggestion-${index}`} key={`suggestion-${player.name}`} role="option" aria-selected={isHighlighted}>
                                                    <button
                                                        type="button"
                                                        onMouseDown={() => handleSuggestionMouseDown(player.name)}
                                                        onMouseEnter={() => setHighlightedSuggestionIndex(index)}
                                                        className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm ${isHighlighted ? 'bg-[#deebf7] text-[#084594]' : 'text-[#2171b5] hover:bg-[#eff3ff]'}`}
                                                    >
                                                        <span className="inline-flex items-center gap-2">
                                                            <span style={{ color: wrColor(player.pvp_ratio) }} aria-hidden="true">{"\u25C6"}</span>
                                                            <span>{player.name}</span>
                                                        </span>
                                                        {player.is_hidden && (
                                                            <span className="text-xs uppercase tracking-wide text-[#6baed6]">Hidden</span>
                                                        )}
                                                    </button>
                                                </li>
                                            );
                                        })}
                                    </ul>
                                )}
                            </div>
                            <button type="submit" className="px-4 py-2 bg-[#2171b5] hover:bg-[#084594] text-white rounded transition-colors">Go</button>
                        </div>
                    </form>
                    {error && <p className="mt-2 text-red-600">{error}</p>}

                    {landingActivity ? (
                        <div className="mt-8 pt-6">
                            <h3 className="text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Player Activity and Attrition</h3>
                            <div className="mt-3">
                                <LandingActivityAttritionSVG data={landingActivity} />
                            </div>
                        </div>
                    ) : null}

                    {clans.length > 0 && (
                        <div className={`${landingActivity ? 'mt-8 border-t border-[#c6dbef] pt-6' : 'mt-8 pt-6'}`}>
                            <div className="flex flex-wrap items-center gap-2">
                                <button
                                    type="button"
                                    onClick={() => setClanMode('random')}
                                    className={`inline-flex items-center rounded-md border px-3 py-1.5 text-sm font-semibold uppercase tracking-wide transition-colors ${clanMode === 'random' ? 'border-[#2171b5] bg-[#2171b5] text-white' : 'border-[#c6dbef] bg-white text-[#2171b5] hover:bg-[#eff3ff]'}`}
                                    aria-pressed={clanMode === 'random'}
                                >
                                    Random
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setClanMode('best')}
                                    className={`inline-flex items-center rounded-md border px-3 py-1.5 text-sm font-semibold uppercase tracking-wide transition-colors ${clanMode === 'best' ? 'border-[#2171b5] bg-[#2171b5] text-white' : 'border-[#c6dbef] bg-white text-[#2171b5] hover:bg-[#eff3ff]'}`}
                                    aria-pressed={clanMode === 'best'}
                                >
                                    Best
                                </button>
                            </div>
                            <div className="mt-3">
                                <LandingClanSVG
                                    clans={visibleLandingClans}
                                    heatmapClans={clans}
                                    onSelectClan={handleSelectClan}
                                />
                            </div>
                            <ClanTagGrid
                                clans={visibleLandingClans}
                                onSelectClan={handleSelectClan}
                                ariaLabelPrefix="Show"
                            />

                            <h3 className="mt-5 text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Recently Viewed Clans</h3>
                            {recentClans.length > 0 ? (
                                <ClanTagGrid
                                    clans={recentClans.slice(0, LANDING_LIMIT)}
                                    onSelectClan={handleSelectClan}
                                    ariaLabelPrefix="Show recent"
                                />
                            ) : (
                                <p className="mt-2 text-sm text-[#6baed6]">No recently viewed clans yet.</p>
                            )}
                        </div>
                    )}

                    {players.length > 0 && (
                        <div className="mt-6 border-t border-[#c6dbef] pt-6">
                            <h3 className="text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Active Players</h3>
                            <PlayerNameGrid
                                players={players.slice(0, LANDING_LIMIT)}
                                onSelectMember={handleSelectMember}
                                ariaLabelPrefix="Show"
                            />

                            <h3 className="mt-5 text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Recently Viewed</h3>
                            {recentPlayers.length > 0 ? (
                                <PlayerNameGrid
                                    players={recentPlayers.slice(0, LANDING_LIMIT)}
                                    onSelectMember={handleSelectMember}
                                    ariaLabelPrefix="Show recent"
                                />
                            ) : (
                                <p className="mt-2 text-sm text-[#6baed6]">No recently viewed players yet.</p>
                            )}
                        </div>
                    )}

                    {SHOW_PLAYER_EXPLORER ? <PlayerExplorer onSelectMember={handleSelectMember} /> : null}
                </div>
            )}
        </div>
    );
};

export default PlayerSearch;