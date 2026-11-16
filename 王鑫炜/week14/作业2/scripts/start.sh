#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then cp .env.example .env; chmod 600 .env; fi
venv_path="$PWD/.venv"
if [[ ! -x "$venv_path/bin/python" && -x ../.venv/bin/python ]]; then
  venv_path="$(cd .. && pwd)/.venv"
fi
if [[ ! -x "$venv_path/bin/python" ]]; then
  python3 -m venv "$venv_path"
  "$venv_path/bin/python" -m pip install -r requirements.txt
fi
export PATH="$venv_path/bin:$PATH"
docker compose up -d --wait redis kafka
mkdir -p runtime
python -m app.worker >runtime/worker.log 2>&1 &
worker_pid=$!
cleanup() { kill "$worker_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
echo '文档问答：http://127.0.0.1:8014'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8014
