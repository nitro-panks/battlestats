"use client";

import React, { startTransition, useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { buildPlayerPath } from "../lib/entityRoutes";
import HiddenAccountIcon from "./HiddenAccountIcon";

interface HeaderSearchSuggestion {
    name: string;
    pvp_ratio: number | null;
    is_hidden?: boolean;
}

const SEARCH_SUGGESTION_LIMIT = 8;
const SEARCH_DEBOUNCE_MS = 180;
const SEARCH_SUGGESTIONS_ID = "header-player-search-suggestions";

const wrColor = (r: number | null): string => {
    if (r == null) return "#c6dbef";
    if (r > 65) return "#810c9e";
    if (r >= 60) return "#D042F3";
    if (r >= 56) return "#3182bd";
    if (r >= 54) return "#74c476";
    if (r >= 52) return "#a1d99b";
    if (r >= 50) return "#fed976";
    if (r >= 45) return "#fd8d3c";
    return "#a50f15";
};

const isAbortError = (error: unknown): boolean => error instanceof DOMException && error.name === "AbortError";

const HeaderSearch: React.FC = () => {
    const router = useRouter();
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const [query, setQuery] = useState("");
    const [suggestions, setSuggestions] = useState<HeaderSearchSuggestion[]>([]);
    const [highlightedSuggestionIndex, setHighlightedSuggestionIndex] = useState(-1);
    const [isSuggestionListOpen, setIsSuggestionListOpen] = useState(false);
    const suggestionHideTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const submitSearch = (rawQuery: string) => {
        const trimmedQuery = rawQuery.trim();
        if (!trimmedQuery) {
            return;
        }

        setQuery(trimmedQuery);
        setIsSuggestionListOpen(false);
        setSuggestions([]);
        setHighlightedSuggestionIndex(-1);

        const targetPath = buildPlayerPath(trimmedQuery);
        startTransition(() => {
            router.push(targetPath);
        });
    };

    const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        submitSearch(query);
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
        const trimmedQuery = query.trim();
        if (trimmedQuery.length < 2) {
            setSuggestions([]);
            setHighlightedSuggestionIndex(-1);
            return;
        }

        const controller = new AbortController();
        const timeoutId = setTimeout(async () => {
            try {
                const response = await fetch(`http://localhost:8888/api/landing/player-suggestions/?q=${encodeURIComponent(trimmedQuery)}`, {
                    signal: controller.signal,
                });
                if (!response.ok) {
                    setSuggestions([]);
                    setHighlightedSuggestionIndex(-1);
                    return;
                }

                const payload: HeaderSearchSuggestion[] = await response.json();
                const nextSuggestions = payload.slice(0, SEARCH_SUGGESTION_LIMIT);
                setSuggestions(nextSuggestions);
                setHighlightedSuggestionIndex(nextSuggestions.length > 0 ? 0 : -1);
            } catch (error) {
                if (isAbortError(error)) {
                    return;
                }

                setSuggestions([]);
                setHighlightedSuggestionIndex(-1);
            }
        }, SEARCH_DEBOUNCE_MS);

        return () => {
            clearTimeout(timeoutId);
            controller.abort();
        };
    }, [query]);

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

    const handleSuggestionMouseDown = (playerName: string) => {
        if (suggestionHideTimeoutRef.current) {
            clearTimeout(suggestionHideTimeoutRef.current);
            suggestionHideTimeoutRef.current = null;
        }

        submitSearch(playerName);
    };

    const handleInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
        if (!isSuggestionListOpen || suggestions.length === 0) {
            return;
        }

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
            submitSearch(suggestions[highlightedSuggestionIndex].name);
            return;
        }

        if (event.key === "Escape") {
            setIsSuggestionListOpen(false);
            setHighlightedSuggestionIndex(-1);
        }
    };

    return (
        <form onSubmit={handleSubmit} className="flex w-full max-w-md items-center gap-2">
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
                    placeholder="Search player"
                    autoComplete="off"
                    aria-label="Search player"
                    aria-autocomplete="list"
                    aria-controls={SEARCH_SUGGESTIONS_ID}
                    aria-activedescendant={highlightedSuggestionIndex >= 0 ? `header-player-search-suggestion-${highlightedSuggestionIndex}` : undefined}
                    className="block w-full rounded-md border border-[#c6dbef] px-3 py-2 text-sm shadow-sm focus:border-[#4292c6] focus:outline-none focus:ring-[#4292c6]"
                />
                {isSuggestionListOpen && suggestions.length > 0 && (
                    <ul
                        id={SEARCH_SUGGESTIONS_ID}
                        className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-md border border-[#c6dbef] bg-white py-1 shadow-lg"
                        role="listbox"
                    >
                        {suggestions.map((player, index) => {
                            const isHighlighted = index === highlightedSuggestionIndex;
                            return (
                                <li
                                    id={`header-player-search-suggestion-${index}`}
                                    key={`header-suggestion-${player.name}`}
                                    role="option"
                                    aria-selected={isHighlighted}
                                >
                                    <button
                                        type="button"
                                        onMouseDown={() => handleSuggestionMouseDown(player.name)}
                                        onMouseEnter={() => setHighlightedSuggestionIndex(index)}
                                        className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm ${isHighlighted ? "bg-[#deebf7] text-[#084594]" : "text-[#2171b5] hover:bg-[#eff3ff]"}`}
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
                className="rounded-md bg-[#2171b5] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[#084594]"
            >
                Go
            </button>
        </form>
    );
};

export default HeaderSearch;