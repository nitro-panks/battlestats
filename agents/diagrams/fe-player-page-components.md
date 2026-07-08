# Player Page — Component Block Diagram

The `/player/[playerName]` route. Two persistent regions under the root-layout chrome: a
**clan rail** that lives in the route layout and stays mounted across a soft-nav player swap,
and a **keyed main well** (`PlayerRouteView`, remounted per player) holding the player header,
the ship-top banner, and the seven-tab insights deck. The Activity tab's `BattleHistoryCard`
hosts one nested drilldown — `ShipStats`, a per-ship combat profile toggled by a table-row
click — which is its own component, not part of the card.

Boxes are React components; `file:line` annotations point at the part worth reading. The root
chrome (header search, theme/realm selectors, footer) is detailed in
[fe-landing-page-components.md](fe-landing-page-components.md) and only stubbed here.

```mermaid
flowchart TD
    CHROME["Root layout chrome<br/>app/layout.tsx — header search / theme / realm / footer<br/>(see fe-landing-page-components.md)"]

    %% ---- Route shell ----
    LAYOUT["Route layout<br/>app/player/layout.tsx:11<br/>wraps children in PlayerRailLayout"]
    PAGE["Player page<br/>app/player/[playerName]/page.tsx:46<br/>PlayerRouteView key=playerName (remount per player)"]

    %% ---- Clan rail (stays mounted across soft-nav) ----
    subgraph RAIL["Clan rail — persistent, NOT keyed — PlayerRailLayout.tsx:46"]
        direction TB
        CLANSVG["ClanSVG (clan identity + activity bar)<br/>app/components/ClanSVG.tsx<br/>rendered PlayerRailLayout.tsx:133"]
        CLANMEMBERS["ClanMembers (roster list)<br/>app/components/ClanMembers.tsx<br/>dynamic import PlayerRailLayout.tsx:26 / rendered :151"]
        CLANHOOK["useClanMembers<br/>app/components/useClanMembers.ts:56<br/>GET /api/fetch/clan_members/clanId (X-Clan-Idle poll)"]
    end

    %% ---- Main well (keyed, remounts per player) ----
    subgraph WELL["Main well — keyed per player — PlayerRouteView.tsx:28"]
        direction TB
        ROUTEVIEW["PlayerRouteView<br/>app/components/PlayerRouteView.tsx:28<br/>GET /api/player/name/ (critical) + PlayerRequestScopeProvider :157"]

        subgraph DETAIL["PlayerDetail — app/components/PlayerDetail.tsx"]
            direction TB
            HEADER["Header identity + badge tray<br/>PlayerDetail.tsx:232-239<br/>Activity·Hidden·LeaderCrown·PveEnjoyer·Ranked·ClanBattleShield·EfficiencyRank·TopShipBadges·Twitch"]
            BANNER["ShipTopPlayerBanner (current T10 top-3)<br/>app/components/ShipTopPlayerBanner.tsx<br/>rendered PlayerDetail.tsx:338 (ship_badges)"]
            TABS["PlayerDetailInsightsTabs (7-tab deck)<br/>app/components/PlayerDetailInsightsTabs.tsx:151<br/>TAB_CONFIG :108 / panel switch :482-635"]
        end
    end

    %% ---- Tabs (each lazy-loaded, fetch on activate) ----
    subgraph TABDECK["Insights tabs — dynamic imports, fetch-on-activate"]
        direction TB
        T_ACT["Activity → BattleHistoryCard (+ ShipStats on ship-row click)<br/>GET /api/player/name/battle-history/ (BattleHistoryCard.tsx:100)<br/>totals: clustered stat bar, WR split Window·Overall·Δ (:972-1062)"]
        T_SHIPS["Ships → RandomsSVG<br/>GET /api/fetch/randoms_data/id/?all=true (RandomsSVG.tsx:325)"]
        T_PROFILE["Profile → TierTypeHeatmapSVG / TypeSVG / TierSVG<br/>GET /api/fetch/player_correlation/tier_type/id/ (Tabs:333)"]
        T_RANKED["Ranked → RankedWRBattlesHeatmapSVG + RankedSeasons<br/>GET player_correlation/ranked_wr_battles/id/ + ranked_data/id/ (Tabs:267/274)"]
        T_CB["Clan Battles → PlayerClanBattleSeasons<br/>GET /api/fetch/player_clan_battle_seasons/id/ (Tabs:286)"]
        T_EFF["Efficiency → PlayerEfficiencyBadges<br/>(from player payload, no extra fetch)"]
        T_POP["Population → WR / Battles / Score DistributionSVG<br/>(realm distributions from player payload)"]
        SHIPSTATS["ShipStats (combat-profile panel, ship-row click)<br/>app/components/ShipStats.tsx · rendered BattleHistoryCard.tsx:1067<br/>Average|Player|Delta table (Accuracy cluster = career)<br/>GET /api/player/name/ship/id/combat-stats"]
    end

    FETCH["fetchSharedJson<br/>app/lib/sharedJsonFetch.ts<br/>dedup + SWR cache + priority queue + retry"]

    %% ---- structural edges ----
    CHROME --> LAYOUT
    LAYOUT --> RAIL
    LAYOUT --> PAGE
    PAGE --> WELL

    CLANSVG -.-> CLANMEMBERS
    CLANMEMBERS --> CLANHOOK

    ROUTEVIEW --> DETAIL
    DETAIL --> HEADER
    DETAIL --> BANNER
    DETAIL --> TABS

    TABS --> T_ACT
    TABS --> T_SHIPS
    TABS --> T_PROFILE
    TABS --> T_RANKED
    TABS --> T_CB
    TABS --> T_EFF
    TABS --> T_POP
    T_ACT -- "ship-row click → combat panel" --> SHIPSTATS

    %% ---- nav + data edges ----
    CLANMEMBERS -- "member click → soft-nav swaps well only<br/>rail stays mounted (page.tsx:46 key)" --> PAGE
    CLANSVG -. "clan name → /clan/slug" .-> CLANPAGE["/clan/slug page<br/>app/clan/..."]
    HEADER -. "clan tag → /clan/slug" .-> CLANPAGE
    BANNER -. "card → /ship/id" .-> SHIPPAGE["/ship/id page"]

    ROUTEVIEW --> FETCH
    CLANHOOK --> FETCH
    T_ACT --> FETCH
    T_SHIPS --> FETCH
    T_PROFILE --> FETCH
    T_RANKED --> FETCH
    T_CB --> FETCH
    SHIPSTATS --> FETCH
```

