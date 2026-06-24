from __future__ import absolute_import, unicode_literals

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "battlestats.settings")

app = Celery("battlestats")
app.config_from_object("django.conf:settings", namespace="CELERY")

# Emit task-lifecycle events so Flower (observability) can render per-task
# history, not just worker liveness + queue depth. Cheap on the broker; the
# alternative (runtime `celery control enable_events`) is lost on worker
# restart. See agents/runbooks/runbook-flower-observability-2026-04-02.md.
app.conf.worker_send_task_events = True

app.autodiscover_tasks()
