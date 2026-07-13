# Ranked Battle-History Relocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the barely-used Random|Ranked|All mode pill from the Activity tab's battle-history card, fix that card to Random with a "Random Battles" caption, and add a ranked-mode battle-history card (including the sparkline) to the Ranked insight tab.

**Architecture:** `BattleHistoryCard` converts its internal `mode` state into a fixed `mode` prop (`'random' | 'ranked'`; `'combined'` is dropped from the UI entirely — the backend `?mode=` API is untouched). The Activity tab renders the card with `mode="random"`; the Ranked tab gains a "Recent Ranked Battles" section rendering a second card instance with `mode="ranked"`, hidden when the player has no ranked history. Availability reporting becomes mode-aware so a ranked-only player's Activity tab darks out and the page falls back to the Ranked tab instead of Ships.

**Tech Stack:** Next.js 16 / React 18 / Tailwind, Jest + @testing-library/react. Frontend-only — zero backend changes.

## Global Constraints

- No backend changes. `/api/player/<name>/battle-history/?mode=` keeps accepting `random|ranked|combined`; only the FE stops requesting `combined`.
- Tab label stays **"Activity"** (rename to "Randoms" explicitly rejected 2026-07-13); the mode caption lives inside the card instead.
- The Umami event `battle-history-mode` stops being emitted (the control is gone). Events `battle-history-sort`, `ship-stats-open/close`, `player-history-<window>` keep their `mode` field, now sourced from the prop.
- `prefetchBattleHistory` (PlayerRouteView's parallel month/random prefetch) and the shared cacheKey dedupe contract must not change — the Activity card's first fetch must still dedupe onto it.
- Do NOT add the ranked battle-history fetch to `warmTabData`'s prefetch fan-out (doctrine: avoid new fan-out; the Ranked tab fetch fires on tab visit).
- All work happens in the worktree `WT=/home/august/code/battlestats/.claude/worktrees/battlestats-wt-ranked-history-relocation` on branch `worktree-battlestats-wt-ranked-history-relocation` (worktrees always live under `.claude/worktrees/`, never `../`). Every file path below is relative to `$WT`.
- Frontend test command: `cd $WT/client && npm test -- <file>`.

---

### Task 0: Worktree node_modules bring-up

**Files:** none (environment only)

The worktree has no `client/node_modules`. Hardlink it from the main checkout (hardlink, NOT symlink — Next.js resolves symlinked module trees incorrectly; this is the established FE visual-verify recipe):

- [ ] **Step 1: Hardlink node_modules into the worktree**

```bash
cp -al /home/august/code/battlestats/client/node_modules \
      /home/august/code/battlestats/.claude/worktrees/battlestats-wt-ranked-history-relocation/client/node_modules
```

- [ ] **Step 2: Verify the test harness runs**

```bash
cd /home/august/code/battlestats/.claude/worktrees/battlestats-wt-ranked-history-relocation/client \
  && npm test -- app/components/__tests__/BattleHistoryCard.test.tsx
```

Expected: PASS (pre-change baseline is green).

---

### Task 1: BattleHistoryCard — fixed-mode conversion

**Files:**
- Modify: `client/app/components/BattleHistoryCard.tsx`
- Test: `client/app/components/__tests__/BattleHistoryCard.test.tsx`

**Interfaces:**
- Consumes: nothing new.
- Produces (Task 2 relies on these exact signatures):
  - `BattleHistoryCardProps.mode?: 'random' | 'ranked'` (default `'random'`)
  - `battleHistoryIndicatesActivity(payload: BattleHistoryPayload, mode?: 'random' | 'ranked'): boolean`
  - `onAvailabilityChange?: (available: boolean, availableModes: ReadonlyArray<'random' | 'ranked'>) => void`

- [ ] **Step 1: Update/add the failing tests**

In `client/app/components/__tests__/BattleHistoryCard.test.tsx`:

**Delete these tests** (they cover the removed pill/auto-switch behavior):
- `'defaults to Ranked + hides Random/All when player has only ranked data'` (line ~437)
- `'renders mode pill row with three options + defaults to Random when both modes available'` (line ~492)
- `'opens on Ranked when both modes available but the default window has zero random battles'` (line ~516)
- `'clicking ranked pill refetches with mode=ranked'` (line ~558)

**Replace** `'hides mode pills when player has only random data'` (line ~425) with:

```tsx
test('never renders a mode pill row; a static caption labels the fixed mode', async () => {
    // Even a dual-mode player gets no toggle — the mode is fixed by prop now.
    mockByMode({ available_modes: ['random', 'ranked'] });
    render(<BattleHistoryCard playerName="lil_boots" realm="na" />);
    await waitFor(() => {
        expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
    });
    expect(screen.queryByRole('group', { name: /battle mode/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Ranked$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^All$/ })).not.toBeInTheDocument();
    expect(screen.getByText('Random Battles')).toBeInTheDocument();
});
```

**Add** after the `'initial fetch uses mode=random (default)'` test:

```tsx
test('mode="ranked" drives both fetches with mode=ranked and shows the static Ranked caption', async () => {
    mockByMode({ available_modes: ['random', 'ranked'] });
    render(<BattleHistoryCard playerName="lil_boots" realm="na" mode="ranked" />);
    await waitFor(() => {
        expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
    });
    expect(mainFetchCalls('ranked').length).toBeGreaterThan(0);
    expect(mainFetchCalls('random').length).toBe(0);
    expect(screen.getByText('Ranked')).toBeInTheDocument();
    expect(screen.queryByText('Random Battles')).not.toBeInTheDocument();
});
```

**Replace** `'embedded: reports availability true for a ranked-only player with zero random battles'` (line ~365) with:

```tsx
test('embedded: reports availability false + surfaces available modes for a ranked-only player', async () => {
    // Activity availability is now random-scoped: a ranked-only player darks the
    // Activity tab, and the second callback arg lets the parent fall back to Ranked.
    mockByMode({ available_modes: ['ranked'] }, {
        random: {
            totals: { ...buildPayload().totals, battles: 0 },
            by_ship: [],
            by_day: [],
        },
    });
    const onAvailabilityChange = jest.fn();
    render(
        <BattleHistoryCard
            embedded
            playerName="lil_boots"
            realm="na"
            onAvailabilityChange={onAvailabilityChange}
        />,
    );
    await waitFor(() => {
        expect(onAvailabilityChange).toHaveBeenCalledWith(false, ['ranked']);
    });
});
```

**Update** `'labels the ranked header with the season name when provided'` (line ~465): drive it with the prop instead of a pill click — render `<BattleHistoryCard playerName="lil_boots" realm="na" mode="ranked" />` against `mockByMode({ available_modes: ['ranked'], ranked_season_name: 'Season 29' }, ...)` and assert `screen.getByText('Season 29')`. Delete any `fireEvent.click` on a Ranked pill inside it.

**Update** `'polls when X-Ranked-Observation-Pending is true on a ranked-mode response'` (line ~644): reach ranked mode via the `mode="ranked"` prop instead of clicking the pill; the polling assertions stay as-is.

**Update** `'renders empty state with pill row when ranked mode has zero data'` (line ~686): rename to `'embedded: renders the empty state with the mode caption when the window has zero data'`, render embedded default-mode with a zero-battle random payload, assert `screen.getByText(/No random battles in this window/i)` and `screen.getByText('Random Battles')`, and drop all pill assertions.

**Update** the two zero-battle availability tests (~324, ~349): they now receive a second argument — change `toHaveBeenCalledWith(false)` → `toHaveBeenCalledWith(false, ['random'])` and `toHaveBeenCalledWith(true)` → `toHaveBeenCalledWith(true, ['random'])` (both use `available_modes: ['random']` payloads).

Also delete any assertions anywhere in the file that `trackEvent` was called with `'battle-history-mode'`.

- [ ] **Step 2: Run the test file to verify the new/changed tests fail**

```bash
cd $WT/client && npm test -- app/components/__tests__/BattleHistoryCard.test.tsx
```

Expected: FAIL — `mode` prop unknown/ignored, pill still renders, availability callback called with one arg.

- [ ] **Step 3: Implement the card conversion**

In `client/app/components/BattleHistoryCard.tsx`:

**(a)** Narrow the UI mode type and labels (lines 82–86). The payload type's `mode?: 'random' | 'ranked' | 'combined'` field stays (API back-compat):

```tsx
export type BattleHistoryMode = 'random' | 'ranked';
const MODE_LABEL: Record<BattleHistoryMode, string> = {
    random: 'Random Battles', ranked: 'Ranked',
};
const MODE_TITLE: Record<BattleHistoryMode, string> = {
    random: 'Random battles only',
    ranked: 'Ranked battles only (sums across active seasons)',
};
const MODE_NOUN: Record<BattleHistoryMode, string> = {
    random: 'random', ranked: 'ranked',
};
```

Delete the old `type Mode` and `const MODES` entirely.

**(b)** Mode-aware availability helper (replaces lines 144–155):

```tsx
// Single source of truth for "does this payload light the tab that hosts this
// card?" Mode-scoped since the pill was removed (2026-07-13): the Activity tab
// (random) lights only on in-window random battles; the Ranked tab's section
// (ranked) also accepts recent ranked rows (available_modes) so a season-edge
// zero-window doesn't hide a genuinely ranked-active player.
export const battleHistoryIndicatesActivity = (
    payload: BattleHistoryPayload,
    mode: BattleHistoryMode = 'random',
): boolean => {
    const hasBattles = !!(payload.totals && payload.totals.battles > 0);
    if (mode === 'ranked') {
        return hasBattles || (payload.available_modes ?? []).includes('ranked');
    }
    return hasBattles;
};
```

**(c)** Props (interface `BattleHistoryCardProps`): add `mode?: BattleHistoryMode;` with a doc comment ("Fixed battle mode for this instance — the card no longer switches modes itself"), and change the availability callback to:

```tsx
    onAvailabilityChange?: (
        available: boolean,
        availableModes: ReadonlyArray<'random' | 'ranked'>,
    ) => void;
```

**(d)** Component body:
- Add `mode = 'random',` to the destructured props (keep the name `mode` — every downstream reference, fetch dep array, and trackEvent payload keeps working unchanged).
- Delete `const [mode, setMode] = useState<Mode>('random');` and `const [userPickedMode, setUserPickedMode] = useState(false);` (lines 582–583).
- Delete the whole auto-mode-selection block: the comment + `initialModeResolvedRef` + its `useEffect` (lines 688–725), and the `initialModeResolvedRef.current = false;` reset line inside the `[playerName, realm]` effect (line 738) — keep that effect for the availability latch reset.
- Availability report effect (line ~741): error branch becomes `onAvailabilityChange(false, []);` and the success branch becomes:

```tsx
        onAvailabilityChange(
            battleHistoryIndicatesActivity(payload, mode),
            payload.available_modes ?? ['random'],
        );
```

Add `mode` to that effect's dependency array.
- Delete the `availableModes`/`hasRandom`/`hasRanked`/`visibleModes` derivation block (lines 842–853).
- Standalone null-collapse check (lines 862–867): drop the mode clauses —

```tsx
    if (!embedded && (
        !hasBattles
        && window === 'month' && !userPickedWindow
    )) {
        return null;
    }
```

- Replace BOTH the single-mode static label (lines 962–976) and the pill row (lines 977–1010) with one always-on caption chip:

```tsx
                {/* Static caption naming the card's fixed mode — Random Battles
                    on the Activity tab, Ranked on the Ranked tab. Replaced the
                    Random|Ranked|All pill (removed 2026-07-13: 35 sessions/90d
                    ever touched it; ranked history moved to the Ranked tab). */}
                <span
                    className="ml-auto rounded bg-[var(--accent-mid)] px-2 py-0.5 text-xs font-semibold text-[var(--bg-card)]"
                    title={MODE_TITLE[mode]}
                >
                    {MODE_LABEL[mode]}
                </span>
```

- Empty-state copy (line ~1016): `No {MODE_NOUN[mode]} battles in this window.`
- Embedded loading skeleton text (line ~835): `Loading activity…` → `Loading battles…` (the card now also hosts the Ranked tab).
- The pill's `trackEvent('battle-history-mode', ...)` disappears with the pill; make sure no other reference to that event name remains (`grep -rn "battle-history-mode" client/app` must return nothing).

- [ ] **Step 4: Run the test file to verify it passes**

```bash
cd $WT/client && npm test -- app/components/__tests__/BattleHistoryCard.test.tsx
```

Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add client/app/components/BattleHistoryCard.tsx client/app/components/__tests__/BattleHistoryCard.test.tsx
git commit -m "feat(battle-history): fix card to a single mode prop, drop the Random|Ranked|All pill"
```

---

### Task 2: PlayerDetailInsightsTabs — Random-only Activity + ranked history on the Ranked tab

**Files:**
- Modify: `client/app/components/PlayerDetailInsightsTabs.tsx`
- Test: `client/app/components/__tests__/PlayerDetailInsightsTabs.test.tsx`

**Interfaces:**
- Consumes from Task 1: `mode` prop, two-arg `onAvailabilityChange`, `battleHistoryIndicatesActivity(payload, mode)`.
- Produces: no new exports.

- [ ] **Step 1: Write the failing tests**

Add to `client/app/components/__tests__/PlayerDetailInsightsTabs.test.tsx` (reuse the file's existing prop set from the `'defaults to the Activity tab'` test — `playerId={101} playerName="TestCaptain" pvpRatio={55} pvpSurvivalRate={40} pvpBattles={800} hasKnownRankedGames hasClan efficiencyRows={[]}`):

```tsx
    it('falls back to the Ranked tab (not Ships) when the player is ranked-only', async () => {
        mockFetchSharedJson.mockImplementation((url) => {
            if (url.includes('/api/fetch/player_correlation/tier_type/')) {
                return new Promise(() => { });
            }
            if (url.includes('/battle-history/')) {
                return Promise.resolve({
                    data: {
                        as_of: '2026-06-06T00:00:00Z',
                        available_modes: ['ranked'],
                        totals: {
                            battles: 0, wins: 0, losses: 0, win_rate: 0,
                            damage: 0, avg_damage: 0, frags: 0, xp: 0,
                            planes_killed: 0, survived_battles: 0, survival_rate: 0,
                        },
                        by_ship: [],
                        by_day: [],
                    },
                    headers: {},
                });
            }
            return Promise.resolve({ data: [], headers: {} });
        });
        render(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="TestCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
            />,
        );
        await waitFor(() => {
            expect(screen.getByRole('tab', { name: 'Ranked' })).toHaveAttribute('aria-selected', 'true');
        });
        expect(screen.getByRole('tab', { name: 'Activity' })).toBeDisabled();
    });

    it('renders the Recent Ranked Battles history card on the Ranked tab', async () => {
        render(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="TestCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
            />,
        );
        fireEvent.click(screen.getByRole('tab', { name: 'Ranked' }));
        await waitFor(() => {
            expect(screen.getByText('Recent Ranked Battles')).toBeInTheDocument();
        });
        // The battle-history card itself is mounted inside the section (the
        // default beforeEach mock answers every battle-history URL with battles).
        await waitFor(() => {
            expect(screen.getByTestId('battle-history-card')).toBeInTheDocument();
        });
    });

    it('hides Recent Ranked Battles when the player has no ranked history', async () => {
        mockFetchSharedJson.mockImplementation((url) => {
            if (url.includes('/api/fetch/player_correlation/tier_type/')) {
                return new Promise(() => { });
            }
            if (url.includes('/battle-history/')) {
                const rankedRequest = url.includes('mode=ranked');
                return Promise.resolve({
                    data: {
                        as_of: '2026-06-06T00:00:00Z',
                        available_modes: ['random'],
                        totals: {
                            battles: rankedRequest ? 0 : 42,
                            wins: rankedRequest ? 0 : 24,
                            losses: rankedRequest ? 0 : 18,
                            win_rate: rankedRequest ? 0 : 57.1,
                            damage: 0, avg_damage: 0, frags: 0, xp: 0,
                            planes_killed: 0, survived_battles: 0,
                            survival_rate: 0,
                        },
                        by_ship: [],
                        by_day: [],
                    },
                    headers: {},
                });
            }
            return Promise.resolve({ data: [], headers: {} });
        });
        render(
            <PlayerDetailInsightsTabs
                playerId={101}
                playerName="TestCaptain"
                pvpRatio={55}
                pvpSurvivalRate={40}
                pvpBattles={800}
                hasKnownRankedGames
                hasClan
                efficiencyRows={[]}
            />,
        );
        fireEvent.click(screen.getByRole('tab', { name: 'Ranked' }));
        // The ranked card mounts, reports no ranked availability, and the
        // section unmounts; the rest of the Ranked tab stays.
        await waitFor(() => {
            expect(screen.queryByText('Recent Ranked Battles')).not.toBeInTheDocument();
        });
        expect(screen.getByText('Ranked Seasons')).toBeInTheDocument();
    });
```

Note: the existing `'darks out the Activity tab and falls back to Ships when there is no activity'` test must KEEP passing — its payload is `available_modes: ['random']` with zero battles, which now falls back to Ships because ranked is absent.

- [ ] **Step 2: Run the test file to verify the new tests fail**

```bash
cd $WT/client && npm test -- app/components/__tests__/PlayerDetailInsightsTabs.test.tsx
```

Expected: the three new tests FAIL ('Recent Ranked Battles' not found; fallback goes to Ships); existing tests pass.

- [ ] **Step 3: Implement the tabs changes**

In `client/app/components/PlayerDetailInsightsTabs.tsx`:

**(a)** Ranked-history availability state — next to `activityAvailable` (line ~175):

```tsx
    // null = unknown; set by the Ranked tab's battle-history card. false hides
    // the "Recent Ranked Battles" section for players with no ranked history.
    const [rankedHistoryAvailable, setRankedHistoryAvailable] = useState<boolean | null>(null);
```

Reset it in the existing player-change effect (line ~188):

```tsx
    useEffect(() => {
        setActiveTab('activity');
        setActivityAvailable(null);
        setRankedHistoryAvailable(null);
        setGlowArmed(false);
    }, [playerId]);
```

**(b)** Ranked-aware Activity fallback — replace `handleActivityAvailability` (lines 212–219):

```tsx
    const handleActivityAvailability = useCallback((
        available: boolean,
        availableModes: ReadonlyArray<'random' | 'ranked'> = [],
    ) => {
        setActivityAvailable(available);
        if (!available) {
            // Nothing random to show — dark out Activity. A ranked-only player
            // lands on Ranked (their history lives there now); otherwise Ships.
            const fallback: InsightsTabId = availableModes.includes('ranked') ? 'ranked' : 'ships';
            setActiveTab((current) => (current === 'activity' ? fallback : current));
        }
    }, []);
```

**(c)** Re-probe effect (line ~250): make the check mode-explicit and forward modes:

```tsx
                if (battleHistoryIndicatesActivity(data, 'random')) {
                    // Light up only — never switches focus to Activity.
                    handleActivityAvailability(true, data.available_modes ?? ['random']);
                }
```

**(d)** Activity panel (line ~513): add `mode="random"` to the `<BattleHistoryCard embedded ...>` instance.

**(e)** Ranked panel (line ~568): insert the ranked history section as the FIRST child of the ranked `<div>`, above the heatmap block:

```tsx
                {activeTab === 'ranked' ? (
                    <div>
                        {rankedHistoryAvailable !== false ? (
                            <div className="mb-6">
                                <SectionHeadingWithTooltip
                                    title="Recent Ranked Battles"
                                    description="Battle history scoped to Ranked — daily activity over the last 30 days, per-ship results, and totals for the player's current ranked season. The same view the Activity tab gives for Random battles."
                                    className="mb-2"
                                />
                                <BattleHistoryCard
                                    embedded
                                    mode="ranked"
                                    playerName={playerName}
                                    realm={realm}
                                    refreshNonce={refreshNonce}
                                    onAvailabilityChange={setRankedHistoryAvailable}
                                />
                            </div>
                        ) : null}
                        {!showRankedHeatmap ? (
                        ... existing heatmap/seasons content unchanged ...
```

(`setRankedHistoryAvailable` works directly as the callback — the second `availableModes` argument is simply ignored by the setter.)

**(f)** Bump the ranked tab's `minHeight` in `TAB_CONFIG` from `620` to `900` (the panel now leads with the ~500px battle-history card; the floor prevents scroll-jump while it loads).

- [ ] **Step 4: Run both test files to verify they pass**

```bash
cd $WT/client && npm test -- app/components/__tests__/PlayerDetailInsightsTabs.test.tsx app/components/__tests__/BattleHistoryCard.test.tsx
```

Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add client/app/components/PlayerDetailInsightsTabs.tsx client/app/components/__tests__/PlayerDetailInsightsTabs.test.tsx
git commit -m "feat(player): move ranked battle history to the Ranked tab, caption Activity as Random Battles"
```

---

### Task 3: Full frontend gate + docs reconciliation

**Files:**
- Modify (if grep hits): `agents/runbooks/*.md`, `agents/knowledge/*` docs that describe the mode pill or the `battle-history-mode` Umami event.

- [ ] **Step 1: Run the full frontend gate**

```bash
cd $WT/client && npm test && npm run lint && npm run build
```

Expected: tests PASS, lint clean, build succeeds. (Any PlayerSearch d3-ESM failure is a known pre-existing local artifact, not a regression from this change.)

- [ ] **Step 2: Reconcile durable docs**

```bash
cd $WT
grep -rn "battle-history-mode\|Random|Ranked|All\|mode pill" agents/ CLAUDE.md --include="*.md" --include="*.json" | grep -v archive/
```

For every live (non-archive) hit describing the pill or the `battle-history-mode` event as current behavior, update it to reflect: mode pill removed 2026-07-13, Activity card fixed to Random with a "Random Battles" caption, ranked battle history now on the Ranked tab, `combined` mode no longer reachable from the UI (API unchanged), `battle-history-mode` event retired. Likely hits: the Umami coverage runbook and any battle-history runbook. Do not edit archived runbooks.

- [ ] **Step 3: Commit docs (if any changed)**

```bash
cd $WT
git add agents/ CLAUDE.md
git commit -m "docs: reconcile battle-history mode-pill removal + ranked-tab relocation"
```

---

### Task 4: Local dev bring-up for visual check

**Files:** none (runtime only). Local dev convention: FE dev server on :3000 from the worktree, backend is the Docker gunicorn on :8888 against the cloud DB.

- [ ] **Step 1: Confirm the backend is up**

```bash
docker compose -f /home/august/code/battlestats/docker-compose.yml ps
curl -s -o /dev/null -w '%{http_code}' http://localhost:8888/api/healthcheck/ || true
```

Expected: Django service running, HTTP 200. If not running: `cd /home/august/code/battlestats && docker compose up -d`.

- [ ] **Step 2: Start the frontend dev server from the worktree**

```bash
cd $WT/client && npm run dev
```

Expected: Next.js dev server on http://localhost:3000 (run in background; leave running for the user).

- [ ] **Step 3: Verify the three surfaces render, then hand off to the user**

Load and eyeball (players with both random + ranked data give the fullest picture):

1. `http://localhost:3000/player/<dual-mode player>` — Activity tab: sparkline + table as before, **"Random Battles"** caption top-right of the card header, **no** Random|Ranked|All pill.
2. Same page, Ranked tab — **"Recent Ranked Battles"** section on top with its own sparkline, season-named header (e.g. "Season 29"), totals + per-ship table; heatmap + Ranked Seasons below.
3. `http://localhost:3000/player/<random-only player>` — Ranked tab shows **no** Recent Ranked Battles section (heatmap empty-state + seasons only).

Known dev-only quirk (do not chase): React Strict Mode double-mount can show a transient "Player not found" on soft-nav in `next dev`; production is unaffected.

Report the URLs to the user for their own visual check. Do NOT deploy, bump VERSION, or merge — the user reviews first (this is a `minor` when it ships).
