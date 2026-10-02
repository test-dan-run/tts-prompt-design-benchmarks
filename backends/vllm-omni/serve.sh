#!/usr/bin/env bash
# Entry point for a TTS backend container.
#   MODEL_PATH         directory of the weights inside the container (required)
#   SERVED_MODEL_NAME  name exposed on the OpenAI-compatible API (optional)
#   PORT               listen port (default 8091)
# Any container arguments are passed straight through to `vllm serve`.
set -euo pipefail

: "${MODEL_PATH:?MODEL_PATH must point at a model directory under /models}"
PORT="${PORT:-8091}"

if [[ ! -d "$MODEL_PATH" ]]; then
  echo "ERROR: model directory '$MODEL_PATH' not found in the mounted models volume." >&2
  echo "Download it first, e.g.: docker compose run --rm downloader <hf_repo>" >&2
  echo "Contents of /models:" >&2
  ls -la /models >&2 || true
  exit 64
fi

args=(serve "$MODEL_PATH" --omni --host 0.0.0.0 --port "$PORT")
if [[ -n "${SERVED_MODEL_NAME:-}" ]]; then
  args+=(--served-model-name "$SERVED_MODEL_NAME")
fi

echo "+ vllm ${args[*]} $*"
exec vllm "${args[@]}" "$@"
