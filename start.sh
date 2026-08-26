#!/usr/bin/env bash
# Soft IP v4 开发启动：后端 :8000 + 前端 :5173
set -e
cd "$(dirname "$0")"

VENV=${VENV:-/Users/zhaoyirui/.workbuddy/binaries/python/envs/default}
NODE=/Users/zhaoyirui/.workbuddy/binaries/node/versions/22.22.2/bin

echo "== 启动后端 (FastAPI :8000, USE_MOCK=${USE_MOCK:-True}) =="
(cd backend && USE_MOCK=${USE_MOCK:-True} $VENV/bin/python -m uvicorn main:app --reload --port 8000) &
BACK_PID=$!

echo "== 启动前端 (Vite :5173) =="
(cd frontend && $NODE/npm run dev) &
FRONT_PID=$!

trap "kill $BACK_PID $FRONT_PID 2>/dev/null" EXIT
wait
