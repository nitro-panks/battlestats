# Data-Flow Diagrams

Mermaid-based diagram docs that explain how data and events move through Battlestats'
backend subsystems. Each doc focuses on **one** area of eventing and renders the story at
multiple levels (overview → state machine → scheduling/sequence). They are reference
material, not runbooks — operational procedures live in `agents/runbooks/`.

| Doc | Subsystem | What it covers |
|---|---|---|
| [queue-data-flow.md](queue-data-flow.md) | Celery/queue topology | The whole request/refresh/warm pipeline: triggers → RabbitMQ → the four queue workers → WG API → Postgres → warmers → Redis → DRF read path |
| [player-enrichment-data-flow.md](player-enrichment-data-flow.md) | Player enrichment | One-time-per-player backfill: eligibility filter, write-once status state machine, self-chaining batch task, and the daily DB-only pool-maintenance / reclassify-drift loop |
| [hot-player-queue-data-flow.md](hot-player-queue-data-flow.md) | Hot-player engagement queue | How durable visitor interest (not the player's own activity) promotes a player into the `HotPlayer` set, and the three sweeps (brain / capture / freshness) that guarantee daily capture + sub-second visit resolution |
| [observation-floor-data-flow.md](observation-floor-data-flow.md) | Battle-observation floor + daily-active sweep | The rolling freshness guarantee, bulk-batched capture path, the change-gates, and the gap-free daily `Snapshot` engine — plus per-realm striping and shared-pool contention |

All diagrams are validated with the mermaid CLI (`mmdc`) before commit. If you edit one,
re-render to confirm it still parses — sequence-diagram note/message text must not contain
`;` (mermaid reads it as a statement separator).
