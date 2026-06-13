# QA Review — Heavy Client Component Audit

## Verdict

The audit is directionally correct, but the recommended next tranche is too large to execute safely as one change set.

Proceed with a narrowed first tranche:

1. move clan-plot fetch policy out of `ClanSVG`
2. move landing refresh and clan-hydration polling policy out of `PlayerSearch`

Do not attempt a full `PlayerDetail` and `PlayerSearch` file split in the same pass unless the change is broken into smaller validated slices.

## QA Findings

### 1. The audit identifies the right hotspots

The prioritization is correct:

- `PlayerDetail.tsx` and `PlayerSearch.tsx` carry too much route/controller policy
- `ClanSVG.tsx` matters more than raw size suggests because it is above the fold and currently fetches inside the draw path
- the D3 components share the same fetch-plus-render coupling smell

### 2. The proposed execution plan is too broad for one safe tranche

The runbook recommends all of the following at once:

- split `PlayerDetail.tsx`
- split `PlayerSearch.tsx`
- move fetch policy out of `ClanSVG.tsx` and `RandomsSVG.tsx`
- extract shared chart utilities

That is a valid roadmap, not a single implementation tranche. The regression surface is too wide if executed literally in one pass.

### 3. The first execution slice should target policy extraction before layout decomposition

The safest first implementation is to extract operational behavior without changing route layout semantics:

- fetch policy out of `ClanSVG.tsx`
- interval/polling policy out of `PlayerSearch.tsx`

This reduces complexity while keeping the rendered UI structure stable.

## Required QA Checks For The Execution Slice

1. `ClanSVG` should fetch clan plot data once per `clanId` change, not on every redraw caused by member activity updates.
2. Clan plot rendering should still react to `membersData` and highlighted-player changes without re-fetching the plot payload.
3. `PlayerSearch` landing auto-refresh should keep existing behavior for clan/player lists.
4. Clan hydration polling should still stop once `clan_name` becomes available.
5. `handleBack()` should still clear detail mode and reset any hydration attempt state.
6. Existing `PlayerSearch` and `PlayerDetail` tests should continue to pass.
7. No new TypeScript/editor errors in touched files.

## Review Outcome

Approved with scope reduction.

Execute only the narrowed first tranche in this pass.
