// Client feature flags. NEXT_PUBLIC_* vars are inlined at build time; read them
// through small functions so unit tests can toggle process.env per-case and so a
// flag has exactly one source of truth.

// Player-page de-waterfall: fetch the clan-members rail immediately after the
// player detail resolves — in parallel with the chart "warmup" — instead of
// gating it behind warmup completion (the legacy provisional serialization).
// Off by default; set NEXT_PUBLIC_PLAYER_DEWATERFALL=1 to enable. Reversible at
// build time. See agents/runbooks (player fetch orchestration) + the prior
// de-waterfall incident — ship behind a visual verify.
export const isPlayerDewaterfallEnabled = (): boolean =>
    process.env.NEXT_PUBLIC_PLAYER_DEWATERFALL === '1';
