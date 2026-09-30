#!/usr/bin/env bash
# Rivendel · Payoff Lab — arranque.
# En Termux corrélo como:  bash run.sh
set -eu

cd "$(dirname "$0")"

PY="${PY:-python3}"
PORT="${PORT:-8765}"
HOST="${HOST:-127.0.0.1}"

command -v "$PY" >/dev/null 2>&1 || {
  echo "No encuentro '$PY'. En Termux:  pkg install python" >&2
  exit 1
}

if "$PY" -c 'import yfinance' 2>/dev/null; then
  echo "yfinance: ok ($("$PY" -c 'import yfinance;print(yfinance.__version__)'))"
else
  cat <<'EOF'
yfinance no está instalado. El lab arranca igual: el servidor cae a la API
pública de Yahoo por HTTP, que no necesita nada más que la stdlib.

Para instalarlo en Termux (pandas/numpy compilan lento, mejor por pkg):
    pkg install python numpy
    pip install yfinance
EOF
fi

URL="http://${HOST}:${PORT}"
[ "$HOST" = "0.0.0.0" ] && URL="http://$(hostname -i 2>/dev/null | awk '{print $1}'):${PORT}"

# En Termux, abre el navegador del teléfono solo.
if command -v termux-open-url >/dev/null 2>&1; then
  ( sleep 2; termux-open-url "http://localhost:${PORT}" ) &
fi

exec "$PY" server.py --host "$HOST" --port "$PORT"
