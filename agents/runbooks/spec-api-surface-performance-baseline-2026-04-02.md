# Spec: API Surface Performance Baseline

_Captured: 2026-04-02_

_Status: Production before-state snapshot for backend memory-tuning comparison_

## Goal

Capture a reasonable live-production baseline before any further backend droplet memory tuning is deployed, so later changes can be compared against the real current runtime rather than older assumptions in docs.

This snapshot is intentionally lightweight. It records the live host memory/process state plus a small public API latency sample set.

## Environment And Method

- Target: `battlestats.online` production droplet
- Capture time: `2026-04-02`
- Host probe method:
  - `ssh root@battlestats.online`
  - `free -h`
  - `cat /proc/sys/vm/swappiness`
  - `grep '^CELERY_' /etc/battlestats-server.env`
  - `systemctl cat battlestats-celery battlestats-celery-hydration battlestats-celery-background`
  - `ps -eo pid,ppid,rss,comm,args --sort=-rss | head -n 25`
- Public latency probe method:
  - 5 direct `curl` samples per endpoint against `https://battlestats.online`
  - recorded fields: `time_total`, `time_starttransfer`, `http_code`
- Probe set:
  - `GET /api/player/lil_boots/`
  - `GET /api/landing/players/?mode=best&limit=25&realm=na`
  - `GET /api/landing/clans/?mode=best&limit=30&realm=na`
  - `GET /api/fetch/player_distribution/win_rate/?realm=na`

## Executive Summary

- The droplet is already under high memory pressure:
  - `3.8 GiB` total RAM
  - `3.6 GiB` used
  - `243 MiB` available
  - `501 MiB` swap in use
- Production is already running with `vm.swappiness=10`.
- The live Celery shape is already more aggressive than some older docs imply:
  - default queue: `-c 3`
  - hydration queue: `-c 4`
  - background queue: `-c 2`
- An unidentified `/dev/shm/f43np` process is consuming about `1.73 GiB RSS`, which dominates host memory usage more than any app worker.
- Public API steady-state latency looks acceptable for most sampled landing/chart endpoints, but tail latency on player detail and landing players is severe:
  - player detail median across 5 samples: about `8.06 s`
  - player detail worst sample: `16.46 s`
  - landing players median across 5 samples: about `0.21 s`, but one outlier hit `14.78 s`
  - landing clans and win-rate distribution stayed consistently sub-`0.33 s`

## Host Snapshot

### Memory

`free -h`:

```text
Mem:   total 3.8Gi  used 3.6Gi  free 105Mi  buff/cache 437Mi  available 243Mi
Swap:  total 2.0Gi  used 501Mi  free 1.5Gi
```

Interpretation:

- The box does not currently have meaningful free headroom.
- Swap is not just configured; it is actively absorbing pressure.
- Any deploy-side comparison after tuning must account for the fact that the current baseline is already close to the edge.

### Swappiness

Live value:

```text
10
```

This matters because older notes discussed swap activation, but the actual current production baseline already includes low-swappiness behavior.

### Live Celery unit shape

Observed systemd `ExecStart` values:

- default queue: `celery ... -Q default -c 3 --max-tasks-per-child=200`
- hydration queue: `celery ... -Q hydration -c 4 --max-tasks-per-child=200`
- background queue: `celery ... -Q background -c 2 --max-tasks-per-child=50`

Important drift note:

- `/etc/battlestats-server.env` did not expose the `CELERY_*CONCURRENCY` keys in this snapshot.
- The live queue counts are therefore currently encoded in the deployed unit files, not surfaced through env-backed tuning knobs.
- Any upcoming deploy should treat this `3/4/2` process shape as the true before-state.

### Top RSS processes

Largest observed RSS consumers:

| Process | Approx RSS | Notes |
| --- | ---: | --- |
| `/dev/shm/f43np -c /dev/shm/LoT2dH -B` | `1,810,032 KiB` | Unidentified process; by far the largest resident set |
| Celery background child | `182,500 KiB` | background queue worker child |
| `systemd-journald` | `161,532 KiB` | larger than expected, but not dominant |
| Celery background/default/hydration parents+children | `141,620-147,992 KiB` each | app worker footprint is material but not the main outlier |
| Gunicorn master/workers | `35,624-144,168 KiB` | smaller than Celery aggregate footprint |

Primary implication:

- The largest current memory consumer is not Gunicorn or Celery. Any backend tuning result will be confounded if the `/dev/shm/f43np` process remains on the box.

## Public API Latency Samples

### `GET /api/player/lil_boots/`

Samples:

- `0.390804 s`
- `16.459137 s`
- `0.315838 s`
- `9.283729 s`
- `8.064518 s`

Summary:

- median: about `8.06 s`
- min: `0.32 s`
- max: `16.46 s`

Interpretation:

- This route has severe tail variability.
- The network path is not the bottleneck; `time_starttransfer` tracks almost all of `time_total`, which points to server-side wait/work.

### `GET /api/landing/players/?mode=best&limit=25&realm=na`

Samples:

- `0.218707 s`
- `0.204300 s`
- `14.775753 s`
- `0.207647 s`
- `0.201102 s`

Summary:

- median: about `0.21 s`
- min: `0.20 s`
- max: `14.78 s`

Interpretation:

- Warm behavior is good, but there is at least one major tail-latency path still escaping the cache-first intent.

### `GET /api/landing/clans/?mode=best&limit=30&realm=na`

Samples:

- `0.177151 s`
- `0.167987 s`
- `0.196744 s`
- `0.169505 s`
- `0.167882 s`

Summary:

- median: about `0.17 s`
- min: `0.17 s`
- max: `0.20 s`

Interpretation:

- This route looked stable during the sample window.
- It is a reasonable control endpoint for after-change comparison.

### `GET /api/fetch/player_distribution/win_rate/?realm=na`

Samples:

- `0.183556 s`
- `0.328954 s`
- `0.203082 s`
- `0.209299 s`
- `0.217896 s`

Summary:

- median: about `0.21 s`
- min: `0.18 s`
- max: `0.33 s`

Interpretation:

- Population distribution caching looks healthy at the API edge during this probe.

## Comparison Guidance For The Next Pass

When remeasuring after backend memory/deploy changes, compare at least these dimensions:

1. `free -h` and swap usage before and after the services settle.
2. Celery unit concurrency and whether it changed from the live `3/4/2` baseline.
3. RSS ranking of the top 10 processes, especially whether `/dev/shm/f43np` is still present.
4. The same four public endpoints with the same 5-sample method.
5. Tail behavior, not just median latency, for player detail and landing players.

## Main Risks To Baseline Validity

- The unidentified `/dev/shm/f43np` process may dominate host-level memory results more than any application tuning.
- The sampled endpoint set is intentionally small; it is enough for a comparison snapshot, not a full API benchmark.
- Production traffic during the sample window may have influenced the long-tail responses.

## Bottom Line

The real current baseline is not an idle 4 GB app host with modest Celery settings. It is a heavily loaded 3.8 GB droplet already running low-swappiness memory policy and a `3/4/2` Celery shape, with only about `243 MiB` available RAM, active swap usage, and an additional unidentified `~1.7 GiB RSS` process competing with the app. Any claim that a new deploy materially improved memory behavior needs to beat this exact measured state.