#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

api_port=${DARECHY_API_PORT:-8013}
web_port=${DARECHY_WEB_PORT:-5174}
if [[ ! -x backend/.venv/bin/uvicorn ]]; then
  echo 'Настройте окружение backend/.venv по README.md.' >&2
  exit 1
fi
if [[ ! -d frontend/node_modules ]]; then
  (cd frontend && npm ci)
fi
if ! curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:11434/api/version >/dev/null; then
  echo 'Запустите Ollama и повторите запуск. Для поиска и генерации нужны bge-m3 и qwen3:4b-instruct-2507-q4_K_M.' >&2
  exit 1
fi

(cd backend && exec env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$api_port") &
api_pid=$!
trap 'kill "$api_pid" 2>/dev/null || true' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

ready=false
for ((attempt=0; attempt<30; attempt++)); do
  kill -0 "$api_pid" 2>/dev/null || exit 1
  if curl --noproxy '*' -fsS --max-time 1 "http://127.0.0.1:$api_port/health" >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  echo 'API не успел запуститься. Проверьте вывод выше.' >&2
  exit 1
fi

cd frontend
DARECHY_API_TARGET="http://127.0.0.1:$api_port" npm run dev -- --host 127.0.0.1 --port "$web_port" --strictPort
