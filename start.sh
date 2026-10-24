#!/usr/bin/env bash
# Startet digitizeMyBook (beendet vorher laufende Instanzen auf 8765 / 5173).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

BACKEND_PORT=8765
DEV_PORT=5173
MODE="${1:-}"

stop_listeners_on_port() {
  local port="$1"
  local pids
  pids="$(lsof -t -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "${pids}" ]]; then
    return 0
  fi
  echo "Beende Prozess(e) auf Port ${port}: ${pids}"
  # shellcheck disable=SC2086
  kill ${pids} 2>/dev/null || true
  sleep 1
  # shellcheck disable=SC2086
  kill -9 ${pids} 2>/dev/null || true
}

echo "Stoppe alte digitizeMyBook-Instanzen …"
stop_listeners_on_port "${BACKEND_PORT}"
stop_listeners_on_port "${DEV_PORT}"

if [[ ! -d .venv ]]; then
  echo "Kein Python-venv (.venv). Einmalig einrichten mit: ./run.sh"
  echo "(Abbruch nach der Installation mit Ctrl+C ist OK – danach ./start.sh)"
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
export PYTHONPATH="${ROOT}/backend"

if [[ ! -d frontend/node_modules ]]; then
  echo "Frontend-Abhängigkeiten fehlen – npm install …"
  (cd frontend && npm install)
fi

if [[ "${MODE}" == "--dev" ]]; then
  echo "Entwicklungsmodus: Backend ${BACKEND_PORT}, Vite ${DEV_PORT}"
  python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port "${BACKEND_PORT}" &
  BACK_PID=$!
  trap 'kill "${BACK_PID}" 2>/dev/null || true' EXIT
  (cd frontend && npm run dev -- --host 0.0.0.0 --port "${DEV_PORT}")
  exit 0
fi

echo "Baue Frontend …"
(cd frontend && npm run build)

echo ""
echo "digitizeMyBook läuft auf http://127.0.0.1:${BACKEND_PORT}"
echo "Im LAN: siehe GPU-Zeile in der App nach dem Start."
echo "Beenden mit Ctrl+C"
echo ""

exec python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port "${BACKEND_PORT}"
