#!/bin/sh

if [ -z "${BASH_VERSION:-}" ]; then
  if command -v bash >/dev/null 2>&1; then
    exec bash "$0" "$@"
  fi
  echo "bash is required to run this script" >&2
  exit 1
fi

set -euo pipefail

SERVICE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SERVICE_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "${CONFIG_PATH:-}" ]]; then
  if [[ -f "$SERVICE_DIR/config.json" ]]; then
    CONFIG_PATH="$SERVICE_DIR/config.json"
  else
    CONFIG_PATH="$PROJECT_ROOT/config.json"
  fi
fi
if [[ -z "${LOG_DIR:-}" ]]; then
  if [[ "$CONFIG_PATH" == "$SERVICE_DIR/config.json" ]]; then
    LOG_DIR="$SERVICE_DIR/logs"
  else
    LOG_DIR="$PROJECT_ROOT/logs"
  fi
fi
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8880}"
if [[ -z "${PID_FILE:-}" ]]; then
  if [[ "$CONFIG_PATH" == "$SERVICE_DIR/config.json" ]]; then
    PID_FILE="$SERVICE_DIR/mail_bridge_8880.pid"
  else
    PID_FILE="$PROJECT_ROOT/mail_bridge_8880.pid"
  fi
fi
if [[ -z "${OUT_FILE:-}" ]]; then
  if [[ "$CONFIG_PATH" == "$SERVICE_DIR/config.json" ]]; then
    OUT_FILE="$SERVICE_DIR/mail_bridge_8880.out"
  else
    OUT_FILE="$PROJECT_ROOT/mail_bridge_8880.out"
  fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    echo "python3/python not found" >&2
    exit 1
  fi
fi

SERVER_SCRIPT="$SERVICE_DIR/mail_bridge_server.py"

if [[ ! -f "$SERVER_SCRIPT" ]]; then
  echo "missing file: $SERVER_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "missing config: $CONFIG_PATH" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(tr -d '[:space:]' < "$PID_FILE" || true)"
  if [[ -n "${old_pid:-}" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "mail bridge already running pid=$old_pid"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

echo "starting mail bridge"
echo "python: $PYTHON_BIN"
echo "config: $CONFIG_PATH"
echo "listen: $HOST:$PORT"
echo "stdout: $OUT_FILE"

nohup "$PYTHON_BIN" "$SERVER_SCRIPT" \
  --host "$HOST" \
  --port "$PORT" \
  --config "$CONFIG_PATH" \
  --log-dir "$LOG_DIR" \
  > "$OUT_FILE" 2>&1 &

new_pid=$!
echo "$new_pid" > "$PID_FILE"
sleep 1

if kill -0 "$new_pid" 2>/dev/null; then
  echo "mail bridge started pid=$new_pid"
  exit 0
fi

echo "mail bridge failed to start" >&2
tail -n 50 "$OUT_FILE" 2>/dev/null || true
rm -f "$PID_FILE"
exit 1
