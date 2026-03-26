import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from warships.agentic.memory import (
    get_memory_store_snapshot,
    get_pending_memory_candidates,
    review_memory_candidates,
)


def _load_json_file(path: str | None) -> dict:
    if not path:
        return {}
    payload = Path(path)
    with payload.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class Command(BaseCommand):
    help = "Review queued agentic memory candidates without injecting approvals through workflow context."

    def add_arguments(self, parser):
        parser.add_argument("--workflow-id", type=str, default=None)
        parser.add_argument("--approve", action="append", default=[])
        parser.add_argument("--reject", action="append", default=[])
        parser.add_argument("--reviewed-by", type=str, default="cli-review")
        parser.add_argument("--supersedes-file", type=str, default=None)
        parser.add_argument("--backend", type=str, default=None,
                            choices=["file", "langgraph_memory", "langgraph_postgres"])
        parser.add_argument("--environment", type=str, default=None)
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        context = {}
        if options.get("backend"):
            context["memory_backend"] = options["backend"]
        if options.get("environment"):
            context["memory_environment"] = options["environment"]

        workflow_id = options.get("workflow_id")
        approve = [value for value in options.get("approve", []) if value]
        reject = [value for value in options.get("reject", []) if value]

        if not workflow_id:
            payload = get_memory_store_snapshot(
                limit=options.get("limit", 10), context=context)
            self._write_payload(payload, options.get("as_json", False))
            return

        if not approve and not reject:
            payload = {
                "workflow_id": workflow_id,
                "candidates": get_pending_memory_candidates(workflow_id, context=context),
            }
            self._write_payload(payload, options.get("as_json", False))
            return

        review_context = {
            "approved_candidate_ids": approve,
            "rejected_candidate_ids": reject,
            "reviewed_by": options.get("reviewed_by") or "cli-review",
            "supersedes": _load_json_file(options.get("supersedes_file")),
        }
        result = review_memory_candidates(
            workflow_id, review_context, context=context)
        payload = {
            "workflow_id": workflow_id,
            **result,
            "remaining_candidates": get_pending_memory_candidates(workflow_id, context=context),
        }
        self._write_payload(payload, options.get("as_json", False))

    def _write_payload(self, payload: dict, as_json: bool) -> None:
        if as_json:
            self.stdout.write(json.dumps(payload, indent=2))
            return

        if "candidates" in payload:
            self.stdout.write(self.style.SUCCESS(
                f"Workflow: {payload.get('workflow_id')}"))
            for candidate in payload.get("candidates", []):
                self.stdout.write(
                    f"- {candidate.get('candidate_id')}: {candidate.get('summary')}")
            return

        if "promoted_count" in payload:
            self.stdout.write(self.style.SUCCESS(
                f"Workflow: {payload.get('workflow_id')}"))
            self.stdout.write(f"Promoted: {payload.get('promoted_count', 0)}")
            self.stdout.write(f"Rejected: {payload.get('rejected_count', 0)}")
            for path in payload.get("reviewed_store_paths", []):
                self.stdout.write(f"Reviewed store: {path}")
            return

        self.stdout.write(self.style.SUCCESS("Agentic memory snapshot"))
        self.stdout.write(f"Backend: {payload.get('backend')}")
        self.stdout.write(
            f"Reviewed total: {payload.get('reviewed_total', 0)}")
        self.stdout.write(
            f"Pending review total: {payload.get('pending_review_total', 0)}")
