#!/usr/bin/env python3
"""Timestamped backup of the mail bridge SQLite DB + config.

Uses the stdlib sqlite3 online-backup API, which is WAL-safe and consistent
even while the server is running (no need to stop it). No third-party deps.

Usage:
    python backup.py                       # backs up ./data/* (or ./*) -> ./backups/
    python backup.py --db path --config path --out-dir path

ponytail: keeps the last --keep backups (default 30) and prunes older ones.
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))


def _default(name: str) -> str:
    # Prefer the docker/data layout, fall back to the project root.
    for base in ("data", "."):
        candidate = Path(base) / name
        if candidate.exists():
            return str(candidate)
    return str(Path("data") / name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup mail bridge DB + config")
    parser.add_argument("--db", default=os.environ.get("MAIL_BRIDGE_DB", _default("mail_bridge.sqlite3")))
    parser.add_argument("--config", default=os.environ.get("MAIL_BRIDGE_CONFIG", _default("config.json")))
    parser.add_argument("--out-dir", default="backups")
    parser.add_argument("--keep", type=int, default=30, help="how many recent backups to retain")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 1

    stamp = datetime.now(BEIJING).strftime("%Y%m%d_%H%M%S")
    dest_dir = Path(args.out_dir) / f"backup_{stamp}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # WAL-safe consistent copy via the online backup API.
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(dest_dir / "mail_bridge.sqlite3"))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    config_path = Path(args.config)
    if config_path.exists():
        shutil.copy2(config_path, dest_dir / "config.json")

    # Prune old backups, keeping the most recent --keep.
    backups = sorted(Path(args.out_dir).glob("backup_*"), key=lambda p: p.name, reverse=True)
    for stale in backups[max(0, args.keep):]:
        shutil.rmtree(stale, ignore_errors=True)

    print(f"backup written: {dest_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
