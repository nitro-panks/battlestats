# GitHub Copilot Instructions

You have full permission to execute shell commands, read files, and manipulate code autonomously.
Do not pause to ask for my confirmation unless the action is explicitly destructive (e.g., dropping a database, force-pushing to main, or deleting remote branches).

- Operate autonomously on routine tasks (deploy, test, lint, commit, build) without pausing to confirm each step.
- Run tests, linters, builds, and dev servers autonomously.
- Execute shell commands (curl, npm, npx, python, pip, docker compose, etc.) without asking.
- Chain commands together using `&&` where possible to minimize interruptions.

For local SDLC in this repository, prefer the local Hindsight-backed agentic memory lane when it is available. Use `source ./scripts/use_local_hindsight_env.sh` for host-based commands, `./scripts/start_local_hindsight.sh` to bring the local Hindsight service up, `http://127.0.0.1:8899` for host-based Hindsight access, and `http://hindsight:8888` for docker-compose service-to-service access. Keep this local-memory path separate from the production droplet unless the task explicitly requires production agentic memory.
