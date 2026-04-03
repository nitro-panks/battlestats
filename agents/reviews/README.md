# Reviews

This directory holds review artifacts, QA snapshots, and critique notes from prior work.

Use it only when:

- an active runbook explicitly points to a review,
- you are revisiting an unresolved defect or regression, or
- you need historical reasoning for a current implementation choice.

Do not scan this directory by default when gathering task context. Reviews are supporting material, not the canonical source of current repo behavior.

These files normally should not have active entries in `../doc_registry.json`. If a review must influence retrieval, the active runbook should point to it explicitly.

Current source-of-truth order:

1. `../README.md`
2. `../runbooks/README.md`
3. the specific active runbook
4. only then the linked review artifact