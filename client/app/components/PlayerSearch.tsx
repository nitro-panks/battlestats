import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import PlayerDetail from './PlayerDetail';
import ClanDetail from './ClanDetail';
import LandingClanSVG from './LandingClanSVG';

interface LandingClan {
    clan_id: number;
    name: string;
    tag: string;
    members_count: number;
    clan_wr: number | null;
    total_battles: number | null;
}

interface LandingPlayer {
    name: string;
    pvp_ratio: number | null;
    is_hidden?: boolean;
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
}

const LANDING_LIMIT = 40;
const SEARCH_SUGGESTION_LIMIT = 8;
const SEARCH_DEBOUNCE_MS = 180;
const CLAN_HYDRATION_POLL_LIMIT = 6;
const CLAN_HYDRATION_POLL_INTERVAL_MS = 2500;
const SEARCH_SUGGESTIONS_ID = 'player-search-suggestions';

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
    const [players, setPlayers] = useState<LandingPlayer[]>([]);
    const [recentPlayers, setRecentPlayers] = useState<LandingPlayer[]>([]);
    const clanHydrationAttemptsRef = useRef<Record<string, number>>({});
    const suggestionHideTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const fetchLandingData = useCallback(async () => {
        try {
            const [clansRes, playersRes, recentRes] = await Promise.all([
                fetch('http://localhost:8888/api/landing/clans/'),
                fetch('http://localhost:8888/api/landing/players/'),
                fetch('http://localhost:8888/api/landing/recent/'),
            ]);
            setClans(await clansRes.json());
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

        const timeoutId = setTimeout(async () => {
            try {
                const response = await fetch(`http://localhost:8888/api/landing/player-suggestions/?q=${encodeURIComponent(trimmedSearch)}`);
                if (!response.ok) {
                    throw new Error(`Failed to fetch suggestions for ${trimmedSearch}`);
                }

                const suggestions: LandingPlayer[] = await response.json();
                setSearchSuggestions(suggestions.slice(0, SEARCH_SUGGESTION_LIMIT));
                setHighlightedSuggestionIndex(suggestions.length > 0 ? 0 : -1);
            } catch (err) {
                console.error('Error fetching player suggestions:', err);
                setSearchSuggestions([]);
                setHighlightedSuggestionIndex(-1);
            }
        }, SEARCH_DEBOUNCE_MS);

        return () => clearTimeout(timeoutId);
    }, [searchTerm]);

    const fetchPlayerByName = async (playerName: string): Promise<PlayerData | null> => {
        const response = await axios.get<PlayerData>(`http://localhost:8888/api/player/${playerName}`);
        return response.data;
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

    const handleSelectClan = (clan: LandingClan) => {
        setSelectedClan(clan);
        setPlayerData(null);
        setError('');
    };

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

                    {clans.length > 0 && (
                        <div className="mt-8 pt-6">
                            <h3 className="text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Clans</h3>
                            <div className="mt-3">
                                <LandingClanSVG
                                    clans={clans.slice(0, LANDING_LIMIT)}
                                    onSelectClan={handleSelectClan}
                                />
                            </div>
                            <p className="mt-6 text-sm leading-7 text-[#4292c6]">
                                {clans.slice(0, LANDING_LIMIT).map((clan) => (
                                    <button
                                        key={clan.clan_id}
                                        onClick={() => handleSelectClan(clan)}
                                        className="mr-3 inline-flex items-center gap-1 font-medium text-[#084594] underline-offset-2 hover:underline hover:text-[#2171b5]"
                                        aria-label={`Show clan ${clan.name}`}
                                    >
                                        <span style={{ color: wrColor(clan.clan_wr) }} aria-hidden="true">{"\u25C8"}</span>
                                        [{clan.tag}] {clan.name}
                                    </button>
                                ))}
                            </p>
                        </div>
                    )}

                    {players.length > 0 && (
                        <div className="mt-6 border-t border-[#c6dbef] pt-6">
                            <h3 className="text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Active Players</h3>
                            <p className="mt-2 text-sm leading-7 text-[#4292c6]">
                                {players.slice(0, LANDING_LIMIT).map((player) => (
                                    player.is_hidden ? (
                                        <span
                                            key={player.name}
                                            className="mr-3 inline-flex items-center gap-1 font-medium text-[#6baed6]"
                                            aria-label={`${player.name} has hidden stats`}
                                        >
                                            <span style={{ color: wrColor(player.pvp_ratio) }} aria-hidden="true">{"\u25C6"}</span>
                                            {player.name}
                                        </span>
                                    ) : (
                                        <button
                                            key={player.name}
                                            onClick={() => handleSelectMember(player.name)}
                                            className="mr-3 inline-flex items-center gap-1 font-medium text-[#084594] underline-offset-2 hover:underline hover:text-[#2171b5]"
                                            aria-label={`Show player ${player.name}`}
                                        >
                                            <span style={{ color: wrColor(player.pvp_ratio) }} aria-hidden="true">{"\u25C6"}</span>
                                            {player.name}
                                        </button>
                                    )
                                ))}
                            </p>

                            <h3 className="mt-5 text-sm font-semibold uppercase tracking-wide text-[#2171b5]">Recently Viewed</h3>
                            {recentPlayers.length > 0 ? (
                                <p className="mt-2 text-sm leading-7 text-[#4292c6]">
                                    {recentPlayers.slice(0, LANDING_LIMIT).map((player) => (
                                        <button
                                            key={`recent-${player.name}`}
                                            onClick={() => handleSelectMember(player.name)}
                                            className="mr-3 inline-flex items-center gap-1 font-medium text-[#084594] underline-offset-2 hover:underline hover:text-[#2171b5]"
                                            aria-label={`Show recent player ${player.name}`}
                                        >
                                            <span style={{ color: wrColor(player.pvp_ratio) }} aria-hidden="true">{"\u25C6"}</span>
                                            {player.name}
                                        </button>
                                    ))}
                                </p>
                            ) : (
                                <p className="mt-2 text-sm text-[#6baed6]">No recently viewed players yet.</p>
                            )}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default PlayerSearch;