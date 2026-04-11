"use client";

import React, { startTransition, useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { buildClanPath, buildPlayerPath } from "../lib/entityRoutes";
import { useRealm } from "../context/RealmContext";
import { withRealm } from "../lib/realmParams";
import HiddenAccountIcon from "./HiddenAccountIcon";
import SearchModeToggle from "./SearchModeToggle";
import wrColor from "../lib/wrColor";

type SearchMode = "player" | "clan";

interface PlayerSuggestion {
    name: string;
    pvp_ratio: number | null;
    is_hidden?: boolean;
}

interface ClanSuggestion {
    clan_id: number;
    tag: string;
    name: string;
    members_count: number;
}

type Suggestion = PlayerSuggestion | ClanSuggestion;

const isClanSuggestion = (s: Suggestion): s is ClanSuggestion => "clan_id" in s;

const SEARCH_SUGGESTION_LIMIT = 8;
const SEARCH_DEBOUNCE_MS = 180;
const SEARCH_SUGGESTIONS_ID = "header-player-search-suggestions";

const isAbortError = (error: unknown): boolean => error instanceof DOMException && error.name === "AbortError";

const suggestionCache = new Map<string, Suggestion[]>();
const SUGGESTION_CACHE_MAX = 200;

const HeaderSearch: React.FC = () => {
    const router = useRouter();
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const { realm } = useRealm();
    const [query, setQuery] = useState("");
    const [searchMode, setSearchMode] = useState<SearchMode>("player");
    const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
    const [highlightedSuggestionIndex, setHighlightedSuggestionIndex] = useState(-1);
    const [isSuggestionListOpen, setIsSuggestionListOpen] = useState(false);
    const suggestionHideTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const navigateToSuggestion = (suggestion: Suggestion) => {
        if (isClanSuggestion(suggestion)) {
            const targetPath = buildClanPath(suggestion.clan_id, suggestion.name, realm);
            startTransition(() => { router.push(targetPath); });
        } else {
            const targetPath = buildPlayerPath(suggestion.name, realm);
            startTransition(() => { router.push(targetPath); });
        }
    };

    const submitSearch = (rawQuery: string) => {
        const trimmedQuery = rawQuery.trim();
        if (!trimmedQuery) return;

        setQuery(trimmedQuery);
        setIsSuggestionListOpen(false);
        setSuggestions([]);
        setHighlightedSuggestionIndex(-1);

        if (searchMode === "clan") {
            // Clan mode: navigate to first suggestion if available
            const match = suggestions.find((s) =>
                isClanSuggestion(s) &&
                (s.name.toLowerCase() === trimmedQuery.toLowerCase() || s.tag.toLowerCase() === trimmedQuery.toLowerCase())
            ) || (suggestions.length > 0 ? suggestions[0] : null);
            if (match) {
                navigateToSuggestion(match);
            }
            return;
        }

        const targetPath = buildPlayerPath(trimmedQuery, realm);
        startTransition(() => { router.push(targetPath); });
    };

    const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        if (highlightedSuggestionIndex >= 0 && suggestions[highlightedSuggestionIndex]) {
            navigateToSuggestion(suggestions[highlightedSuggestionIndex]);
            setIsSuggestionListOpen(false);
            setSuggestions([]);
            setHighlightedSuggestionIndex(-1);
            return;
        }
        submitSearch(query);
    };

    const handleToggle = () => {
        setSearchMode((prev) => (prev === "player" ? "clan" : "player"));
        setSuggestions([]);
        setHighlightedSuggestionIndex(-1);
        setIsSuggestionListOpen(false);
    };

    useEffect(() => {
        const currentQuery = (searchParams.get("q") || "").trim();
        if (!currentQuery) {
            setQuery("");
            return;
        }

        setQuery(currentQuery);
    }, [pathname, searchParams]);

    useEffect(() => {
        return () => {
            if (suggestionHideTimeoutRef.current) {
                clearTimeout(suggestionHideTimeoutRef.current);
            }
        };
    }, []);

    useEffect(() => {
        const trimmedQuery = query.trim().toLowerCase();
        const minLength = searchMode === "clan" ? 2 : 3;
        if (trimmedQuery.length < minLength) {
            setSuggestions([]);
            setHighlightedSuggestionIndex(-1);
            return;
        }

        const cacheKey = `${searchMode}:${realm}:${trimmedQuery}`;
        const cached = suggestionCache.get(cacheKey);
        if (cached) {
            setSuggestions(cached);
            setHighlightedSuggestionIndex(cached.length > 0 ? 0 : -1);
            return;
        }

        const endpoint = searchMode === "clan"
            ? `/api/landing/clan-suggestions?q=${encodeURIComponent(trimmedQuery)}`
            : `/api/landing/player-suggestions?q=${encodeURIComponent(trimmedQuery)}`;

        const controller = new AbortController();
        const timeoutId = setTimeout(async () => {
            try {
                const response = await fetch(withRealm(endpoint, realm), {
                    signal: controller.signal,
                });
                if (!response.ok) {
                    setSuggestions([]);
                    setHighlightedSuggestionIndex(-1);
                    return;
                }

                const payload: Suggestion[] = await response.json();
                const nextSuggestions = payload.slice(0, SEARCH_SUGGESTION_LIMIT);

                if (suggestionCache.size >= SUGGESTION_CACHE_MAX) {
                    const firstKey = suggestionCache.keys().next().value;
                    if (firstKey !== undefined) suggestionCache.delete(firstKey);
                }
                suggestionCache.set(cacheKey, nextSuggestions);

                setSuggestions(nextSuggestions);
                setHighlightedSuggestionIndex(nextSuggestions.length > 0 ? 0 : -1);
            } catch (error) {
                if (isAbortError(error)) return;
                setSuggestions([]);
                setHighlightedSuggestionIndex(-1);
            }
        }, SEARCH_DEBOUNCE_MS);

        return () => {
            clearTimeout(timeoutId);
            controller.abort();
        };
    }, [query, realm, searchMode]);

    const handleInputFocus = () => {
        if (suggestions.length > 0) {
            setIsSuggestionListOpen(true);
        }
    };

    const handleInputBlur = () => {
        suggestionHideTimeoutRef.current = setTimeout(() => {
            setIsSuggestionListOpen(false);
        }, 120);
    };

    const handleSuggestionMouseDown = (suggestion: Suggestion) => {
        if (suggestionHideTimeoutRef.current) {
            clearTimeout(suggestionHideTimeoutRef.current);
            suggestionHideTimeoutRef.current = null;
        }

        setIsSuggestionListOpen(false);
        setSuggestions([]);
        setHighlightedSuggestionIndex(-1);
        navigateToSuggestion(suggestion);
    };

    const handleInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
        if (!isSuggestionListOpen || suggestions.length === 0) return;

        if (event.key === "ArrowDown") {
            event.preventDefault();
            setHighlightedSuggestionIndex((currentIndex) => (
                currentIndex < suggestions.length - 1 ? currentIndex + 1 : 0
            ));
            return;
        }

        if (event.key === "ArrowUp") {
            event.preventDefault();
            setHighlightedSuggestionIndex((currentIndex) => (
                currentIndex > 0 ? currentIndex - 1 : suggestions.length - 1
            ));
            return;
        }

        if (event.key === "Enter" && highlightedSuggestionIndex >= 0) {
            event.preventDefault();
            navigateToSuggestion(suggestions[highlightedSuggestionIndex]);
            setIsSuggestionListOpen(false);
            setSuggestions([]);
            setHighlightedSuggestionIndex(-1);
            return;
        }

        if (event.key === "Escape") {
            setIsSuggestionListOpen(false);
            setHighlightedSuggestionIndex(-1);
        }
    };

    const placeholderText = searchMode === "clan" ? "Search Clans" : "Search Players";

    return (
        <form onSubmit={handleSubmit} className="flex w-full max-w-md items-center gap-2">
            <SearchModeToggle mode={searchMode} onToggle={handleToggle} />
            <div
                className="relative w-full"
                role="combobox"
                aria-expanded={isSuggestionListOpen && suggestions.length > 0}
                aria-controls={SEARCH_SUGGESTIONS_ID}
                aria-haspopup="listbox"
            >
                <input
                    type="text"
                    value={query}
                    onChange={(event) => {
                        setQuery(event.target.value);
                        setIsSuggestionListOpen(true);
                    }}
                    onFocus={handleInputFocus}
                    onBlur={handleInputBlur}
                    onKeyDown={handleInputKeyDown}
                    placeholder={placeholderText}
                    autoComplete="off"
                    aria-label={placeholderText}
                    aria-autocomplete="list"
                    aria-controls={SEARCH_SUGGESTIONS_ID}
                    aria-activedescendant={highlightedSuggestionIndex >= 0 ? `header-player-search-suggestion-${highlightedSuggestionIndex}` : undefined}
                    className="block w-full rounded-md border border-[var(--border)] bg-[var(--bg-page)] px-3 py-2 text-sm text-[var(--text-primary)] shadow-sm placeholder:text-[var(--text-secondary)] focus:border-[var(--accent-light)] focus:outline-none focus:ring-[var(--accent-light)]"
                />
                {isSuggestionListOpen && suggestions.length > 0 && (
                    <ul
                        id={SEARCH_SUGGESTIONS_ID}
                        className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-md border border-[var(--border)] bg-[var(--bg-page)] py-1 shadow-lg"
                        role="listbox"
                    >
                        {suggestions.map((suggestion, index) => {
                            const isHighlighted = index === highlightedSuggestionIndex;
                            if (isClanSuggestion(suggestion)) {
                                return (
                                    <li
                                        id={`header-player-search-suggestion-${index}`}
                                        key={`header-suggestion-clan-${suggestion.clan_id}`}
                                        role="option"
                                        aria-selected={isHighlighted}
                                    >
                                        <button
                                            type="button"
                                            onMouseDown={() => handleSuggestionMouseDown(suggestion)}
                                            onMouseEnter={() => setHighlightedSuggestionIndex(index)}
                                            className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm ${isHighlighted ? "bg-[var(--bg-hover)] text-[var(--accent-dark)]" : "text-[var(--accent-mid)] hover:bg-[var(--accent-faint)]"}`}
                                        >
                                            <span className="inline-flex items-center gap-2">
                                                <span className="font-semibold text-[var(--accent-mid)]">[{suggestion.tag}]</span>
                                                <span>{suggestion.name}</span>
                                            </span>
                                            <span className="text-xs text-[var(--text-secondary)]">{suggestion.members_count}m</span>
                                        </button>
                                    </li>
                                );
                            }
                            const player = suggestion as PlayerSuggestion;
                            return (
                                <li
                                    id={`header-player-search-suggestion-${index}`}
                                    key={`header-suggestion-${player.name}`}
                                    role="option"
                                    aria-selected={isHighlighted}
                                >
                                    <button
                                        type="button"
                                        onMouseDown={() => handleSuggestionMouseDown(suggestion)}
                                        onMouseEnter={() => setHighlightedSuggestionIndex(index)}
                                        className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm ${isHighlighted ? "bg-[var(--bg-hover)] text-[var(--accent-dark)]" : "text-[var(--accent-mid)] hover:bg-[var(--accent-faint)]"}`}
                                    >
                                        <span className="inline-flex items-center gap-2">
                                            <span style={{ color: wrColor(player.pvp_ratio) }} aria-hidden="true">{"\u{1F79C}"}</span>
                                            <span>{player.name}</span>
                                            {player.is_hidden ? <HiddenAccountIcon /> : null}
                                        </span>
                                    </button>
                                </li>
                            );
                        })}
                    </ul>
                )}
            </div>
            <button
                type="submit"
                className={`rounded-md px-4 py-2 text-sm font-medium text-white transition-colors ${searchMode === "clan" ? "bg-emerald-500 hover:bg-emerald-600" : "bg-[var(--accent-mid)] hover:bg-[var(--accent-dark)]"}`}
            >
                Go
            </button>
        </form>
    );
};

export default HeaderSearch;