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
if [[ -z "${PID_FILE:-}" ]]; then
  if [[ -f "$SERVICE_DIR/config.json" ]]; then
    PID_FILE="$SERVICE_DIR/mail_bridge_8880.pid"
  else
    PID_FILE="$PROJECT_ROOT/mail_bridge_8880.pid"
  fi
fi

if [[ ! -f "$PID_FILE" ]]; then
  echo "mail bridge pid file not found"
  exit 0
fi

pid="$(tr -d '[:space:]' < "$PID_FILE" || true)"

if [[ -z "$pid" ]]; then
  rm -f "$PID_FILE"
  echo "mail bridge pid file empty, cleaned"
  exit 0
fi

if kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  sleep 1
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" || true
  fi
  echo "mail bridge stopped pid=$pid"
else
  echo "mail bridge process not running pid=$pid"
fi

rm -f "$PID_FILE"
