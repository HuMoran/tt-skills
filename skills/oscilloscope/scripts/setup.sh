#!/usr/bin/env bash
# Bootstrap a venv for the bundled scope.py.
# Idempotent: safe to re-run; exits early if venv already works.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$SKILL_DIR/.venv"
PY="$VENV/bin/python"

# If venv already imports pyvisa, we're done.
if [[ -x "$PY" ]] && "$PY" -c 'import pyvisa, usb.core' 2>/dev/null; then
  echo "venv ready: $VENV"
  exit 0
fi

# Pick a Python interpreter. Prefer Homebrew's pinned 3.x, fall back to system python3.
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PY_BOOT="$candidate"
    break
  fi
done
: "${PY_BOOT:?need a python3 in PATH}"

echo "creating venv with $PY_BOOT → $VENV"
"$PY_BOOT" -m venv "$VENV"
PIP="$VENV/bin/pip"
"$PIP" install --quiet --upgrade pip
"$PIP" install --quiet -r "$SKILL_DIR/scripts/requirements.txt"

# pyvisa-py needs a USB backend. Homebrew's libusb is the usual one on macOS.
# We don't install libusb here (system-level); just verify it's loadable.
if ! "$PY" -c 'import usb.core; usb.core.find()' >/dev/null 2>&1; then
  echo "WARNING: pyusb cannot find a libusb backend." >&2
  echo "  macOS:   brew install libusb" >&2
  echo "  Debian:  sudo apt install libusb-1.0-0" >&2
  echo "  Verify:  $PY -c 'import usb.core; print(usb.core.find())'" >&2
fi

echo "ok: $PY $SKILL_DIR/scripts/scope.py --help"
