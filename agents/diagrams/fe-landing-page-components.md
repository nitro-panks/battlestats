# Landing Page — Component Block Diagram

The `/` route. A thin page that mounts one feature component (`PlayerSearch`), wrapped by
the global app chrome from the root layout. Search is **not** on the landing body itself —
it lives in the header (`HeaderSearch`) and the landing's only job is the discovery surface:
realm top-ships treemap → inline ship leaderboard with in-place drilldown.

Boxes are React components; the `file:line` annotations point at the part worth reading.

```mermaid
flowchart TD
    %% ---- Root chrome (app/layout.tsx) ----
    subgraph LAYOUT["Root layout — app/layout.tsx:53"]
        direction TB
        PROVIDERS["Context providers<br/>Theme / Realm / Degradation<br/>app/layout.tsx:53-55"]
        subgraph HEADER["Header bar — app/layout.tsx:58-64"]
            LOGO["Logo<br/>app/components/Logo.tsx"]
            THEME["ThemeToggle<br/>app/components/ThemeToggle.tsx"]
            REALM["RealmSelector<br/>app/components/RealmSelector.tsx"]
            HSEARCH["HeaderSearch<br/>app/components/HeaderSearch.tsx:46<br/>dual-mode player/clan, debounced autocomplete"]
        end
        CONNHINT["ConnectionHint<br/>app/components/ConnectionHint.tsx"]
        FOOTER["Footer (app version)<br/>app/components/Footer.tsx"]
    end

    %% ---- Landing page body ----
    PAGE["Page (/)<br/>app/page.tsx:18<br/>+ WebSite/SearchAction JSON-LD"]
    PSEARCH["PlayerSearch<br/>app/components/PlayerSearch.tsx:10<br/>?q= deep-link to buildPlayerPath redirect (line 22-31)"]

    %% ---- Discovery surface ----
    subgraph BODY["Landing discovery surface — PlayerSearch.tsx:33-50"]
        direction TB
        TREEMAP["RealmTopShipsTreemapSVG<br/>app/components/RealmTopShipsTreemapSVG.tsx:104<br/>most-played ships, random/ranked mode toggle (line 118)"]
        SHIPLB["ShipLeaderboard<br/>app/components/ShipLeaderboard.tsx:222<br/>tier+type filter, WR-pct filter, in-place ship board"]
    end

    %% ---- ShipLeaderboard internals ----
    subgraph SLB_INTERNALS["ShipLeaderboard parts"]
        direction TB
        SELECTORS["Tier/Type + WR-pct selectors<br/>tier/type state ShipLeaderboard.tsx:227-228<br/>wrPct state line 233 default 50pct, setWrPct line 267"]
        LIST["Ship list (tier x type bucket)<br/>fetch ShipLeaderboard.tsx:313-333<br/>GET /api/realm/realm/ships?tier&type&wr_pct"]
        BOARD["Per-ship player board<br/>fetch ShipLeaderboard.tsx:368-374<br/>GET /api/realm/realm/ship/id/leaderboard"]
        EGG["Easter-egg branch (no fetch)<br/>ShipLeaderboard.tsx:279-298"]
        STOOL["ShipToolLink<br/>app/components/ShipToolLink.tsx (line 691)"]
    end

    %% ---- Shared infra ----
    FETCH["fetchSharedJson<br/>app/lib/sharedJsonFetch.ts<br/>dedup + SWR cache + priority queue + retry"]

    %% ---- edges ----
    PROVIDERS --> HEADER
    LAYOUT --> PAGE
    PAGE --> PSEARCH
    PSEARCH --> TREEMAP
    PSEARCH --> SHIPLB

    TREEMAP -- "tile click to selectShip(sel) ref handoff<br/>PlayerSearch.tsx:40-42 · SLB handle line 51-52" --> SHIPLB
    TREEMAP -. "tiles board cant represent fall back to /ship/id" .-> SHIPROUTE["/ship/id page<br/>app/components/ShipRouteView.tsx"]

    SHIPLB --> SELECTORS
    SHIPLB --> LIST
    SHIPLB --> BOARD
    SHIPLB --> EGG
    BOARD --> STOOL
    LIST -- "row click to drill in place" --> BOARD

    HSEARCH -- "enter / suggestion" --> PLAYERPAGE["/player/name<br/>app/player/..."]
    PSEARCH -. "?q= redirect" .-> PLAYERPAGE
    BOARD -. "player row click" .-> PLAYERPAGE

    TREEMAP --> FETCH
    LIST --> FETCH
    BOARD --> FETCH
    HSEARCH --> FETCH
```

## Data sources (all proxied through Django, never WG directly)

| Component | Endpoint | Backing |
|---|---|---|
| `RealmTopShipsTreemapSVG` | `GET /api/realm/<realm>/top-ships?mode=` | nightly snapshot (`realm_top_ships`, warm-before-evict) |
| `ShipLeaderboard` list | `GET /api/realm/<realm>/ships?tier&type&wr_pct` | nightly `ShipTopPlayerSnapshot` + pre-warmed WR-pct buckets |
| `ShipLeaderboard` board | `GET /api/realm/<realm>/ship/<id>/leaderboard` | per-ship snapshot read-cache |
| `HeaderSearch` | `GET /api/landing/{player,clan}-suggestions?q=` | 3-tier suggest cache (client Map → Redis → `pg_trgm`) |

## Notes

- The landing body holds **no search box** — `HeaderSearch` (in the layout header) owns
  search. `PlayerSearch` is named for history; today it's the discovery surface plus a
  `?q=` deep-link redirect for the SEO `SearchAction` (PlayerSearch.tsx:22-31).
- Treemap → leaderboard handoff is in-place via an imperative ref
  (`ShipLeaderboardHandle.selectShip`, ShipLeaderboard.tsx:51-52); only tiles the inline
  board can't represent fall back to the standalone `/ship/<id>` page.
- WR-pct filter defaults to **50%** (top 50% of each ship's players by WR); buckets are
  pre-warmed nightly, with a lazy `X-Ships-WR-Pending` poll fallback (`ttlMs:0`,
  ShipLeaderboard.tsx:333). See `runbook-ship-list-wr-percentile-2026-06-23.md`.
