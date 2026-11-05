#!/bin/zsh
# Hindsight API launcher (run by launchd, which does not source ~/.zshrc).
# ~/.hindsight/.env (chmod 600) must export HINDSIGHT_API_LLM_API_KEY=<your key>.
source ~/.hindsight/.env

# Any OpenAI-compatible endpoint works. Pick a model you have verified with a real inference call.
export HINDSIGHT_API_LLM_PROVIDER=openai
export HINDSIGHT_API_LLM_MODEL=<model-id>
export HINDSIGHT_API_LLM_BASE_URL="<https://your-provider/v1>"
unset OPENAI_BASE_URL  # keeps a shell-level override from hijacking extraction calls

exec ~/.hindsight-venv/bin/hindsight-api --host 127.0.0.1 --port 8888
