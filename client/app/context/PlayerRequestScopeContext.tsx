'use client';

import { createContext, useContext } from 'react';

// Carries the current player page's AbortSignal. PlayerRouteView owns one
// AbortController per (playerName, realm) and aborts it when the player or realm
// changes (or the page unmounts), so EVERY fetch on the page — detail, charts,
// battle history, the live-refresh poll — is cancelled in one shot when the user
// navigates away or switches realm. Cancelled in-flight + queued requests free
// the concurrency queue immediately for the page the user actually wants.
//
// This is the seed of the Phase-C orchestrator: a single owner of the page's
// request lifetime. Consumers pass the signal straight to fetchSharedJson.
const PlayerRequestScopeContext = createContext<AbortSignal | undefined>(undefined);

export const PlayerRequestScopeProvider = PlayerRequestScopeContext.Provider;

export const usePlayerRequestSignal = (): AbortSignal | undefined =>
    useContext(PlayerRequestScopeContext);
