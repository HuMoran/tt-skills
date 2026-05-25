#!/usr/bin/env bash
# Bootstrap a venv for the rs485-modbus skill. Idempotent.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$SKILL_DIR/.venv"

if [[ ! -d "$VENV" ]]; then
    echo "[setup] creating venv at $VENV"
    python3 -m venv "$VENV"
fi

"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$SKILL_DIR/scripts/requirements.txt"

echo "[setup] OK — pyserial: $("$VENV/bin/python" -c 'import serial; print(serial.__version__)')"
echo "[setup] use: $VENV/bin/python $SKILL_DIR/scripts/modbus.py --help"
