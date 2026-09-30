#!/usr/bin/env bash
# Corre la suite de tests. Usa pytest si está; si no, cae a unittest (stdlib),
# así corre en cualquier lado (incluso Termux sin pip). Requiere node para el
# puente del motor JS.
set -eu
cd "$(dirname "$0")"

command -v node >/dev/null 2>&1 || { echo "falta node (para el motor JS)"; exit 1; }

python3 tests/gen_reference.py            # (re)genera el golden-master

if python3 -c "import pytest" 2>/dev/null; then
  exec python3 -m pytest
else
  echo "(pytest no instalado — uso unittest de la stdlib)"
  exec python3 -m unittest discover -s tests -p "test_*.py" -v
fi
