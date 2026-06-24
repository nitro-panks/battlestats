# Architecture Diagrams

Mermaid-based diagram docs that explain how Battlestats fits together — both the **backend**
data/event flow and the **frontend** component layout. Each doc focuses on **one** area and
renders the story at multiple levels (overview → state machine → scheduling/sequence, or, for
the frontend, route → component tree → data sources). They are reference material, not
runbooks — operational procedures live in `agents/runbooks/`.

Files are prefixed by side of the stack: **`be-`** = backend (Celery/queue/data flow),
**`fe-`** = frontend (Next.js component layout).

## Frontend (`fe-`)

| Doc | Surface | What it covers |
|---|---|---|
| [fe-landing-page-components.md](fe-landing-page-components.md) | Landing page (`/`) | Component block diagram of the `/` route: root-layout chrome (header search), `PlayerSearch` discovery surface, the realm top-ships treemap → inline `ShipLeaderboard` in-place drilldown, and each component's `file:line` anchors + backing endpoint |
| [fe-player-page-components.md](fe-player-page-components.md) | Player page (`/player/[name]`) | Component block diagram of the player route: the persistent clan rail (`ClanMembers`) vs. the keyed main well (`PlayerRouteView` → `PlayerDetail` header/badge tray + `ShipTopPlayerBanner` + the 7-tab insights deck), with each tab's panel components, `file:line` anchors, and backing endpoint |

## Backend (`be-`)

| Doc | Subsystem | What it covers |
|---|---|---|
| [be-queue-data-flow.md](be-queue-data-flow.md) | Celery/queue topology | The whole request/refresh/warm pipeline: triggers → RabbitMQ → the five queue workers → WG API → Postgres → warmers → Redis → DRF read path |
| [be-player-enrichment-data-flow.md](be-player-enrichment-data-flow.md) | Player enrichment | One-time-per-player backfill: eligibility filter, write-once status state machine, self-chaining batch task, and the daily DB-only pool-maintenance / reclassify-drift loop |
| [be-observation-floor-data-flow.md](be-observation-floor-data-flow.md) | Battle-observation floor + daily-active sweep | The rolling freshness guarantee, bulk-batched capture path, the change-gates, and the gap-free daily `Snapshot` engine — plus per-realm striping and shared-pool contention |
| [be-hot-player-queue-data-flow.md](be-hot-player-queue-data-flow.md) | Hot-player engagement queue **(disabled in prod 2026-06-16)** | How durable visitor interest promotes a player into the `HotPlayer` set, the brain/capture sweeps, and the backfill seed. **Kill-switched off in prod (`HOT_PLAYERS_ENABLED=0`) — code + models retained and reversible.** Kept as reference for the dormant subsystem |

All diagrams are validated with the mermaid CLI (`mmdc`) before commit. If you edit one,
re-render to confirm it still parses — sequence-diagram note/message text must not contain
`;` (mermaid reads it as a statement separator).
