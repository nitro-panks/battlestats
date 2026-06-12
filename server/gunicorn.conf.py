# gunicorn.conf.py
import multiprocessing
import os

# Non logging stuff
bind = "unix:/run/gunicorn.sock"
# 2*CPU + 1 is the Gunicorn recommendation; floor at 3, cap at 9 to stay
# within the memory budget of a single-droplet deploy.
workers = min(max(multiprocessing.cpu_count() * 2 + 1, 3), 9)
# Access log - records incoming HTTP requests
accesslog = "-"
# Error log - records Gunicorn server goings-on
errorlog = "-"
# Whether to send Django output to the error log
capture_output = True
# How verbose the Gunicorn error logs should be
loglevel = "info"


def when_ready(server):
    """Dispatch startup cache warmers to Celery background queue."""
    if os.getenv("WARM_CACHES_ON_STARTUP", "1") != "1":
        return
    delay = int(os.getenv("CACHE_WARMUP_START_DELAY_SECONDS", "5"))

    server.log.info(
        "Dispatching startup cache warmers to Celery (countdown=%ds)...", delay)
    try:
        from warships.tasks import startup_warm_caches_task
        startup_warm_caches_task.apply_async(countdown=delay)
    except Exception:
        server.log.exception(
            "Startup cache warm dispatch failed; continuing without startup warmers.")
