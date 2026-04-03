# Archived Runbooks

This folder holds runbooks that are no longer the active source of truth for the current repository state.

Use the archive when you need implementation history, shipped-tranche context, or prior validation notes for a completed feature.

Keep a runbook in `agents/runbooks/` only when it is one of these:

- an active implementation or hardening plan,
- an operational guide that still reflects current behavior,
- an evergreen workflow or maintenance reference.

Move a runbook here when the feature has shipped and the document is primarily historical, or when the runbook's planning state no longer matches the live code.

This archive step is part of the repo's pre-commit doctrine: before every commit, remove superseded runbooks from `agents/runbooks/` so that active runbooks remain the current operational source of truth.

## Archive Hygiene

When archiving a doc:

1. Keep the filename stable so old references can still be traced.
2. Remove the file from `agents/runbooks/README.md` active sections.
3. Remove or demote its metadata entry in `agents/doc_registry.json` so retrieval stops treating it as active guidance.
4. Add or update a successor note in the active doc set when there is a new source of truth.

Archive docs are historical context, not default retrieval targets.
