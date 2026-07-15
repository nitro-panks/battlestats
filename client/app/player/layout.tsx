import React from 'react';

// Parent layout for /player/[playerName]. The left clan-members rail that used
// to live here (PlayerRailLayout) was removed 2026-07-15 — the main well now
// fills the site's single 850px column (see app/layout.tsx), sharing its
// content edges with the header and footer. Clan membership remains reachable
// via the clan page (/clan/[clanSlug]).
const PlayerLayout = ({ children }: { children: React.ReactNode }) => {
    return <div className="relative min-w-0 py-6 text-left">{children}</div>;
};

export default PlayerLayout;
