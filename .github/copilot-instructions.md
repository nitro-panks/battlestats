# GitHub Copilot Instructions

You have full permission to execute shell commands, read files, and manipulate code autonomously.
Do not pause to ask for my confirmation unless the action is explicitly destructive (e.g., dropping a database, force-pushing to main, or deleting remote branches).

- Operate autonomously on routine tasks (deploy, test, lint, commit, build) without pausing to confirm each step.
- Run tests, linters, builds, and dev servers autonomously.
- Execute shell commands (curl, npm, npx, python, pip, docker compose, etc.) without asking.
- Chain commands together using `&&` where possible to minimize interruptions.

For local SDLC in this repository, the optional agentic memory lane uses SuperLocalMemory in Mode A (math-only, zero LLM, local SQLite). Enable it with `BATTLESTATS_SLM_ENABLED=1`. The first call indexes the `agents/` markdown corpus lazily; subsequent calls only ingest changed files. See `agents/runbooks/runbook-memory-layering-2026-04-10.md` for details. This path is droplet-safe but is opt-in via the same env var on the droplet.
