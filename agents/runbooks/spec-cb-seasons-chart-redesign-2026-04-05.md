# Spec: Clan Battle Seasons Chart Redesign

_Created: 2026-04-05_

## Summary

Redesign the clan battle seasons histogram on the clan detail page. Remove the activity percentage line overlay. Replace the WR-colored bars with a layered bar pattern matching the player page ships histogram (`RandomsSVG`): a grey background bar showing total games played, overlaid with a colored foreground bar showing games won, colored by WR threshold.

## Current Design

- Vertical bars showing WR percentage (y-axis is 0-100%)
- Bars colored by WR threshold (purple/blue/green/yellow/orange/red)
- Activity percentage line + dots overlaid on same y-axis
- Legend shows WR gradient swatch + activity line swatch
- X-axis: season labels (S1, S2, ... S301)

## New Design

- Vertical bars showing **games played** (y-axis is game count, not percentage)
- Each season has **two layered bars**:
  - **Background bar** (grey `colors.barBg`): total `roster_battles` for the season
  - **Foreground bar** (WR-colored): `roster_wins` for the season, colored by `roster_win_rate`
- No activity line
- X-axis: season labels (chronological, latest rightmost — already fixed)
- Y-axis: game count (auto-scaled to max `roster_battles`)

### Visual pattern (vertical version of RandomsSVG)

```
     ┌───┐
     │   │  ← grey bar (total battles)
     │┌─┐│
     ││G││  ← colored bar (wins, colored by WR)
     ││ ││
     │└─┘│
     └───┘
      S15
```

The grey bar is **wider** (full `barWidth`). The colored bar is **narrower** (inset, ~60-70% of barWidth) and layered on top, both anchored to the x-axis baseline. This matches how RandomsSVG uses `backgroundBarHeight` at ~50% of foreground height — but rotated 90 degrees for vertical bars, the equivalent is the colored bar being narrower than the grey bar.

### Bar sizing

- Grey bar: full `barWidth`, height scaled to `roster_battles`
- Colored bar: ~65% of `barWidth`, centered within grey bar, height scaled to `roster_wins`
- Both bars: rounded top corners (existing `roundedTopBar` path helper)
- Both bars anchored to x-axis baseline

### Color scheme

Reuse existing `selectColorByWR()` for the colored (wins) bar:

| WR | Color | Name |
|---|---|---|
| > 65% | `#810c9e` | Elite |
| 60-65% | `#D042F3` | Super Unicum |
| 56-60% | `#3182bd` | Unicum |
| 54-56% | `#74c476` | Very Good |
| 52-54% | `#a1d99b` | Good |
| 50-52% | `#fed976` | Above Average |
| 45-50% | `#fd8d3c` | Average |
| < 45% | `#a50f15` | Below Average |

Grey bar uses `colors.barBg` (`#dde5ed` light / `#2d333b` dark).

### Tooltip

Keep existing tooltip structure but update content:

```
Season 15
Battles: 342
Wins: 189 (55.3%)      ← WR colored
```

Replace the activity line from the tooltip. Keep the "Did not participate" message for gap-filled seasons.

### Legend

Replace the current two-item legend (WR gradient + activity line) with:

- Grey rect swatch + "Games Played"
- WR gradient rect swatch + "Games Won"

### Y-axis

Change from percentage (0-100%) to game count. Use `d3.max(rows, d => d.roster_battles)` for domain max, with ~10% headroom. Tick format: plain numbers (no `%` suffix).

## Data Flow

### Already available — no backend changes

The `ClanBattleSeasonSummarySerializer` already includes:

```python
roster_battles: int    # Total battles across all participating members
roster_wins: int       # Total wins
roster_losses: int     # Total losses
roster_win_rate: float # (wins / battles) * 100
```

The parent component `ClanBattleSeasons.tsx` already receives all these fields in its `ClanBattleSeason` interface (lines 8-21).

### Frontend interface change

`ClanBattleSeasonPoint` in `ClanBattleSeasonsSVG.tsx` needs `roster_battles` and `roster_wins` added:

```typescript
export interface ClanBattleSeasonPoint {
    season_id: number;
    season_name: string;
    season_label: string;
    start_date?: string | null;
    roster_battles: number;      // ADD — total games played
    roster_wins: number;         // ADD — games won
    participants: number;
    roster_win_rate: number;
}
```

The parent component `ClanBattleSeasons.tsx` already passes the full `seasons` array — these fields flow through automatically.

## Files to Modify

| File | Change |
|---|---|
| `client/app/components/ClanBattleSeasonsSVG.tsx` | Full chart rewrite: new interface fields, remove activity line/dots, add layered bars, update y-scale/axis/tooltip/legend |
| `client/app/components/ClanBattleSeasons.tsx` | No changes needed (already passes full data) |

No backend changes.

## Reference Implementation

`client/app/components/RandomsSVG.tsx` — the horizontal ships histogram. Key patterns to replicate:

- `colors.barBg` for grey background bars (line ~230)
- Background bar at ~50% height of foreground (in our case: ~65% width of foreground since bars are vertical)
- Foreground bar colored by `selectRandomsColorByWr()` (same thresholds as our `selectColorByWR`)
- Both bars with `rx: 3` rounded corners

## Verification

1. `cd client && npm run build` — type check + build
2. Visual: load a clan page with CB seasons data, confirm:
   - Grey bars show total battles per season
   - Colored bars show wins, colored by WR
   - No activity line or dots
   - Y-axis shows game counts, not percentages
   - Tooltips show battles + wins + WR%
   - Legend shows "Games Played" + "Games Won"
   - Dark mode contrast is acceptable
3. Check gap-filled "did not participate" seasons render correctly (no bars, tooltip still works)
