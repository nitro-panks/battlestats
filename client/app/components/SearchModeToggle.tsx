"use client";

import React from "react";

interface SearchModeToggleProps {
    mode: "player" | "clan";
    onToggle: () => void;
}

const SearchModeToggle: React.FC<SearchModeToggleProps> = ({ mode, onToggle }) => {
    const isClan = mode === "clan";
    const tooltip = isClan ? "Search Clans" : "Search Players";

    return (
        <button
            type="button"
            role="switch"
            aria-checked={isClan}
            aria-label={tooltip}
            title={tooltip}
            onClick={onToggle}
            className="relative flex h-8 w-24 flex-shrink-0 cursor-pointer items-center rounded-full border border-[var(--border)] bg-[var(--bg-surface)] transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--accent-light)]"
        >
            <span
                className={`inline-block h-5 w-5 transform rounded-full bg-[var(--accent-mid)] shadow transition-transform ${isClan ? "translate-x-[4.25rem]" : "translate-x-1"}`}
            />
            <span className="pointer-events-none absolute inset-0 flex items-center justify-between px-2 text-[9px] font-semibold text-[var(--text-secondary)]">
                <span aria-hidden="true">Player</span>
                <span aria-hidden="true">Clan</span>
            </span>
        </button>
    );
};

export default SearchModeToggle;
