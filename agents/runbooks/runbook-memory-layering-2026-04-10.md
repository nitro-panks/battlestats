# Agentic Memory Layering — SuperLocalMemory at the Guidance Seam

**Status:** Implemented 2026-04-10. Optional, opt-in via env var. Droplet-safe.

## Why this runbook exists

The Battlestats agentic runtime (`server/warships/agentic/`) used to ship a
`hindsight-langgraph==0.1.1` integration as its only optional memory layer.
After auditing the wiring, the integration was aspirational rather than
load-bearing:

- It was passed to `graph_builder.compile(..., store=hindsight_store)` and that
  was the only contact point — no graph node ever called `store.get()` or
  `store.put()`.
- All memory work in the 13 graph nodes goes through
  `prepare_phase0_memory_context()` (file-backed, in-node).
- It was disabled by default on the droplet
  (`BATTLESTATS_HINDSIGHT_ENABLED=0`), v0.1.1, single-vendor, hosted-only, and
  pay-per-call to `api.hindsight.vectorize.io`.

Removing it changed no production behavior. In its place we added
**SuperLocalMemory** (Mode A: math-only, zero LLM, local SQLite) at the
`_retrieve_guidance` graph node — the highest-leverage memory seam in the
codebase, since it runs on every workflow execution.

`mem0` was deferred indefinitely. Its natural use case is a long-running chat
assistant remembering individual users across sessions, and Battlestats does
not have one. Revisit only if a future feature explicitly needs it.

## What SuperLocalMemory does here

SLM operates **only** on `_retrieve_guidance`
(`server/warships/agentic/graph.py`). It re-ranks the deterministic output of
`retrieve_doctrine_guidance` (`server/warships/agentic/retrieval.py`) by
issuing a semantic `recall` against the `agents/` markdown corpus.

The corpus is indexed lazily into a local SQLite database the first time the
node fires, and subsequent calls only re-ingest files whose mtime has changed
since the last run. Mode A means no Ollama, no cloud LLM, no recurring cost.

The deterministic retrieval list remains the source of truth: SLM only
reorders and supplements it, and any candidate it cannot improve is returned
unchanged.

## Layer position

```
_retrieve_guidance(state)
        │
        ├── retrieve_doctrine_guidance(task)              ← deterministic baseline
        │       (token-bigram match over GUIDANCE_GLOBS)
        │
        └── if BATTLESTATS_SLM_ENABLED=1
                ├── ensure_corpus_indexed(client)         ← lazy / mtime-aware
                └── rerank_guidance(client, task, …)      ← additive reorder
```

`prepare_phase0_memory_context()` and the procedural Phase 0 memory subsystem
are unchanged. SLM does not touch them.

## Enabling and disabling

**Local SDLC.** Set the env vars before invoking any of the agentic entry
points:

```bash
ENABLE_AGENTIC_RUNTIME=1
BATTLESTATS_SLM_ENABLED=1
cd server
python scripts/run_agent_graph.py "fix clan hydration bug" --json
```

The first call indexes the `agents/` markdown corpus into
`server/logs/agentic/slm/corpus.db`. Subsequent calls only re-ingest files
whose mtime is newer than the marker file alongside the database.

**Production droplet.** SLM lives in `server/requirements-agentic.txt` and is
only installed when `DEPLOY_AGENTIC_RUNTIME=1` is passed to the deploy script.
Even when installed, it stays off until `BATTLESTATS_SLM_ENABLED=1` is set in
the droplet env. Mode A is droplet-safe (no network, no GPU, low memory) but
still opt-in.

## Optional knobs

| Var | Default | Purpose |
|---|---|---|
| `BATTLESTATS_SLM_ENABLED` | `0` | Master switch |
| `BATTLESTATS_SLM_MODE` | `A` | `A` is the only supported mode in this rollout. `B` (Ollama) and `C` (cloud) are stubbed for future work |
| `BATTLESTATS_SLM_DB_PATH` | `server/logs/agentic/slm/corpus.db` | SQLite location |
| `BATTLESTATS_SLM_REINDEX_ON_BOOT` | `0` | Force a full reindex on next call |

The same keys are also accepted as `slm_*` keys on the workflow `state` dict
for per-run overrides without touching the env.

## Forcing a reindex

Either set `BATTLESTATS_SLM_REINDEX_ON_BOOT=1` for the next run, or delete the
marker file alongside the SQLite database:

```bash
rm server/logs/agentic/slm/corpus.indexed
```

The next call will walk `GUIDANCE_GLOBS` and re-feed every file.

## Trace dashboard

When `ENABLE_AGENTIC_RUNTIME=1`, the `/trace` dashboard (`dashboard.py`) emits
an `slm` config card alongside the existing agentic config. The card surfaces:

- `enabled` — env-flag state
- `dependency_available` — whether `superlocalmemory` was importable
- `configured` — both of the above true
- `mode`, `db_path`, `reindex_on_boot`
- `guidance_globs` — the corpus globs SLM walks

If the card shows `dependency_available=false`, the agentic extras lane has
not been installed in the active Python environment.

## Operator cleanup after the Hindsight removal

After the 2026-04-10 deploy, the droplet env file
(`/etc/battlestats-server.env`) still has the legacy lines:

```bash
BATTLESTATS_HINDSIGHT_ENABLED=0
BATTLESTATS_HINDSIGHT_API_URL=
HINDSIGHT_API_KEY=
BATTLESTATS_HINDSIGHT_BUDGET=mid
BATTLESTATS_HINDSIGHT_MAX_TOKENS=4096
BATTLESTATS_HINDSIGHT_TAGS=
```

These are no-ops once the code is gone but should be removed on the next
deploy window:

```bash
ssh deploy@battlestats.online
sudo sed -i '/^BATTLESTATS_HINDSIGHT_/d; /^HINDSIGHT_API_KEY=/d' /etc/battlestats-server.env
sudo systemctl restart battlestats-server
```

If SLM should also run on the droplet, append the new vars in the same edit:

```bash
sudo tee -a /etc/battlestats-server.env <<'EOF'
BATTLESTATS_SLM_ENABLED=1
BATTLESTATS_SLM_MODE=A
BATTLESTATS_SLM_DB_PATH=/var/lib/battlestats/slm/corpus.db
BATTLESTATS_SLM_REINDEX_ON_BOOT=0
EOF
```

The droplet's deploy script must include `DEPLOY_AGENTIC_RUNTIME=1` for SLM to
be installed at all.

## Verification

```bash
cd server

# 1. New SLM tests pass.
python -m pytest \
  warships/tests/test_agentic_slm.py \
  warships/tests/test_agentic_graph_guidance.py -x

# 2. No file references hindsight outside the archive.
grep -ri hindsight server/ client/ agents/runbooks CLAUDE.md README.md
# Expected: zero matches outside agents/runbooks/archive/.

# 3. Lean release gate (must still pass — no behavior change to non-agentic surfaces).
python -m pytest \
  warships/tests/test_views.py warships/tests/test_landing.py \
  warships/tests/test_realm_isolation.py warships/tests/test_data_product_contracts.py \
  -x --tb=short
```

## Maintenance expectations

Per project doctrine (CLAUDE.md "Runbook reconciliation"), this runbook is the
authoritative description of the optional agentic memory layer. Update it on
every change to `server/warships/agentic/superlocalmemory.py`,
`_retrieve_guidance`, or the `BATTLESTATS_SLM_*` env contract.
