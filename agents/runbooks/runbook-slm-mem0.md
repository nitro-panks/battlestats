# SLM + mem0 Editorial Memory Runbook

Reference implementation: [oturu](https://github.com/anthropics/claude-code). Setup learnings captured after unbreaking a silently-degraded memory layer on a production DO droplet.

This runbook covers everything you need to get **SuperLocalMemory (SLM)** and **mem0** running as a cross-context memory layer for an LLM pipeline, including the non-obvious failure modes that the soft-fail wrapper pattern tends to hide.

---

## When to use this

You have an LLM pipeline (scoring / summarizing / synthesis / agents) and you want it to:

1. **Recall specific prior facts** from earlier runs when generating a new output — atomic, fine-grained, math-based retrieval. That's **SLM**.
2. **Benefit from distilled higher-order patterns** that the system has learned across many runs — recurring mechanisms, cluster patterns, proven framings. That's **mem0**.

The two layers are complementary. SLM answers *"what facts do I already know that match this query"* in ~50–200ms with no LLM call. mem0 answers *"what patterns has the system distilled across dozens of runs"* using a small LLM (Haiku-tier) to extract insights post-run and again at recall time.

You want both if your pipeline runs repeatedly over similar-shape inputs and could benefit from editorial/domain continuity. You want only SLM if you're bootstrapping and don't yet have enough runs to distill patterns. You want only mem0 if you don't care about raw-fact recall and just want distilled insights.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Your pipeline                        │
│                                                              │
│   Scoring ────► recall_context()  ──── SLM ──► atomic facts │
│                                                              │
│   Synthesis ──► recall_context()  ──── SLM ──► atomic facts │
│              └► recall_insights() ── mem0 ──► distilled     │
│                                                patterns     │
│                                                              │
│   Publish ───► retain_article()   ──── SLM ──► store facts  │
│              └► consolidate_cycle() ─ mem0 ──► extract &    │
│                                                consolidate  │
└─────────────────────────────────────────────────────────────┘

SLM storage: ~/.superlocalmemory/<profile>/
  - memory.db       (sqlite, atomic facts + embeddings)
  - audit_chain.db  (append-only provenance)
  - pending.db      (write buffer)

mem0 storage: <your-project>/data/mem0/
  - chromadb collection, persistent on disk
```

A thin wrapper module (`app/memory.py` in oturu) catches all exceptions from both layers and falls back to empty-string / False returns. This keeps the pipeline resilient — but it also silently hides broken setups, which is the single biggest lesson from the oturu incident. Complement the soft-fail with health checks and prestart logging (see below).

---

## 1. Dependencies (the ones that bit us)

### requirements.txt

```
# Core memory layers
superlocalmemory[search]==3.4.0
mem0ai
chromadb

# Transitive pins required by the above
mistralai>=2.3.1
websockets>=15,<16
```

### Why each pin matters

| Pin | Reason |
|---|---|
| `superlocalmemory[search]` | The bare `superlocalmemory` package does not install the embedding backend (sentence-transformers, torch, sklearn, geoopt). Without `[search]`, `slm doctor` reports `Search deps: Missing: torch, sklearn, geoopt, sentence_transformers` and every `store()` call hangs until the 180s embedding worker timeout fires. This is easy to miss because the wrapper swallows the timeout and returns False. |
| `==3.4.0` | Pin the SLM version explicitly. 3.4.0 introduced new embedding model defaults; 3.3.x takes a different path. Mixing versions across environments will produce different `embedding_signature` values and silently cause recall misses. |
| `mem0ai` | The PyPI package is `mem0ai`, but the import is `from mem0 import Memory`. Easy to misremember. |
| `chromadb` | `mem0ai` does **not** pull chromadb by default. If your mem0 config uses `"vector_store": {"provider": "chroma", ...}` (a sensible default), you must install chromadb separately or mem0's `Memory.from_config()` will raise `ImportError: The 'chromadb' library is required`. |
| `mistralai>=2.3.1` | SLM's embedding worker imports `from mistralai import Mistral`. See the "mistralai shim" pitfall below. |
| `websockets>=15,<16` | Irreconcilable conflict: `atproto<=0.0.65` requires `websockets<16`, while SLM 3.4.0's metadata declares `websockets>=16.0`. Runtime-test shows SLM actually works fine with 15.x, and atproto's pin is hard. Pin to 15.x and accept the `pip check` warning from SLM as a false positive. Skip this pin entirely if your project doesn't use atproto. |

### Expect these `pip check` warnings and treat them as false positives

After installing the stack you'll see `pip check` complaints you can safely ignore at runtime, but should know about:

- **`superlocalmemory 3.4.0 has requirement websockets>=16.0`** — metadata only; SLM works with 15.x.
- **`opentelemetry-instrumentation ... semantic-conventions==0.60b1`** — chromadb upgrades opentelemetry, older instrumentation packages complain. Telemetry is non-critical.
- **`logfire ... opentelemetry-sdk<1.40.0`** — same root cause. Logfire is loaded as a pydantic plugin by mistralai; it emits a warning on import but does not crash.

Verify runtime health with the smoke test in section 5 rather than relying on `pip check`.

---

## 2. First-install sequence

### 2.1 Install packages

```bash
pip install -r requirements.txt
```

On a low-memory host (< 2 GB free), also see section 6 on the optional ollama backend, which moves embedding out of the Python process.

### 2.2 Set up SLM profile

This is the step that silently breaks everything if you skip it.

```bash
slm setup                    # downloads models, creates ~/.superlocalmemory/
slm profile create <name>    # e.g. `slm profile create oturu`
slm profile switch <name>    # make it the active profile
slm doctor                   # expect 9 passed, 0 warnings, 0 failed
```

**Why `profile create` is mandatory.** `slm setup` creates only a `default` profile row in `profiles` table. SLM's `memories` table has a FK constraint `profile_id REFERENCES profiles(profile_id)`. If your wrapper code uses `SLMConfig(active_profile="<your-name>")` but you never created a row for that name, **every `store()` call fails with `IntegrityError: FOREIGN KEY constraint failed`**. The wrapper catches the error, logs a warning, and returns False — so the pipeline keeps running with zero memories stored. This was the single hardest bug to diagnose in the oturu incident.

You can verify the profiles table directly:

```bash
sqlite3 ~/.superlocalmemory/memory.db "SELECT profile_id, name FROM profiles"
```

Expected output must include the profile name your code references.

### 2.3 Initialize mem0

mem0 needs no setup command — it lazy-initializes its Chroma collection on first `Memory.from_config()` call. But three environmental requirements have to be in place:

1. `ANTHROPIC_API_KEY` env var (if your config uses `"provider": "anthropic"` for the LLM layer)
2. The Chroma path directory must be writeable by the process user
3. First HuggingFace embedder load downloads `multi-qa-MiniLM-L6-cos-v1` to `~/.cache/huggingface/` (~90 MB). Do this interactively once before wiring it into a systemd service, so the download doesn't race with timeout budgets on first firing.

### 2.4 Wire the wrapper module into your pipeline

Use a thin module that:

- Lazy-initializes both engines as module-level singletons
- Catches all exceptions and returns fallback values (empty string / False)
- Exposes a kill-switch env var so you can disable either layer without touching requirements
- Exposes a `count_memories()` helper for health checks and backfill guards

Reference implementation: [oturu app/memory.py](../app/memory.py). Key API surface:

```python
from app.memory import (
    ensure_bank,      # -> bool; True if SLM engine initializes
    count_memories,   # -> int; raw count of memories for the active profile
    recall_context,   # (query, max_tokens) -> str; formatted SLM context block
    retain_article,   # (article_id, headline, summary, ...) -> bool
    consolidate_cycle,  # (article_id, headline, summary, ..., score_total) -> bool
    recall_insights,  # (query, limit) -> str; formatted mem0 context block
)
```

Feed `recall_context()` / `recall_insights()` returns into your LLM prompts as a prefix before the user message. After a successful generation, call `retain_article()` to add atomic facts to SLM, and `consolidate_cycle()` post-publish to let mem0 extract higher-order patterns.

---

## 3. The mistralai namespace-package pitfall

On some hosts, `from mistralai import Mistral` fails with:

```
ImportError: cannot import name 'Mistral' from 'mistralai' (unknown location)
```

The "unknown location" phrase is diagnostic. What's happening:

- `mistralai` 2.x ships as a [PEP 420 namespace package](https://peps.python.org/pep-0420/). Its on-disk layout has subdirectories (`client/`, `azure/`, `gcp/`, `extra/`) but **no `__init__.py`** at the top level.
- Older consumers (SLM's embedding worker, older `instructor` releases) expect the 1.x-era top-level export surface: `from mistralai import Mistral, MistralClient, ...`
- Python treats namespace packages as empty at the top level — there's nothing to import from `mistralai` directly.

### Fix: add a one-line compat shim

Write an `__init__.py` into the installed package:

```bash
cat > "$(python -c 'import mistralai, os; print(os.path.dirname(mistralai.__path__[0] if hasattr(mistralai, "__path__") else mistralai.__file__))')/mistralai/__init__.py" <<'EOF'
# Compat shim: some libraries (SLM embedding worker, older instructor)
# do `from mistralai import Mistral`, but mistralai 2.x ships as a PEP 420
# namespace package with no top-level re-exports. Wildcard from client
# restores the old import surface without patching callers.
from mistralai.client import *  # noqa: F401,F403
EOF
```

Or manually, if the path gymnastics are too much:

```bash
python -c "import mistralai; print(mistralai.__path__)"
# => _NamespacePath(['/path/to/venv/lib/python3.12/site-packages/mistralai'])
# Then:
echo "from mistralai.client import *  # noqa: F401,F403" > /path/to/venv/lib/python3.12/site-packages/mistralai/__init__.py
```

After the shim is in place:

```bash
python -c "from mistralai import Mistral; print('ok')"
```

should print `ok`.

Note: the shim needs to be re-applied after every `pip install --force-reinstall mistralai` or venv recreation. Record the shim step in your deploy runbook so it doesn't get lost across rebuilds.

---

## 4. Systemd integration: prestart health logging

The soft-fail wrapper is resilient but silent. To surface degradation you didn't ask for, add a lightweight `ExecStartPre=` to any systemd unit that runs the pipeline:

```ini
[Service]
Type=oneshot
User=myapp
WorkingDirectory=/opt/myapp
EnvironmentFile=/opt/myapp/.env

# Soft prestart check — logs SLM health to the journal but does not
# block the pipeline if SLM is degraded (the wrapper soft-fails anyway).
# Leading `-` tells systemd to ignore a non-zero exit.
ExecStartPre=-/opt/myapp/.venv/bin/python -c "from app.memory import ensure_bank, count_memories; ok = ensure_bank(); n = count_memories(); print(f'SLM healthy (memories={n})' if ok else 'SLM DEGRADED — pipeline will run without cross-article context')"

ExecStart=/opt/myapp/.venv/bin/python -m app.cli run

TimeoutStartSec=1800    # headroom for LLM calls; SLM recall adds ~4x scoring latency
MemoryMax=512M
```

Two details matter:

- **Leading `-` on ExecStartPre**: tells systemd to ignore a non-zero exit. You want the check to *log* the problem, not *prevent* the pipeline from running.
- **`TimeoutStartSec` headroom**: with SLM recall active, expect scoring calls to be ~4x slower than without. In oturu the default 600s systemd timeout killed the service mid-pipeline until we bumped it to 1800s.

Verify the prestart fires:

```bash
systemctl start myapp.service
journalctl -u myapp.service --since "1 minute ago" | head -3
```

Expected first two lines:

```
Starting myapp.service ...
SLM healthy (memories=56)
```

---

## 5. Smoke test (run after every install/upgrade)

Paste this into a venv shell. It exercises both layers end-to-end and will surface every failure mode described in this runbook.

```python
# smoke-test.py
import os, sys

# 1. mistralai import (catches the namespace-package pitfall)
try:
    from mistralai import Mistral
    print("[1/5] mistralai import: ok")
except Exception as e:
    print(f"[1/5] mistralai FAIL: {type(e).__name__}: {e}")
    sys.exit(1)

# 2. SLM engine init (catches missing [search] extras, missing profile)
try:
    from superlocalmemory.core.config import SLMConfig, Mode
    from superlocalmemory.core.engine import MemoryEngine
    PROFILE = os.environ.get("SLM_PROFILE", "default")
    e = MemoryEngine(SLMConfig(mode=Mode.A, active_profile=PROFILE))
    e.initialize()
    print(f"[2/5] SLM engine init (profile={PROFILE}): ok")
except Exception as ex:
    print(f"[2/5] SLM FAIL: {type(ex).__name__}: {ex}")
    sys.exit(2)

# 3. SLM store + recall roundtrip (catches FK profile mismatch)
try:
    e.store("Smoke-test fact: the canonical example sentence", session_id="smoke", metadata={"type": "smoke"})
    resp = e.recall("canonical example", limit=3)
    print(f"[3/5] SLM store + recall roundtrip: ok (recalled {len(resp.results)} facts)")
except Exception as ex:
    print(f"[3/5] SLM ROUNDTRIP FAIL: {type(ex).__name__}: {ex}")
    sys.exit(3)

# 4. mem0 init (catches missing chromadb, missing ANTHROPIC_API_KEY)
try:
    from mem0 import Memory
    config = {
        "llm": {"provider": "anthropic", "config": {"model": "claude-haiku-4-5-20251001", "temperature": 0.1, "max_tokens": 1500}},
        "embedder": {"provider": "huggingface", "config": {"model": "multi-qa-MiniLM-L6-cos-v1", "embedding_dims": 384}},
        "vector_store": {"provider": "chroma", "config": {"collection_name": "smoke", "path": "/tmp/smoke-mem0"}},
    }
    m = Memory.from_config(config)
    print("[4/5] mem0 init: ok")
except Exception as ex:
    print(f"[4/5] mem0 FAIL: {type(ex).__name__}: {ex}")
    sys.exit(4)

# 5. mem0 add + search (catches API-key and Chroma-write failures)
try:
    m.add([{"role": "user", "content": "Smoke-test cycle: the system pattern we noticed"}], user_id="smoke-user")
    results = m.search(query="system pattern", user_id="smoke-user", limit=3)
    found = results.get("results", []) if isinstance(results, dict) else results
    print(f"[5/5] mem0 add + search: ok (found {len(found)})")
except Exception as ex:
    print(f"[5/5] mem0 ROUNDTRIP FAIL: {type(ex).__name__}: {ex}")
    sys.exit(5)

print("\nAll 5 checks passed — memory layer is healthy.")
```

Expected output:

```
[1/5] mistralai import: ok
[2/5] SLM engine init (profile=oturu): ok
[3/5] SLM store + recall roundtrip: ok (recalled 1 facts)
[4/5] mem0 init: ok
[5/5] mem0 add + search: ok (found 1)

All 5 checks passed — memory layer is healthy.
```

Clean up the smoke data before re-running with a real profile:

```bash
sqlite3 ~/.superlocalmemory/memory.db "DELETE FROM memories WHERE session_id='smoke'"
rm -rf /tmp/smoke-mem0
```

---

## 6. Optional: ollama backend for memory-constrained hosts

SLM's default embedding backend is `sentence-transformers`, which loads models in-process via torch. On hosts with < 2 GB available RAM, SLM's own heuristic kicks in and logs:

```
Low memory (1.9 GB available) — deferring embedding worker spawn
Skipping embedding worker spawn due to memory pressure
```

The pipeline then runs with no embeddings — recall returns zero results. The fix if you can't add RAM is to move embedding out-of-process via [ollama](https://ollama.com/), which SLM supports as an alternative embedder backend.

### Install

```bash
curl -fsSL https://ollama.com/install.sh | sh     # installs systemd service ollama.service
ollama pull nomic-embed-text                       # ~270 MB, the default model SLM looks for
systemctl status ollama                            # expect active (running)
curl -s http://localhost:11434/api/tags            # expect JSON with nomic-embed-text:latest listed
```

### Point SLM at ollama

Edit `~/.superlocalmemory/<profile>/config.json`:

```json
{
  "embedding": {
    "provider": "ollama",
    "model_name": "nomic-embed-text",
    "dimension": 768,
    "api_endpoint": "http://localhost:11434"
  }
}
```

Or via `slm provider set` if the CLI supports it on your version. After changing the provider, wipe and re-embed existing memories (the embedding signature changes):

```bash
mv ~/.superlocalmemory ~/.superlocalmemory.bak.$(date +%Y%m%d)
slm setup && slm profile create <name> && slm profile switch <name>
# then re-run your backfill script
```

### Tradeoffs

| | sentence-transformers (default) | ollama |
|---|---|---|
| Memory footprint | ~500 MB in your Python process | ~500 MB in ollama's process (separate) |
| Cold-start latency | Model loaded on first embed call | Model kept warm by ollama |
| Setup complexity | pip install only | Requires ollama service running |
| Works under MemoryMax= cgroup limit | Only if limit > ~600M | Yes — embedding runs outside the cgroup |

Use ollama if your pipeline runs under a strict memory cgroup (e.g. `MemoryMax=512M` in systemd) and you can't raise the ceiling. It's not a fix for missing `[search]` extras — you still need the search deps for SLM's non-embedding search channels. It's purely a pressure-relief valve for the embedding worker.

---

## 7. Backfill is not idempotent (guard it)

SLM's `engine.store()` has no dedup key — every call inserts a new memory row, even if the content already exists. If your backfill script iterates over historical records and calls `store()` once per record, running it twice will double every fact. After N runs, recall returns duplicates-of-duplicates.

### Guard pattern

```python
def backfill_memory(force: bool = False):
    from app.memory import count_memories, ensure_bank, retain_article

    if not ensure_bank():
        print("Failed to initialize SLM.")
        return

    existing = count_memories()
    if existing > 0 and not force:
        print(
            f"Refusing to backfill: SLM already contains {existing} memories.\n"
            f"Backfill is NOT idempotent — running it again will duplicate every fact.\n"
            f"Wipe the profile dir and re-run, or pass --force to proceed anyway."
        )
        return

    # ... actual backfill loop
```

The `count_memories()` helper queries the sqlite DB directly for a given profile:

```python
def count_memories() -> int:
    import sqlite3
    from superlocalmemory.core.config import SLMConfig, Mode
    config = SLMConfig(mode=Mode.A, active_profile=PROFILE)
    db_path = str(config.db_path)
    if not os.path.exists(db_path):
        return 0
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE profile_id = ?",
            (PROFILE,),
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()
```

### Wipe-and-reload procedure

When you need to rebuild from scratch (schema change, algorithm change, corrupted state):

```bash
mv ~/.superlocalmemory ~/.superlocalmemory.bak.$(date +%Y%m%d)
slm setup
slm profile create <name>
slm profile switch <name>
python -m yourapp.cli backfill-memory
```

### mem0 is safer but still worth guarding

mem0's LLM extraction layer dedupes internally — if you re-consolidate the same cycle, the extractor usually produces the same facts and mem0 recognizes them. Usually. The LLM occasionally returns malformed JSON (logged as `Invalid JSON response: Expecting ',' delimiter: line X column Y`) and partial extraction can produce near-duplicate insights. Don't rely on mem0 dedup for correctness — treat backfill as a one-shot operation and track whether you've run it per-cycle.

---

## 8. Diagnostic cookbook

### Symptom: pipeline runs but no cross-context ever shows up

Every recall returns an empty string. The wrapper is silently failing. Run the smoke test (section 5) to localize the break. Common causes:

1. **FK constraint failed** — you didn't create the profile. See section 2.2.
2. **Missing `[search]` extras** — `pip install 'superlocalmemory[search]==3.4.0'`. Verify with `slm doctor` (expect `Search deps: ... sentence-transformers, torch, sklearn, geoopt`).
3. **mistralai import error** — apply the shim (section 3).
4. **Memory pressure** — see the "Low memory" diagnostic below.

### Symptom: `Embedding worker timed out after 180s`

Two possible root causes:

- **Missing [search] extras.** Fix: `pip install 'superlocalmemory[search]==3.4.0'`, verify with `slm doctor`.
- **Genuine memory pressure.** Fix: switch to ollama (section 6), or add RAM, or raise the systemd `MemoryMax=` cgroup.

Tell them apart by running `slm doctor`: if it reports search deps missing, it's (1); if search deps are present and doctor passes, it's (2).

### Symptom: `Low memory — deferring embedding worker spawn`

SLM's own heuristic has decided there's < 2 GB free and is refusing to spawn the embedding worker. Check:

```bash
free -h
```

If `available` is below ~2 GB, switch to ollama or add swap. Even 2 GB of swap is enough to flip the heuristic to green; it just needs the kernel to report enough free virtual memory.

### Symptom: `cannot import name 'Mistral' from 'mistralai' (unknown location)`

Namespace package issue. Apply the shim in section 3.

### Symptom: `ANTHROPIC_API_KEY not set — mem0 unavailable`

The `_get_mem0()` helper checks for the env var before attempting to import mem0. Common cause: running a script via `sudo -u myapp` without sourcing the `.env` file:

```bash
# wrong — no env
sudo -u myapp /opt/myapp/.venv/bin/python -m app.cli backfill-mem0

# right — source the env first
sudo -u myapp bash -c "set -a; source /opt/myapp/.env; set +a; .venv/bin/python -m app.cli backfill-mem0"
```

Note: systemd services running via `EnvironmentFile=/opt/myapp/.env` get the env correctly. It's only ad-hoc `sudo -u` invocations that lose it.

### Symptom: `No module named 'mem0'` (after installing mem0ai)

You probably installed `mem0` (the old, unrelated package) instead of `mem0ai`. Uninstall both and reinstall:

```bash
pip uninstall -y mem0 mem0ai
pip install mem0ai
python -c "from mem0 import Memory; print('ok')"
```

### Symptom: `ImportError: The 'chromadb' library is required`

`mem0ai` doesn't install chromadb as a dependency. Install it:

```bash
pip install chromadb
```

### Symptom: `Invalid JSON response: Expecting ',' delimiter`

mem0's fact extractor LLM returned malformed JSON. mem0 logs the warning and extracts whatever facts it could parse. Non-fatal. If you see this frequently for one specific cycle, try lowering `temperature` on the fact extraction LLM config to `0.0`, or simplify the custom extraction prompt.

### Symptom: `pip check` complains about websockets / opentelemetry

Expected false positives — see section 1. Verify runtime health with the smoke test instead.

### Symptom: Reranker worker timed out after 180s

SLM's cross-encoder reranker (different from the embedding worker) failed to warm up, and recalls fall back to BM25 scoring. This is a quality degradation, not a functional failure — recall still works, just with slightly less relevance ranking. Root cause is usually the same memory pressure issue as the embedding worker; fix the same way (ollama or more RAM).

---

## 9. Diagnostic commands quick-reference

```bash
# SLM health
slm doctor                                                    # 9-point check
slm status                                                    # profile + DB size
slm profile list                                              # all profiles
sqlite3 ~/.superlocalmemory/memory.db "SELECT COUNT(*) FROM memories"
sqlite3 ~/.superlocalmemory/memory.db "SELECT * FROM profiles"

# mem0 health
python -c "from app.memory import _get_mem0; print(_get_mem0() is not None)"
ls -la <your-project>/data/mem0/                              # chroma collection on disk

# Package versions
pip show superlocalmemory mem0ai chromadb mistralai websockets
pip check                                                     # will show expected false positives

# Wrapper smoke test (reference implementation)
python -c "from app.memory import ensure_bank, count_memories, recall_context, recall_insights; print('SLM:', ensure_bank(), 'memories:', count_memories()); print('recall:', len(recall_context('test'))); print('insights:', len(recall_insights('test')))"

# Ollama (if using it)
systemctl status ollama
curl -s http://localhost:11434/api/tags | python -m json.tool
curl -s http://localhost:11434/api/embeddings -d '{"model":"nomic-embed-text","prompt":"test"}' | head -c 200
```

---

## 10. Adapting this for a new project — checklist

- [ ] Add the five pinned deps to `requirements.txt` (drop `websockets` pin if you don't use atproto)
- [ ] `pip install -r requirements.txt`
- [ ] Apply the mistralai shim (section 3) if `from mistralai import Mistral` fails
- [ ] `slm setup`, then `slm profile create <your-profile-name>`, then `slm profile switch <your-profile-name>`
- [ ] `slm doctor` — expect 9/9 PASS
- [ ] Port or adapt the wrapper module pattern from `app/memory.py` (lazy init, soft-fail, kill-switch env var, `count_memories()` helper)
- [ ] Wire `recall_context()` / `recall_insights()` calls into your LLM prompt builders (scoring and synthesis, or wherever cross-context would help)
- [ ] Wire `retain_article()` / `consolidate_cycle()` into your post-run hook
- [ ] Add the idempotency guard to your backfill script
- [ ] Add the `ExecStartPre=-` health log to your systemd service unit (if applicable)
- [ ] Bump `TimeoutStartSec=` to give room for SLM recall latency (oturu uses 1800s)
- [ ] Run the section 5 smoke test
- [ ] Run your pipeline once end-to-end and verify `journalctl` shows `SLM healthy (memories=N)` before the main process starts
- [ ] Document the `SLM_DISABLE=1` env var as a mitigation path in your own deploy runbook

---

## 11. When to disable or strip out each layer

**Disable SLM (`SLM_DISABLE=1`)** if: recall latency is dominating your pipeline runtime, or SLM is broken in a way you can't fix immediately. The wrapper's soft-fail path is fine to rely on for short periods — the pipeline just runs without cross-cycle facts.

**Strip out mem0** if: your Anthropic budget is the constraint (mem0's LLM extraction adds ~1 Haiku-tier call per published cycle), or you're seeing repeated `Invalid JSON response` warnings and the insights aren't improving.

**Strip out both** if: you're at the prototype stage with fewer than ~10 historical runs. Neither layer produces useful output until there's enough history to recall from.

---

## Appendix: a note on the soft-fail wrapper pattern

The wrapper pattern (catch every exception, log a warning, return fallback) is the right default for a memory layer — it keeps the pipeline resilient, and a pipeline running without cross-context is strictly better than a pipeline crashing. But it has one systemic weakness: **broken setups look identical to empty setups**. Empty recall on a fresh install and empty recall on a broken install are the same return value.

The oturu project shipped v1.0.5 with mem0 and SLM both silently broken for weeks — every scoring and synthesis call fell through the soft-fail path, every publish produced an empty consolidation, and the roadmap's "16 insights from 2 cycles" was aspirational rather than observed. Nothing crashed; nothing was wrong at a glance. The diagnosis happened only because an unrelated `feedparser` missing-module error forced a close read of the autorun journal.

Two practices close the loop:

1. **Always pair soft-fail with a health-check helper** (`ensure_bank()`, `count_memories()`) that a human or a systemd unit can call directly to get a truthy/falsy answer about whether the layer is actually working.
2. **Log health at every entry point** — a startup line, a prestart check, a periodic heartbeat. The goal is that a broken memory layer is *loud in the logs* even though it's silent in the pipeline output.

Apply both and you keep the resilience benefit without the silent-decay failure mode.
