#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3.12 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install -q -r backend/requirements.txt
python -m pip install -q python-slugify

if ! python -c "import paddle" >/dev/null 2>&1; then
  echo "Installiere PaddlePaddle GPU (cu129) …"
  python -m pip install paddlepaddle-gpu==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu129/
fi
if ! python -c "import paddleocr" >/dev/null 2>&1; then
  echo "Installiere PaddleOCR …"
  python -m pip install -U "paddleocr[doc-parser]"
fi

if [[ ! -d frontend/node_modules ]]; then
  (cd frontend && npm install)
fi

export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
export PYTHONPATH="$ROOT/backend"
python -m app.cli check-gpu || true

uvicorn_cmd=(python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8765)
"${uvicorn_cmd[@]}" &
BACK_PID=$!
trap 'kill "$BACK_PID" 2>/dev/null || true' EXIT

(cd frontend && npm run dev -- --host 0.0.0.0 --port 5173)
