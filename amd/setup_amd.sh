#!/usr/bin/env bash
# MemoryBridge MI300X setup: native Linux/ROCm, no Docker required.
# Run this on the MI300X Linux instance, not on Windows.
set -euo pipefail

if ! command -v python3.12 >/dev/null 2>&1; then
  echo "ERROR: Python 3.12 is required by the documented ROCm vLLM wheel path."
  echo "Use an AMD-supported Linux image with Python 3.12, ROCm 7.x, and glibc >= 2.35."
  echo "See README.md -> AMD MI300X deployment."
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv into this user's account..."
  python3.12 -m pip install --user uv
  export PATH="$HOME/.local/bin:$PATH"
fi

uv venv --python 3.12 .venv-vllm
# shellcheck disable=SC1091
source .venv-vllm/bin/activate
python -m pip install --upgrade pip
# Use vLLM's ROCm wheel index rather than an unqualified pip install.
uv pip install vllm --extra-index-url https://wheels.vllm.ai/rocm/

mkdir -p .cache/miopen
cat <<'EOF'

Install complete. Start the model server in this terminal with:
  source .venv-vllm/bin/activate
  MIOPEN_USER_DB_PATH="$PWD/.cache/miopen" MIOPEN_FIND_MODE=FAST \
    vllm serve Qwen/Qwen2.5-7B-Instruct \
      --host 127.0.0.1 --port 8000 --max-model-len 8192

Keep this terminal running. In a SECOND terminal, start MemoryBridge:
  cd memorybridge
  python3.12 -m venv .venv-app
  source .venv-app/bin/activate
  pip install -r requirements.txt
  export LLM_BASE_URL=http://127.0.0.1:8000/v1
  export LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
  export CLINICIAN_TOKEN='replace-with-a-long-random-token'
  uvicorn app.main:app --host 127.0.0.1 --port 8080

Use an SSH tunnel to view it from your laptop:
  ssh -L 8080:127.0.0.1:8080 <user>@<MI300X-host>
Then open http://127.0.0.1:8080. Do not expose ports 8000 or 8080 publicly.
EOF
