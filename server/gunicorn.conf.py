# gunicorn.conf.py
import multiprocessing
import os
import subprocess
import threading

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
    """Fire startup cache warmers once the master is ready to accept connections."""
    if os.getenv("WARM_CACHES_ON_STARTUP", "1") != "1":
        return
    delay = int(os.getenv("CACHE_WARMUP_START_DELAY_SECONDS", "5"))

    def _run():
        import time
        time.sleep(delay)
        server.log.info("Running startup cache warmers...")
        result = subprocess.run(
            ["python", "manage.py", "startup_warm_all_caches", "--delay", "0"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            server.log.info("Startup cache warmers completed successfully.")
        else:
            server.log.error("Startup cache warmers failed (exit %d): %s",
                             result.returncode, result.stderr[-500:] if result.stderr else "")

    t = threading.Thread(target=_run, daemon=True, name="startup-warmer")
    t.start()
