# Enrichment Baseline — 7d window + enrich-on-view (2026-06-10)

Benchmark snapshot taken immediately after deploying the 7d intake window +
enrich-on-view, for comparison at re-evaluation. Compare a future snapshot
(same queries) against the tables below.

**Snapshot:** `2026-06-10T05:08:14 UTC` · **Release:** `20260610010153` · **Commit:** `436b5f4`
**Config:** `ENRICH_MIN_WR=0.0`, `ENRICH_MAX_INACTIVE_DAYS=7`, `ENRICH_ON_VIEW_ENABLED=1`
**Context:** drain complete (pending ~150); the **first drift reclassify at the new 7d window has not run yet** (next 08:20–09:00 UTC), so `skipped_inactive` is still at the old 365d boundary. asia clan crawl active; na/eu idle.

## Population & coverage
- Total population: **1,055,847**
- Enriched: **325,042** (30.8%)
- Pending: **150**

## enrichment_status histogram (per realm)
| status | NA | EU | ASIA | ALL |
|---|--:|--:|--:|--:|
| enriched | 104,055 | 134,265 | 86,722 | **325,042** |
| pending | 36 | 26 | 88 | 150 |
| empty | 68 | 57 | 19 | 144 |
| skipped_low_wr | 13,313 | 63,501 | 36,680 | **113,494** |
| skipped_inactive | 37,287 | 61,811 | 39,876 | **138,974** |
| skipped_low_battles | 128,808 | 214,106 | 91,536 | 434,450 |
| skipped_hidden | 8,822 | 23,069 | 11,700 | 43,591 |

## Eligible-but-not-enriched backlog (≥500 battles, visible, `battles_json` null, any WR)
| window | count |
|---|--:|
| ≤3d | 30,596 |
| ≤5d | 34,898 |
| **≤7d (active window)** | **38,802** |
| ≤30d | 53,883 |
| ≤90d | 71,590 |

## ≤7d re-fetch coverage (discovery rate)
- re-fetched ≤24h: **32,964** (85% of the ≤7d pool)
- re-fetched ≤7d: **34,544** (89%)

## Enrich-on-view firing (background worker journal, since deploy 05:02 UTC)
- received: **1** · succeeded: **1**  *(this is the JensUwe2 end-to-end verification; baseline ≈ 0)*

## What to compare at re-evaluation
Re-run the same queries and look for:
1. **`ELIGIBLE_NOT_ENRICHED ≤7d` → near 0.** The active backlog (38,802) should drain within ~1–3 daily drift cycles + enrich-on-view. This is the headline success metric.
2. **`enriched` climbs ~+38k** (the ≤7d pool) minus any that age past 7d before being caught.
3. **`skipped_inactive` grows substantially** once the 7d reclassify runs — players inactive 7–365d move here from `pending`/`skipped_low_wr` (was 138,974 at the old 365d line).
4. **`skipped_low_wr` shrinks** — active ones enriched, 7–365d ones reclassified to `skipped_inactive` (was 113,494).
5. **Enrich-on-view firing count climbs** — returning/viewed players (`journalctl -u battlestats-celery-background | grep enrich_player_on_view_task`). Confirms the on-view path is carrying real traffic, not just the test.

Capture command: the `manage.py shell` block in the 2026-06-10 session (status histogram + per-window eligible aggregate + ≤7d refetch + on-view journal counts).
