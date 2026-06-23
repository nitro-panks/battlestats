import React from 'react';
import PlayerRailLayout from '../components/PlayerRailLayout';

// Parent layout for /player/[playerName]. It renders the left clan rail and
// hosts the per-player page (the main well) as `children`. Because the rail
// lives ABOVE the changing `[playerName]` segment, clicking another clan
// member soft-navigates only the well — the rail stays mounted (marker moves,
// no remount/re-skeleton). Decisive spike + rationale:
// agents/runbooks/runbook-player-rail-soft-nav-2026-06-23.md.
const PlayerLayout = ({ children }: { children: React.ReactNode }) => {
    return <PlayerRailLayout>{children}</PlayerRailLayout>;
};

export default PlayerLayout;
