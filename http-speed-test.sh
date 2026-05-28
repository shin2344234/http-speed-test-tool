#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
SCRIPT="$SCRIPT_DIR/http_speed_test.py"

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SCRIPT" "$@"
fi

if command -v python >/dev/null 2>&1; then
  exec python "$SCRIPT" "$@"
fi

echo "Python was not found. Install Python 3.9+ with your package manager." >&2
exit 1
