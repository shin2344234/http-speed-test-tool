#!/usr/bin/env sh
set -eu

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/share/http-speed-test-tool}"
REPO_RAW_BASE="${REPO_RAW_BASE:-https://raw.githubusercontent.com/shin2344234/http-speed-test-tool/main}"
BACKGROUND=0
QUIET=0

usage() {
  cat <<EOF
Usage: install-and-run-linux-server.sh [options]

Downloads the HTTP speed test tool, installs Python 3 if needed, and starts
the HTTP speed test server.

Options:
  --host HOST           Interface to bind. Default: $HOST
  --port PORT           TCP port to listen on. Default: $PORT
  --install-dir DIR     Install folder. Default: $INSTALL_DIR
  --repo-raw-base URL   Raw GitHub base URL. Default: $REPO_RAW_BASE
  --background          Start the server with nohup and return
  --quiet               Suppress per-request server logging
  --help                Show this help

Environment variables can also set HOST, PORT, INSTALL_DIR, and REPO_RAW_BASE.
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

step() {
  echo ""
  echo "==> $*"
}

run_privileged() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    die "Need root privileges to install packages. Re-run as root or install sudo."
  fi
}

is_compatible_python() {
  candidate="$1"
  "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

find_python() {
  for name in python3 python; do
    if command -v "$name" >/dev/null 2>&1; then
      candidate="$(command -v "$name")"
      if is_compatible_python "$candidate"; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

install_python() {
  step "Installing Python 3 and download prerequisites"

  if command -v apt-get >/dev/null 2>&1; then
    run_privileged apt-get update
    run_privileged apt-get install -y python3 ca-certificates curl
  elif command -v dnf >/dev/null 2>&1; then
    run_privileged dnf install -y python3 ca-certificates curl
  elif command -v yum >/dev/null 2>&1; then
    run_privileged yum install -y python3 ca-certificates curl
  elif command -v zypper >/dev/null 2>&1; then
    run_privileged zypper --non-interactive install python3 ca-certificates curl
  elif command -v apk >/dev/null 2>&1; then
    run_privileged apk add --no-cache python3 ca-certificates curl
  elif command -v pacman >/dev/null 2>&1; then
    run_privileged pacman -Sy --noconfirm python ca-certificates curl
  else
    die "No supported package manager found. Install Python 3.9+ manually and rerun."
  fi
}

download_file() {
  url="$1"
  destination="$2"

  echo "Downloading $url"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$destination"
  elif command -v wget >/dev/null 2>&1; then
    wget -q "$url" -O "$destination"
  else
    die "Neither curl nor wget is installed."
  fi
}

start_server_foreground() {
  if [ "$QUIET" -eq 1 ]; then
    exec "$PYTHON_EXE" "$TOOL_PATH" server --host "$HOST" --port "$PORT" --quiet
  fi
  exec "$PYTHON_EXE" "$TOOL_PATH" server --host "$HOST" --port "$PORT"
}

start_server_background() {
  pid_file="$INSTALL_DIR/server.pid"
  log_file="$INSTALL_DIR/server.log"

  if [ -f "$pid_file" ]; then
    old_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && kill -0 "$old_pid" >/dev/null 2>&1; then
      echo "Server is already running with PID $old_pid."
      echo "Log: $log_file"
      exit 0
    fi
  fi

  if [ "$QUIET" -eq 1 ]; then
    nohup "$PYTHON_EXE" "$TOOL_PATH" server --host "$HOST" --port "$PORT" --quiet >"$log_file" 2>&1 &
  else
    nohup "$PYTHON_EXE" "$TOOL_PATH" server --host "$HOST" --port "$PORT" >"$log_file" 2>&1 &
  fi

  server_pid="$!"
  echo "$server_pid" >"$pid_file"
  sleep 1

  if kill -0 "$server_pid" >/dev/null 2>&1; then
    echo "Server started with PID $server_pid."
    echo "URL: http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT"
    echo "PID file: $pid_file"
    echo "Log: $log_file"
  else
    echo "Server failed to start. Last log lines:" >&2
    tail -n 20 "$log_file" >&2 || true
    exit 1
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --host)
      [ "$#" -ge 2 ] || die "--host requires a value"
      HOST="$2"
      shift 2
      ;;
    --port)
      [ "$#" -ge 2 ] || die "--port requires a value"
      PORT="$2"
      shift 2
      ;;
    --install-dir)
      [ "$#" -ge 2 ] || die "--install-dir requires a value"
      INSTALL_DIR="$2"
      shift 2
      ;;
    --repo-raw-base)
      [ "$#" -ge 2 ] || die "--repo-raw-base requires a value"
      REPO_RAW_BASE="$2"
      shift 2
      ;;
    --background)
      BACKGROUND=1
      shift
      ;;
    --quiet)
      QUIET=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

case "$PORT" in
  ''|*[!0-9]*)
    die "Port must be a number."
    ;;
esac

if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  die "Port must be between 1 and 65535."
fi

step "Checking Python"
if PYTHON_EXE="$(find_python)"; then
  :
else
  install_python
  PYTHON_EXE="$(find_python)" || die "Python 3.9+ was not found after installation."
fi

python_version="$("$PYTHON_EXE" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
echo "Using Python $python_version: $PYTHON_EXE"

step "Creating install folder"
mkdir -p "$INSTALL_DIR"

step "Downloading HTTP speed test files"
download_file "$REPO_RAW_BASE/http_speed_test.py" "$INSTALL_DIR/http_speed_test.py"
download_file "$REPO_RAW_BASE/http-speed-test.sh" "$INSTALL_DIR/http-speed-test.sh"
download_file "$REPO_RAW_BASE/README.md" "$INSTALL_DIR/README.md"
chmod +x "$INSTALL_DIR/http-speed-test.sh"

TOOL_PATH="$INSTALL_DIR/http_speed_test.py"

step "Starting server"
echo "Listening on $HOST:$PORT"
if [ "$BACKGROUND" -eq 1 ]; then
  start_server_background
else
  start_server_foreground
fi
