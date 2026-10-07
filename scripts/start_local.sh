#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python || ! -f frontend/dist/index.html ]]; then
  echo "请先按 docs/LOCAL_DEVELOPMENT_V0_6.md 安装依赖并构建前端。"
  exit 1
fi
exec .venv/bin/python -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8878 --workers 1 --no-access-log