## Tabs → panels → endpoints

| Tab (`InsightsTabId`) | Panel components | Endpoint(s) | Notes |
|---|---|---|---|
| `activity` | `BattleHistoryCard` (+ `ShipStats` on ship-row click) | `GET /api/player/<name>/battle-history/`; ship panel `…/ship/<id>/combat-stats` | default tab; day/week/month/year windows resolve to the daily layer. Totals bar is clustered/divided with a 3-tile WR split (Window · Overall · Δ, payload adds `lifetime_win_rate`/`delta_win_rate`). `ShipStats` expands below as an Average\|Player\|Delta table |
| `ships` | `RandomsSVG` | `GET /api/fetch/randoms_data/<id>/?all=true` | per-ship random-battle aggregates |
| `profile` | `TierTypeHeatmapSVG`, `TypeSVG`, `TierSVG` | `GET /api/fetch/player_correlation/tier_type/<id>/` | one payload derives all three charts |
| `ranked` | `RankedWRBattlesHeatmapSVG`, `RankedSeasons` | `…/ranked_wr_battles/<id>/` + `…/ranked_data/<id>/` | cold `ranked_data` serves `[]` + `X-Ranked-Pending` |
| `career` (Clan Battles) | `PlayerClanBattleSeasons` | `GET /api/fetch/player_clan_battle_seasons/<id>/` | request path sends `allow_remote_fetch=False`; `X-Clan-Battle-Seasons-Pending` |
| `badges` (Efficiency) | `PlayerEfficiencyBadges` | — (from the player payload) | |
| `population` | `WRDistributionSVG`, `BattlesDistributionSVG`, `PlayerScoreDistributionSVG` | — (realm distributions in the player payload) | player marker plotted against realm curves |

## Notes

- **Two mount lifetimes.** The clan rail lives in `app/player/layout.tsx` and is **not**
  keyed, so it survives a soft-nav player swap; the main well is `key={playerName}`
  (`page.tsx:46`) and remounts so per-player state (tab, scroll, sort) never bleeds across
  players. A `ClanMembers` row click swaps only the well. Full design:
  `runbook-player-fetch-orchestration-2026-06-21.md`.
- **One critical fetch, lazy tabs.** `PlayerRouteView` issues the single `critical`
  `/api/player/<name>/` fetch; the clan rail dedupes onto it (same URL → same cacheKey).
  Each tab is a `dynamic()` import that fetches on activate (with a low-priority warmup
  prefetch), so the header + Activity tab paint before the heavier charts.
- **Request scoping.** `PlayerRequestScopeProvider` (`PlayerRouteView.tsx:157`) carries one
  per-(player,realm) abort signal so a nav or realm switch cancels the whole page's
  in-flight requests.
- **Badge-dispatch is inline, on purpose.** Which classification icons the header tray
  renders, and in what order, is inlined at `PlayerDetail.tsx:263-270` (the clan-members row
  has its own order in `ClanMembers.tsx`) — the orders genuinely differ per surface.
- **Activity-tab ship drilldown.** `ShipStats` (`app/components/ShipStats.tsx`) is a separate
  component the Activity tab's `BattleHistoryCard` hosts, not part of the card. A ship-row
  click sets `selectedShip` and renders the panel between the stats rollup and the ships table
  (`BattleHistoryCard.tsx:1067`; toggle handler :743 — a second click on the same ship hides
  it). It fetches `GET /api/player/<name>/ship/<shipId>/combat-stats` and renders an
  Average|Player|Delta comparison table (v2.18.0) of the player's per-ship rates (gunnery /
  torpedo / secondary accuracy, spotting, objective play, survival; the Accuracy cluster is
  career-scoped) against the ship's **30-day population average**, with an All / Top 50% /
  Top 25% skill bracket toggle. Role-irrelevant metric clusters are omitted server-side. Origin:
  `runbook-battle-history-data-operationalization-2026-06-16.md`.
