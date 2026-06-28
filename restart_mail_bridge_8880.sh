#!/bin/sh
# Linux/macOS: 重启 = 先停后起,复用现有脚本
DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
sh "$DIR/stop_mail_bridge_8880.sh" "$@"
exec sh "$DIR/start_mail_bridge_8880.sh" "$@"
