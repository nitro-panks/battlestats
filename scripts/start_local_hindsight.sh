#!/usr/bin/env bash

set -euo pipefail

if [[ -z "${HINDSIGHT_API_LLM_PROVIDER:-}" ]]; then
  if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    export HINDSIGHT_API_LLM_PROVIDER="openai"
  elif [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    export HINDSIGHT_API_LLM_PROVIDER="anthropic"
  elif [[ -n "${GEMINI_API_KEY:-}" ]]; then
    export HINDSIGHT_API_LLM_PROVIDER="gemini"
  else
    export HINDSIGHT_API_LLM_PROVIDER="openai"
  fi
fi

if [[ -z "${HINDSIGHT_API_LLM_API_KEY:-}" ]]; then
  case "${HINDSIGHT_API_LLM_PROVIDER}" in
    openai)
      export HINDSIGHT_API_LLM_API_KEY="${OPENAI_API_KEY:-}"
      ;;
    anthropic)
      export HINDSIGHT_API_LLM_API_KEY="${ANTHROPIC_API_KEY:-}"
      ;;
    gemini)
      export HINDSIGHT_API_LLM_API_KEY="${GEMINI_API_KEY:-}"
      ;;
  esac
fi

if [[ -z "${HINDSIGHT_API_LLM_API_KEY:-}" ]]; then
  echo "HINDSIGHT_API_LLM_API_KEY is not set." >&2
  echo "Export a provider key first, for example:" >&2
  echo "  export OPENAI_API_KEY=..." >&2
  echo "  export ANTHROPIC_API_KEY=..." >&2
  echo "  export GEMINI_API_KEY=..." >&2
  echo "Or set HINDSIGHT_API_LLM_API_KEY directly." >&2
  echo "Optional: export HINDSIGHT_API_LLM_PROVIDER=openai|anthropic|gemini" >&2
  echo "Optional: export HINDSIGHT_API_LLM_MODEL=gpt-5-mini" >&2
  exit 1
fi

export HINDSIGHT_API_LLM_MODEL="${HINDSIGHT_API_LLM_MODEL:-gpt-5-mini}"

docker compose --profile agentic-memory up -d hindsight

cat <<'EOF'
Local Hindsight is running.

Host URLs:
  API: http://127.0.0.1:8899
  UI:  http://127.0.0.1:9999

Recommended env for host-venv agentic commands:
  source ./scripts/use_local_hindsight_env.sh

Recommended env for docker-compose battlestats services:
  export ENABLE_AGENTIC_RUNTIME=1
  export BATTLESTATS_HINDSIGHT_ENABLED=1
  export BATTLESTATS_HINDSIGHT_API_URL=http://hindsight:8888
EOF