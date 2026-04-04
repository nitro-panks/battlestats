"""
Enrichment batch function for DigitalOcean Functions.

Boots a minimal Django environment, then runs enrich_players() in a loop
until the timeout approaches.  Each loop iteration processes one batch
(default 500 players).  The function exits cleanly before the platform
timeout so the next scheduled invocation picks up where it left off.

Designed to replace the Celery-based enrich_player_data_task running on
the background queue.
"""

import base64
import json
import logging
import os
import sys
import tempfile
import time

# ── Timeout budget ──────────────────────────────────────────
# DO Functions max timeout is 900s (15 min).  We leave a 60s buffer
# for cleanup and response serialisation.
HARD_TIMEOUT_S = int(os.getenv("ENRICH_TIMEOUT_S", "840"))

# ── Batch config (mirrors the Celery task env vars) ─────────
BATCH_SIZE = int(os.getenv("ENRICH_BATCH_SIZE", "500"))
MIN_PVP_BATTLES = int(os.getenv("ENRICH_MIN_PVP_BATTLES", "500"))
MIN_WR = float(os.getenv("ENRICH_MIN_WR", "48.0"))
DELAY = float(os.getenv("ENRICH_DELAY", "0.2"))
REALMS_ENV = os.getenv("ENRICH_REALMS", "").strip()
REALMS = tuple(r.strip() for r in REALMS_ENV.split(",") if r.strip()) or None

log = logging.getLogger("enrich-fn")


def _write_ca_cert():
    """Decode DB_CA_CERT_B64 to a temp file and set DB_SSLROOTCERT."""
    ca_b64 = os.getenv("DB_CA_CERT_B64", "")
    if not ca_b64:
        return
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
    tmp.write(base64.b64decode(ca_b64))
    tmp.flush()
    tmp.close()
    os.environ["DB_SSLROOTCERT"] = tmp.name
    os.environ.setdefault("DB_SSLMODE", "require")


def _boot_django():
    """Minimal Django setup for ORM + cache access."""
    # The server package is copied into the function root by build.sh
    server_dir = os.path.join(os.path.dirname(__file__), "server")
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "battlestats.settings")

    # Ensure the logs directory exists (settings.py creates it at import)
    logs_dir = os.path.join(server_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    import django
    django.setup()


# ── Module-level init (reused across warm invocations) ──────
_django_ready = False


def _ensure_django():
    global _django_ready
    if _django_ready:
        return
    _write_ca_cert()
    _boot_django()
    _django_ready = True


def main(event, context):
    start = time.time()
    _ensure_django()

    from warships.management.commands.enrich_player_data import enrich_players

    batches_completed = 0
    total_enriched = 0
    total_errors = 0
    batch_summaries = []

    while True:
        elapsed = time.time() - start
        remaining = HARD_TIMEOUT_S - elapsed

        # Need at least 120s for a batch (typical is 5-10 min for 500 players,
        # but small batches or warm caches can be faster)
        if remaining < 120:
            log.info(
                "Exiting loop: %.0fs remaining (need 120s minimum for a batch)",
                remaining,
            )
            break

        log.info(
            "Starting batch %d (elapsed=%.0fs, remaining=%.0fs)",
            batches_completed + 1, elapsed, remaining,
        )

        try:
            summary = enrich_players(
                batch=BATCH_SIZE,
                min_pvp_battles=MIN_PVP_BATTLES,
                min_wr=MIN_WR,
                delay=DELAY,
                realms=REALMS,
            )
        except Exception as exc:
            log.exception("Batch %d failed", batches_completed + 1)
            batch_summaries.append({
                "batch": batches_completed + 1,
                "status": "error",
                "error": str(exc),
            })
            total_errors += 1
            break

        batches_completed += 1
        enriched = summary.get("enriched", 0)
        errors = summary.get("errors", 0)
        total_enriched += enriched
        total_errors += errors
        batch_summaries.append(summary)

        # If no candidates were found, the pass is complete
        candidates = summary.get("candidates", {})
        total_candidates = sum(candidates.values()) if isinstance(candidates, dict) else 0
        if total_candidates == 0 or enriched == 0:
            log.info("No more candidates — enrichment pass complete")
            break

    elapsed_total = round(time.time() - start, 1)

    result = {
        "status": "ok",
        "batches_completed": batches_completed,
        "total_enriched": total_enriched,
        "total_errors": total_errors,
        "elapsed_seconds": elapsed_total,
        "realms": list(REALMS) if REALMS else "all",
        "batch_size": BATCH_SIZE,
        "batch_summaries": batch_summaries,
    }

    log.info("Function complete: %s", json.dumps(result, default=str))
    return {"body": result}
