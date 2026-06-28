#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import base64
import csv
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import getaddresses
import hashlib
from html.parser import HTMLParser
from http.cookies import SimpleCookie
import json
import logging
import io
import os
import re
import secrets
import sqlite3
import string
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse


SERVICE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SERVICE_ROOT.parent


def resolve_runtime_root(service_root: Path, legacy_root: Path) -> Path:
    service_config = service_root / "config.json"
    legacy_config = legacy_root / "config.json"
    if service_config.exists():
        return service_root
    if legacy_config.exists():
        return legacy_root
    return service_root


RUNTIME_ROOT = resolve_runtime_root(SERVICE_ROOT, PROJECT_ROOT)
DEFAULT_CONFIG_PATH = RUNTIME_ROOT / "config.json"
DEFAULT_LOG_DIR = RUNTIME_ROOT / "logs"
DEFAULT_DB_PATH = RUNTIME_ROOT / "mail_bridge.sqlite3"
FORCED_NEW_ADDRESS_DOMAIN = "52moyu.net"
ICLOUD_FORWARD_ALIAS_ADDRESS = "icloud@52moyu.net"
SESSION_COOKIE_NAME = "mail_bridge_session"
DEFAULT_SESSION_TTL_SECONDS = 60 * 60 * 24 * 7
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 300
REDEEM_MAX_FAILURES = 20
REDEEM_WINDOW_SECONDS = 300
MAX_REQUEST_BODY_BYTES = 10 * 1024 * 1024  # 10 MiB — generous for inbound mail, caps DoS
MAX_PASSWORD_LENGTH = 256  # cap pbkdf2 input
MAX_USERNAME_LENGTH = 64
PASSWORD_HASH_PREFIX = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 200_000
DEFAULT_ADMIN_USERNAME = "admin"
# No hardcoded default password: if admin creds aren't configured, the admin is
# created on first run via the web setup page (/web/admin/login → 首次设置).
MAILBOX_ACCESS_KEY_LENGTH = 12
PUBLIC_QUERY_PAGE_SIZE = 20
ADMIN_MAILBOX_PAGE_SIZE = 20
ADMIN_INBOX_PAGE_SIZE = 20
BEIJING_TIMEZONE = timezone(timedelta(hours=8))

CODE_PATTERNS = (
    r"Subject:\s*Your ChatGPT code is\s*(\d{6})",
    r"Your ChatGPT code is\s*(\d{6})",
    r"temporary verification code to continue:\s*(\d{6})",
    r"(?<![#&])\b(\d{6})\b",
)

INVITE_PRIMARY_MARKERS = (
    "加入工作空间",
    "join workspace",
    "accept invitation",
    "accept the invitation",
)

INVITE_CONTEXT_MARKERS = (
    "已邀请你",
    "邀请你",
    "invited you",
    "workspace",
    "工作空间",
    "team",
    "chatgpt business",
    "chatgpt team",
)

INVITE_LINK_HINTS = (
    "join",
    "invite",
    "accept",
    "workspace",
    "team",
    "openai.com",
    "chatgpt.com",
)

URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.I)
EMAIL_ADDRESS_PATTERN = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc_iso(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_beijing_time(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = parse_utc_iso(raw)
    if not parsed:
        return raw
    return parsed.astimezone(BEIJING_TIMEZONE).isoformat(timespec="seconds")


def decode_mime_header_value(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:
        return raw


def load_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def resolve_shared_token(config_path: Path, explicit_token: str = "") -> str:
    if explicit_token.strip():
        return explicit_token.strip()
    config = load_json_file(config_path)
    mail_conf = config.get("mail") if isinstance(config.get("mail"), dict) else {}
    token = str(mail_conf.get("api_key") or "").strip()
    if token:
        return token
    return "CHANGE_ME_MAIL_BRIDGE_TOKEN"


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def setup_logger(log_dir: Path) -> logging.Logger:
    ensure_parent_dir(log_dir / "mail_bridge.log")
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("mail_bridge")
    if logger.handlers:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    file_handler = logging.FileHandler(log_dir / "mail_bridge.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


def normalize_address(value: Any) -> str:
    return str(value or "").strip().lower()


def is_valid_email_address(value: Any) -> bool:
    normalized = normalize_address(value)
    return bool(normalized and EMAIL_ADDRESS_PATTERN.fullmatch(normalized))


def extract_email_addresses(value: Any) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []

    results: list[str] = []
    seen: set[str] = set()

    def append_candidate(candidate: Any) -> None:
        normalized = normalize_address(candidate)
        if normalized and normalized not in seen:
            seen.add(normalized)
            results.append(normalized)

    try:
        for _, address in getaddresses([raw]):
            append_candidate(address)
    except Exception:
        pass

    if results:
        return results

    for match in re.findall(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", raw, re.I):
        append_candidate(match)
    return results


def get_header_value(headers: Any, *names: str) -> str:
    if not isinstance(headers, dict):
        return ""
    lowered = {str(key or "").strip().lower(): value for key, value in headers.items()}
    for name in names:
        if name.lower() in lowered:
            return str(lowered.get(name.lower()) or "").strip()
    return ""


def resolve_effective_recipient_address(address: Any, headers: Any) -> str:
    normalized = normalize_address(address)
    if normalized != ICLOUD_FORWARD_ALIAS_ADDRESS:
        return normalized

    for header_name in ("to",):
        candidates = [item for item in extract_email_addresses(get_header_value(headers, header_name)) if item]
        non_alias_candidates = [item for item in candidates if item != normalized]
        if len(non_alias_candidates) == 1:
            return non_alias_candidates[0]
    return normalized


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def normalize_username(value: Any) -> str:
    return str(value or "").strip().lower()


def build_password_hash(password: str, *, iterations: int = PASSWORD_HASH_ITERATIONS) -> str:
    secret = str(password or "")
    if not secret:
        raise ValueError("empty_password")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, int(iterations))
    salt_b64 = base64.b64encode(salt).decode("ascii")
    digest_b64 = base64.b64encode(digest).decode("ascii")
    return f"{PASSWORD_HASH_PREFIX}${int(iterations)}${salt_b64}${digest_b64}"


def verify_password(password: str, encoded_hash: str) -> bool:
    value = str(encoded_hash or "").strip()
    parts = value.split("$")
    if len(parts) != 4:
        return False
    scheme, iterations_raw, salt_b64, digest_b64 = parts
    if scheme != PASSWORD_HASH_PREFIX:
        return False
    try:
        iterations = int(iterations_raw)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(digest_b64.encode("ascii"))
    except Exception:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", str(password or "").encode("utf-8"), salt, iterations)
    return secrets.compare_digest(candidate, expected)


def generate_access_key(length: int = MAILBOX_ACCESS_KEY_LENGTH) -> str:
    size = max(8, int(length or MAILBOX_ACCESS_KEY_LENGTH))
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(size))


# Unambiguous alphabet (no O/0/I/1) for human-readable CDK redemption codes.
CDK_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_cdk_code() -> str:
    groups = ["".join(secrets.choice(CDK_CODE_ALPHABET) for _ in range(5)) for _ in range(4)]
    return "CDK-" + "-".join(groups)


def parse_mailbox_credential(raw_value: Any) -> tuple[str, str]:
    raw = str(raw_value or "").strip()
    if not raw or "----" not in raw:
        return "", ""
    address_raw, key_raw = raw.split("----", 1)
    return normalize_address(address_raw), str(key_raw or "").strip()


def strip_html_tags(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    return re.sub(r"<[^>]+>", " ", text)


def build_mail_preview_text(subject: str, text: str, html: str, body: str, verification_code: str, invite_link: str) -> str:
    if verification_code:
        return f"验证码：{verification_code}"
    if invite_link:
        return f"邀请链接：{invite_link}"
    candidates = [text, strip_html_tags(html), body, subject]
    for candidate in candidates:
        normalized = re.sub(r"\s+", " ", str(candidate or "").strip())
        if normalized:
            return normalized[:160]
    return ""


def extract_verification_code(content: str) -> str:
    text = str(content or "")
    for pattern in CODE_PATTERNS:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            code = match.group(1)
            if code != "177010":
                return code
    return ""


def classify_mail_type(subject: str, text: str, html: str, body: str) -> str:
    merged = normalize_text("\n".join(part for part in (subject, text, html, body) if part))
    if any(marker in merged for marker in INVITE_PRIMARY_MARKERS):
        return "team_invite"
    if ("chatgpt business" in merged or "workspace" in merged or "工作空间" in merged) and any(
        marker in merged for marker in INVITE_CONTEXT_MARKERS
    ):
        return "team_invite"
    if extract_verification_code(merged):
        return "verification_code"
    return "unknown"


class InviteAnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._active_href = ""
        self._active_parts: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag.lower() != "a":
            return
        href = ""
        for key, value in attrs:
            if key.lower() == "href" and value:
                href = value.strip()
                break
        self._active_href = href
        self._active_parts = []

    def handle_data(self, data: str) -> None:
        if self._active_href:
            self._active_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._active_href:
            return
        label = re.sub(r"\s+", " ", "".join(self._active_parts)).strip()
        self.links.append((self._active_href, label))
        self._active_href = ""
        self._active_parts = []


def score_invite_link(href: str, label: str) -> int:
    normalized_href = normalize_text(href)
    normalized_label = normalize_text(label)
    haystack = f"{normalized_label} {normalized_href}".strip()
    if not haystack:
        return -1

    score = 0
    if any(marker in normalized_label for marker in INVITE_PRIMARY_MARKERS):
        score += 10
    if any(marker in normalized_href for marker in INVITE_LINK_HINTS):
        score += 6
    if any(marker in haystack for marker in INVITE_CONTEXT_MARKERS):
        score += 3
    if "openai.com" in normalized_href or "chatgpt.com" in normalized_href:
        score += 2
    return score


def extract_invite_link(html: str, text: str, body: str) -> str:
    parser = InviteAnchorParser()
    try:
        parser.feed(str(html or ""))
    except Exception:
        parser.links = []

    best_link = ""
    best_score = -1
    for href, label in parser.links:
        score = score_invite_link(href, label)
        if score > best_score:
            best_score = score
            best_link = href.strip()

    if best_link:
        return best_link

    candidates = URL_PATTERN.findall("\n".join(part for part in (text, body, html) if part))
    for candidate in candidates:
        score = score_invite_link(candidate, "")
        if score > best_score:
            best_score = score
            best_link = candidate.strip().rstrip(").,]>\"'")
    return best_link


def extract_raw_header_text(raw_mail: str) -> str:
    source = str(raw_mail or "")
    if not source:
        return ""
    match = re.search(r"\r?\n\r?\n", source)
    if match:
        return source[: match.start()]
    return source


@dataclass
class MailRecord:
    message_id: int
    address: str
    from_address: str
    subject: str
    text: str
    html: str
    body: str
    raw_headers: str
    raw_mail: str
    raw_header_text: str
    received_at: str
    verification_code: str
    mail_type: str
    invite_link: str
    process_status: str
    processed_at: str
    process_note: str


@dataclass
class UserRecord:
    user_id: int
    username: str
    role: str
    active: bool
    created_at: str


@dataclass
class MailboxCredentialRecord:
    mailbox_id: int
    address: str
    access_key: str
    active: bool
    created_at: str
    updated_at: str
    note: str


@dataclass
class MailboxTagRecord:
    tag_id: int
    name: str
    created_at: str
    mailbox_count: int = 0


class MailBridgeStore:
    def __init__(self, db_path: Path, logger: Optional[logging.Logger] = None):
        ensure_parent_dir(db_path)
        self.db_path = db_path
        self.logger = logger
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address TEXT NOT NULL,
                    from_address TEXT NOT NULL DEFAULT '',
                    subject TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    html TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    raw_headers TEXT NOT NULL DEFAULT '',
                    raw_mail TEXT NOT NULL DEFAULT '',
                    raw_header_text TEXT NOT NULL DEFAULT '',
                    received_at TEXT NOT NULL DEFAULT '',
                    verification_code TEXT NOT NULL DEFAULT '',
                    mail_type TEXT NOT NULL DEFAULT '',
                    invite_link TEXT NOT NULL DEFAULT '',
                    process_status TEXT NOT NULL DEFAULT 'pending',
                    processed_at TEXT NOT NULL DEFAULT '',
                    process_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_messages_address_id
                ON messages(address, id DESC);
                CREATE INDEX IF NOT EXISTS idx_messages_invite_pending
                ON messages(address, mail_type, process_status, id ASC);

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'user',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_users_role
                ON users(role);

                CREATE TABLE IF NOT EXISTS user_mailboxes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    address TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, address),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_user_mailboxes_address
                ON user_mailboxes(address);

                CREATE TABLE IF NOT EXISTS mailbox_credentials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address TEXT NOT NULL UNIQUE,
                    access_key TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'available',
                    owner_user_id INTEGER NOT NULL DEFAULT 0,
                    sold_at TEXT NOT NULL DEFAULT '',
                    order_id INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_mailbox_credentials_address
                ON mailbox_credentials(address);

                CREATE TABLE IF NOT EXISTS mailbox_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_mailbox_tags_name
                ON mailbox_tags(name);

                CREATE TABLE IF NOT EXISTS mailbox_tag_links (
                    mailbox_id INTEGER NOT NULL,
                    tag_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(mailbox_id, tag_id),
                    FOREIGN KEY(mailbox_id) REFERENCES mailbox_credentials(id) ON DELETE CASCADE,
                    FOREIGN KEY(tag_id) REFERENCES mailbox_tags(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_mailbox_tag_links_mailbox
                ON mailbox_tag_links(mailbox_id);
                CREATE INDEX IF NOT EXISTS idx_mailbox_tag_links_tag
                ON mailbox_tag_links(tag_id);

                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL DEFAULT '',
                    last_seen_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    revoked_at TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_lookup
                ON sessions(token_hash, expires_at, revoked_at);

                CREATE TABLE IF NOT EXISTS cdks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    tag_id INTEGER NOT NULL DEFAULT 0,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    max_uses INTEGER NOT NULL DEFAULT 1,
                    used_count INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    batch_label TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL DEFAULT '',
                    created_by INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_cdks_code ON cdks(code);
                CREATE INDEX IF NOT EXISTS idx_cdks_tag ON cdks(tag_id, active);

                CREATE TABLE IF NOT EXISTS cdk_redemptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cdk_id INTEGER NOT NULL,
                    code TEXT NOT NULL DEFAULT '',
                    user_id INTEGER NOT NULL,
                    mailbox_ids TEXT NOT NULL DEFAULT '',
                    addresses TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'success',
                    redeemed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_cdk_redemptions_user
                ON cdk_redemptions(user_id, id DESC);
                """
            )
            existing_columns = {
                str(row["name"] or "")
                for row in self._conn.execute("PRAGMA table_info(messages)").fetchall()
            }
            for column_name, ddl in (
                ("mail_type", "TEXT NOT NULL DEFAULT ''"),
                ("invite_link", "TEXT NOT NULL DEFAULT ''"),
                ("process_status", "TEXT NOT NULL DEFAULT 'pending'"),
                ("processed_at", "TEXT NOT NULL DEFAULT ''"),
                ("process_note", "TEXT NOT NULL DEFAULT ''"),
                ("raw_mail", "TEXT NOT NULL DEFAULT ''"),
                ("raw_header_text", "TEXT NOT NULL DEFAULT ''"),
            ):
                if column_name not in existing_columns:
                    self._conn.execute(f"ALTER TABLE messages ADD COLUMN {column_name} {ddl}")
            user_columns = {
                str(row["name"] or "")
                for row in self._conn.execute("PRAGMA table_info(users)").fetchall()
            }
            for column_name, ddl in (
                ("password_hash", "TEXT NOT NULL DEFAULT ''"),
                ("role", "TEXT NOT NULL DEFAULT 'user'"),
                ("active", "INTEGER NOT NULL DEFAULT 1"),
                ("created_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
                ("updated_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
            ):
                if column_name not in user_columns:
                    self._conn.execute(f"ALTER TABLE users ADD COLUMN {column_name} {ddl}")
            mailbox_columns = {
                str(row["name"] or "")
                for row in self._conn.execute("PRAGMA table_info(mailbox_credentials)").fetchall()
            }
            for column_name, ddl in (
                ("access_key", "TEXT NOT NULL DEFAULT ''"),
                ("active", "INTEGER NOT NULL DEFAULT 1"),
                ("note", "TEXT NOT NULL DEFAULT ''"),
                ("created_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
                ("updated_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
            ):
                if column_name not in mailbox_columns:
                    self._conn.execute(f"ALTER TABLE mailbox_credentials ADD COLUMN {column_name} {ddl}")
            # Inventory/ownership columns for the email-selling platform.
            if "status" not in mailbox_columns:
                for column_name, ddl in (
                    ("status", "TEXT NOT NULL DEFAULT 'available'"),
                    ("owner_user_id", "INTEGER NOT NULL DEFAULT 0"),
                    ("sold_at", "TEXT NOT NULL DEFAULT ''"),
                    ("order_id", "INTEGER NOT NULL DEFAULT 0"),
                ):
                    if column_name not in mailbox_columns:
                        self._conn.execute(f"ALTER TABLE mailbox_credentials ADD COLUMN {column_name} {ddl}")
                # One-time backfill: mailboxes already assigned to a user are 'sold'
                # so the migration does not re-sell them as fresh stock.
                self._conn.execute(
                    """
                    UPDATE mailbox_credentials SET status='sold',
                        owner_user_id=(
                            SELECT user_id FROM user_mailboxes um
                            WHERE um.address = mailbox_credentials.address LIMIT 1
                        )
                    WHERE status='available'
                      AND address IN (SELECT address FROM user_mailboxes)
                    """
                )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mailbox_inventory ON mailbox_credentials(status, active)"
            )
            self._conn.commit()

    def save_message(self, payload: Dict[str, Any]) -> MailRecord:
        raw_headers_obj = payload.get("headers") or {}
        source_address = normalize_address(payload.get("to") or payload.get("address"))
        address = resolve_effective_recipient_address(source_address, raw_headers_obj)
        if not address:
            raise ValueError("missing recipient address")

        subject = str(payload.get("subject") or "").strip()
        text = str(payload.get("text") or "").strip()
        html = str(payload.get("html") or "").strip()
        body = str(payload.get("body") or payload.get("raw") or "").strip()
        raw_headers = json.dumps(raw_headers_obj, ensure_ascii=False, sort_keys=True)
        raw_mail_value = payload.get("raw_mail")
        if raw_mail_value is None:
            raw_mail_value = payload.get("raw")
        if raw_mail_value is None:
            raw_mail_value = payload.get("body")
        raw_mail = str(raw_mail_value or "")
        raw_header_value = payload.get("raw_header_text")
        raw_header_text = str(raw_header_value or "")
        if not raw_header_text and raw_mail:
            raw_header_text = extract_raw_header_text(raw_mail)
        received_at = str(payload.get("received_at") or utcnow_iso()).strip()
        from_address = str(payload.get("from") or payload.get("from_address") or "").strip()

        combined = "\n".join(part for part in (subject, text, html, body) if part)
        verification_code = extract_verification_code(combined)
        mail_type = classify_mail_type(subject, text, html, body)
        invite_link = extract_invite_link(html, text, body) if mail_type == "team_invite" else ""
        process_status = "pending"
        processed_at = ""
        process_note = ""

        if self.logger:
            if source_address and source_address != address:
                self.logger.info(
                    "remapped forwarded recipient: source_address=%s effective_address=%s",
                    source_address,
                    address,
                )
            self.logger.info(
                "classified inbound email: address=%s from=%s mail_type=%s has_code=%s has_invite_link=%s",
                address,
                from_address or "-",
                mail_type or "-",
                "yes" if verification_code else "no",
                "yes" if invite_link else "no",
            )

        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO messages (
                    address, from_address, subject, text, html, body, raw_headers, raw_mail, raw_header_text,
                    received_at, verification_code, mail_type, invite_link, process_status, processed_at, process_note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    address,
                    from_address,
                    subject,
                    text,
                    html,
                    body,
                    raw_headers,
                    raw_mail,
                    raw_header_text,
                    received_at,
                    verification_code,
                    mail_type,
                    invite_link,
                    process_status,
                    processed_at,
                    process_note,
                ),
            )
            self._conn.commit()
            message_id = int(cursor.lastrowid or 0)

        return MailRecord(
            message_id=message_id,
            address=address,
            from_address=from_address,
            subject=subject,
            text=text,
            html=html,
            body=body,
            raw_headers=raw_headers,
            raw_mail=raw_mail,
            raw_header_text=raw_header_text,
            received_at=format_beijing_time(received_at),
            verification_code=verification_code,
            mail_type=mail_type,
            invite_link=invite_link,
            process_status=process_status,
            processed_at=format_beijing_time(processed_at),
            process_note=process_note,
        )

    @staticmethod
    def _mail_record_from_row(row: sqlite3.Row) -> MailRecord:
        return MailRecord(
            message_id=int(row["id"] or 0),
            address=str(row["address"] or ""),
            from_address=str(row["from_address"] or ""),
            subject=str(row["subject"] or ""),
            text=str(row["text"] or ""),
            html=str(row["html"] or ""),
            body=str(row["body"] or ""),
            raw_headers=str(row["raw_headers"] or ""),
            raw_mail=str(row["raw_mail"] or ""),
            raw_header_text=str(row["raw_header_text"] or ""),
            received_at=format_beijing_time(row["received_at"]),
            verification_code=str(row["verification_code"] or ""),
            mail_type=str(row["mail_type"] or ""),
            invite_link=str(row["invite_link"] or ""),
            process_status=str(row["process_status"] or ""),
            processed_at=format_beijing_time(row["processed_at"]),
            process_note=str(row["process_note"] or ""),
        )

    def latest_message(self, address: str) -> Optional[MailRecord]:
        normalized = normalize_address(address)
        if not normalized:
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    id, address, from_address, subject, text, html, body, raw_headers, raw_mail, raw_header_text,
                    received_at, verification_code,
                    mail_type, invite_link, process_status, processed_at, process_note
                FROM messages
                WHERE address = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
        if not row:
            return None
        return self._mail_record_from_row(row)

    def list_messages(self, address: str, limit: int = 5, offset: int = 0) -> list[MailRecord]:
        normalized = normalize_address(address)
        if not normalized:
            return []
        safe_limit = max(1, min(int(limit), 200))
        safe_offset = max(0, int(offset))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    id, address, from_address, subject, text, html, body, raw_headers, raw_mail, raw_header_text,
                    received_at, verification_code,
                    mail_type, invite_link, process_status, processed_at, process_note
                FROM messages
                WHERE address = ?
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (normalized, safe_limit, safe_offset),
            ).fetchall()
        return [self._mail_record_from_row(row) for row in rows]

    def list_recent_messages(self, *, keyword: str = "", limit: int = ADMIN_INBOX_PAGE_SIZE, offset: int = 0) -> list[MailRecord]:
        safe_limit = max(1, min(int(limit or ADMIN_INBOX_PAGE_SIZE), 200))
        safe_offset = max(0, int(offset or 0))
        clean_keyword = str(keyword or "").strip().lower()
        where_sql = ""
        params: list[Any] = []
        if clean_keyword:
            where_sql = "WHERE lower(address) LIKE ? OR lower(from_address) LIKE ? OR lower(subject) LIKE ?"
            like_value = f"%{clean_keyword}%"
            params.extend([like_value, like_value, like_value])
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT
                    id, address, from_address, subject, text, html, body, raw_headers, raw_mail, raw_header_text,
                    received_at, verification_code,
                    mail_type, invite_link, process_status, processed_at, process_note
                FROM messages
                {where_sql}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                tuple([*params, safe_limit, safe_offset]),
            ).fetchall()
        return [self._mail_record_from_row(row) for row in rows]

    def count_all_messages(self, *, keyword: str = "") -> int:
        clean_keyword = str(keyword or "").strip().lower()
        where_sql = ""
        params: list[Any] = []
        if clean_keyword:
            where_sql = "WHERE lower(address) LIKE ? OR lower(from_address) LIKE ? OR lower(subject) LIKE ?"
            like_value = f"%{clean_keyword}%"
            params.extend([like_value, like_value, like_value])
        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(1) AS total FROM messages {where_sql}",
                tuple(params),
            ).fetchone()
        return int((row["total"] if row else 0) or 0)

    def count_messages(self, address: str) -> int:
        normalized = normalize_address(address)
        if not normalized:
            return 0
        with self._lock:
            row = self._conn.execute("SELECT COUNT(1) AS total FROM messages WHERE address = ?", (normalized,)).fetchone()
        return int((row["total"] if row else 0) or 0)

    def get_message_for_address(self, address: str, message_id: int) -> Optional[MailRecord]:
        normalized = normalize_address(address)
        if not normalized or message_id <= 0:
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    id, address, from_address, subject, text, html, body, raw_headers, raw_mail, raw_header_text,
                    received_at, verification_code,
                    mail_type, invite_link, process_status, processed_at, process_note
                FROM messages
                WHERE address = ? AND id = ?
                LIMIT 1
                """,
                (normalized, int(message_id)),
            ).fetchone()
        if not row:
            return None
        return self._mail_record_from_row(row)

    def get_message_by_id(self, message_id: int) -> Optional[MailRecord]:
        if int(message_id or 0) <= 0:
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    id, address, from_address, subject, text, html, body, raw_headers, raw_mail, raw_header_text,
                    received_at, verification_code,
                    mail_type, invite_link, process_status, processed_at, process_note
                FROM messages
                WHERE id = ?
                LIMIT 1
                """,
                (int(message_id),),
            ).fetchone()
        if not row:
            return None
        return self._mail_record_from_row(row)

    @staticmethod
    def _mailbox_record_from_row(row: sqlite3.Row) -> MailboxCredentialRecord:
        return MailboxCredentialRecord(
            mailbox_id=int(row["id"] or 0),
            address=normalize_address(row["address"]),
            access_key=str(row["access_key"] or ""),
            active=bool(int(row["active"] or 0)),
            created_at=format_beijing_time(row["created_at"]),
            updated_at=format_beijing_time(row["updated_at"]),
            note=str(row["note"] or ""),
        )

    @staticmethod
    def _mailbox_tag_record_from_row(row: sqlite3.Row) -> MailboxTagRecord:
        return MailboxTagRecord(
            tag_id=int(row["id"] or 0),
            name=str(row["name"] or ""),
            created_at=format_beijing_time(row["created_at"]),
            mailbox_count=int(row["mailbox_count"] or 0),
        )

    def _list_tags_for_mailbox_ids(self, mailbox_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        clean_ids = sorted({int(mailbox_id) for mailbox_id in mailbox_ids if int(mailbox_id or 0) > 0})
        if not clean_ids:
            return {}
        placeholders = ",".join("?" for _ in clean_ids)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT
                    mtl.mailbox_id AS mailbox_id,
                    mt.id AS id,
                    mt.name AS name,
                    mt.created_at AS created_at
                FROM mailbox_tag_links mtl
                INNER JOIN mailbox_tags mt ON mt.id = mtl.tag_id
                WHERE mtl.mailbox_id IN ({placeholders})
                ORDER BY mt.name COLLATE NOCASE ASC, mt.id ASC
                """,
                tuple(clean_ids),
            ).fetchall()
        grouped: dict[int, list[dict[str, Any]]] = {mailbox_id: [] for mailbox_id in clean_ids}
        for row in rows:
            mailbox_id = int(row["mailbox_id"] or 0)
            grouped.setdefault(mailbox_id, []).append(
                {
                    "id": int(row["id"] or 0),
                    "name": str(row["name"] or ""),
                    "created_at": format_beijing_time(row["created_at"]),
                }
            )
        return grouped

    def list_mailbox_credentials(
        self, *, keyword: str = "", tag_id: int = 0, status: str = "", limit: int = ADMIN_MAILBOX_PAGE_SIZE, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        normalized_keyword = normalize_address(keyword)
        safe_limit = max(1, min(int(limit or ADMIN_MAILBOX_PAGE_SIZE), 200))
        safe_offset = max(0, int(offset or 0))
        where_clauses: list[str] = []
        params: list[Any] = []
        if normalized_keyword:
            where_clauses.append("(mc.address LIKE ? OR lower(mc.note) LIKE ?)")
            params.extend([f"%{normalized_keyword}%", f"%{normalized_keyword}%"])
        clean_status = str(status or "").strip()
        if clean_status in ("presale", "available", "sold", "deleted"):
            where_clauses.append("mc.status = ?")
            params.append(clean_status)
        else:
            # Default view hides soft-deleted mailboxes.
            where_clauses.append("mc.status != 'deleted'")
        clean_tag_id = int(tag_id or 0)
        if clean_tag_id > 0:
            where_clauses.append(
                "EXISTS (SELECT 1 FROM mailbox_tag_links mtl_filter WHERE mtl_filter.mailbox_id = mc.id AND mtl_filter.tag_id = ?)"
            )
            params.append(clean_tag_id)
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        with self._lock:
            total_row = self._conn.execute(
                f"SELECT COUNT(1) AS total FROM mailbox_credentials mc {where_sql}",
                tuple(params),
            ).fetchone()
            rows = self._conn.execute(
                f"""
                SELECT
                    mc.id AS id,
                    mc.address AS address,
                    mc.access_key AS access_key,
                    mc.active AS active,
                    mc.status AS status,
                    mc.note AS note,
                    mc.created_at AS created_at,
                    mc.updated_at AS updated_at,
                    (
                        SELECT COUNT(1)
                        FROM messages m
                        WHERE m.address = mc.address
                    ) AS message_count,
                    (
                        SELECT m.received_at
                        FROM messages m
                        WHERE m.address = mc.address
                        ORDER BY m.id DESC
                        LIMIT 1
                    ) AS latest_received_at
                FROM mailbox_credentials mc
                {where_sql}
                ORDER BY mc.id ASC
                LIMIT ? OFFSET ?
                """,
                tuple([*params, safe_limit, safe_offset]),
            ).fetchall()
        tag_map = self._list_tags_for_mailbox_ids([int(row["id"] or 0) for row in rows])
        results: list[dict[str, Any]] = []
        for row in rows:
            record = self._mailbox_record_from_row(row)
            results.append(
                {
                    "id": record.mailbox_id,
                    "address": record.address,
                    "access_key": record.access_key,
                    "active": record.active,
                    "status": str(row["status"] or "available"),
                    "note": record.note,
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                    "message_count": int(row["message_count"] or 0),
                    "latest_received_at": format_beijing_time(row["latest_received_at"]),
                    "tags": tag_map.get(record.mailbox_id, []),
                }
            )
        return results, int((total_row["total"] if total_row else 0) or 0)

    def list_mailbox_credentials_for_export(self, *, keyword: str = "") -> list[MailboxCredentialRecord]:
        normalized_keyword = normalize_address(keyword)
        where_clauses = ["status != 'deleted'"]
        params: list[Any] = []
        if normalized_keyword:
            where_clauses.append("(address LIKE ? OR lower(note) LIKE ?)")
            params.extend([f"%{normalized_keyword}%", f"%{normalized_keyword}%"])
        where_sql = "WHERE " + " AND ".join(where_clauses)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, address, access_key, active, note, created_at, updated_at
                FROM mailbox_credentials
                {where_sql}
                ORDER BY created_at ASC, id ASC
                """,
                tuple(params),
            ).fetchall()
        return [self._mailbox_record_from_row(row) for row in rows]

    def create_mailbox_credential(self, address: str, *, note: str = "", tag_ids: Optional[list[Any]] = None) -> tuple[bool, Optional[MailboxCredentialRecord], str]:
        normalized_address = normalize_address(address)
        if not normalized_address:
            return False, None, "missing_address"
        if not is_valid_email_address(normalized_address):
            return False, None, "invalid_address"
        access_key = generate_access_key()
        now = utcnow_iso()
        clean_note = str(note or "").strip()
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM mailbox_credentials WHERE address = ? LIMIT 1",
                (normalized_address,),
            ).fetchone()
            if existing:
                return False, None, "address_exists"
            cursor = self._conn.execute(
                """
                INSERT INTO mailbox_credentials (address, access_key, active, status, note, created_at, updated_at)
                VALUES (?, ?, 1, 'presale', ?, ?, ?)
                """,
                (normalized_address, access_key, clean_note, now, now),
            )
            self._conn.commit()
            mailbox_id = int(cursor.lastrowid or 0)
            row = self._conn.execute(
                """
                SELECT id, address, access_key, active, note, created_at, updated_at
                FROM mailbox_credentials
                WHERE id = ?
                LIMIT 1
                """,
                (mailbox_id,),
            ).fetchone()
        if not row:
            return False, None, "create_failed"
        record = self._mailbox_record_from_row(row)
        if tag_ids:
            # Best-effort tagging: a freshly created mailbox has no prior tags,
            # so overwriting is safe; invalid tag ids simply leave it untagged.
            self.set_mailbox_tags(record.mailbox_id, tag_ids)
        return True, record, "ok"

    def bulk_create_mailbox_credentials(self, raw_text: str, *, note: str = "", tag_ids: Optional[list[Any]] = None) -> dict[str, Any]:
        lines = str(raw_text or "").splitlines()
        clean_note = str(note or "").strip()
        seen_in_request: set[str] = set()
        results: list[dict[str, Any]] = []
        created = 0
        reset = 0
        skipped = 0
        invalid = 0
        accepted_lines = 0

        for line_no, raw_line in enumerate(lines, start=1):
            source = str(raw_line or "").strip()
            if not source:
                continue
            accepted_lines += 1
            address = normalize_address(source)
            if not is_valid_email_address(address):
                invalid += 1
                results.append(
                    {
                        "line": line_no,
                        "input": source,
                        "address": address,
                        "status": "invalid",
                        "reason": "invalid_address",
                    }
                )
                continue
            if address in seen_in_request:
                skipped += 1
                results.append(
                    {
                        "line": line_no,
                        "input": source,
                        "address": address,
                        "status": "skipped",
                        "reason": "duplicate_in_request",
                    }
                )
                continue
            seen_in_request.add(address)
            ok, mailbox, reason = self.create_mailbox_credential(address, note=clean_note, tag_ids=tag_ids)
            if ok and mailbox:
                created += 1
                results.append(
                    {
                        "line": line_no,
                        "input": source,
                        "address": mailbox.address,
                        "status": "created",
                        "reason": "ok",
                        "mailbox": {
                            "id": mailbox.mailbox_id,
                            "address": mailbox.address,
                            "access_key": mailbox.access_key,
                            "credential": f"{mailbox.address}----{mailbox.access_key}",
                            "note": mailbox.note,
                            "created_at": mailbox.created_at,
                            "updated_at": mailbox.updated_at,
                            "active": mailbox.active,
                        },
                    }
                )
                continue
            if reason == "address_exists":
                with self._lock:
                    existing_row = self._conn.execute(
                        "SELECT id FROM mailbox_credentials WHERE address = ? LIMIT 1",
                        (address,),
                    ).fetchone()
                existing_id = int(existing_row["id"] or 0) if existing_row else 0
                if existing_id:
                    reset_ok, reset_mailbox, reset_reason = self.reset_mailbox_access_key(existing_id)
                else:
                    reset_ok, reset_mailbox, reset_reason = False, None, "mailbox_not_found"
                if reset_ok and reset_mailbox:
                    reset += 1
                    results.append(
                        {
                            "line": line_no,
                            "input": source,
                            "address": reset_mailbox.address,
                            "status": "reset",
                            "reason": "ok",
                            "mailbox": {
                                "id": reset_mailbox.mailbox_id,
                                "address": reset_mailbox.address,
                                "access_key": reset_mailbox.access_key,
                                "credential": f"{reset_mailbox.address}----{reset_mailbox.access_key}",
                                "note": reset_mailbox.note,
                                "created_at": reset_mailbox.created_at,
                                "updated_at": reset_mailbox.updated_at,
                                "active": reset_mailbox.active,
                            },
                        }
                    )
                    continue
                invalid += 1
                results.append(
                    {
                        "line": line_no,
                        "input": source,
                        "address": address,
                        "status": "invalid",
                        "reason": reset_reason,
                    }
                )
                continue
            invalid += 1
            results.append(
                {
                    "line": line_no,
                    "input": source,
                    "address": address,
                    "status": "invalid",
                    "reason": reason,
                }
            )

        return {
            "total_lines": len(lines),
            "accepted_lines": accepted_lines,
            "created": created,
            "reset": reset,
            "skipped": skipped,
            "invalid": invalid,
            "results": results,
        }

    def import_mailbox_credentials_csv(self, csv_text: str, *, note: str = "") -> dict[str, Any]:
        raw_text = str(csv_text or "")
        clean_note = str(note or "").strip()
        if not raw_text.strip():
            return {
                "total_lines": 0,
                "accepted_lines": 0,
                "created": 0,
                "updated": 0,
                "invalid": 0,
                "results": [],
            }

        lines = raw_text.splitlines()
        if lines and lines[0].startswith("\ufeff"):
            lines[0] = lines[0].lstrip("\ufeff")
        seen_in_request: set[str] = set()
        results: list[dict[str, Any]] = []
        created = 0
        updated = 0
        invalid = 0
        accepted_lines = 0

        for line_no, raw_line in enumerate(lines, start=1):
            source = str(raw_line or "").strip()
            if not source:
                continue
            accepted_lines += 1
            if "----" in source:
                address_raw, access_key_raw = source.split("----", 1)
                address = normalize_address(address_raw)
                access_key = str(access_key_raw or "").strip()
            else:
                address = normalize_address(source)
                access_key = ""
            if not is_valid_email_address(address):
                invalid += 1
                results.append(
                    {
                        "line": line_no,
                        "input": source,
                        "address": address,
                        "status": "invalid",
                        "reason": "invalid_address",
                    }
                )
                continue
            if address in seen_in_request:
                invalid += 1
                results.append(
                    {
                        "line": line_no,
                        "input": source,
                        "address": address,
                        "status": "invalid",
                        "reason": "duplicate_in_request",
                    }
                )
                continue
            seen_in_request.add(address)
            with self._lock:
                existing = self._conn.execute(
                    """
                    SELECT id, address, access_key, active, note, created_at, updated_at
                    FROM mailbox_credentials
                    WHERE address = ?
                    LIMIT 1
                    """,
                    (address,),
                ).fetchone()
                now = utcnow_iso()
                if existing:
                    next_access_key = access_key or str(existing["access_key"] or "")
                    next_note = clean_note if clean_note else str(existing["note"] or "")
                    self._conn.execute(
                        """
                        UPDATE mailbox_credentials
                        SET access_key = ?, active = 1, note = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (next_access_key, next_note, now, int(existing["id"] or 0)),
                    )
                    self._conn.commit()
                    row = self._conn.execute(
                        """
                        SELECT id, address, access_key, active, note, created_at, updated_at
                        FROM mailbox_credentials
                        WHERE id = ?
                        LIMIT 1
                        """,
                        (int(existing["id"] or 0),),
                    ).fetchone()
                    if not row:
                        invalid += 1
                        results.append(
                            {
                                "line": line_no,
                                "input": source,
                                "address": address,
                                "status": "invalid",
                                "reason": "update_failed",
                            }
                        )
                        continue
                    record = self._mailbox_record_from_row(row)
                    updated += 1
                    results.append(
                        {
                            "line": line_no,
                            "input": source,
                            "address": record.address,
                            "status": "updated",
                            "reason": "ok",
                            "mailbox": {
                                "id": record.mailbox_id,
                                "address": record.address,
                                "access_key": record.access_key,
                                "credential": f"{record.address}----{record.access_key}",
                                "note": record.note,
                                "created_at": record.created_at,
                                "updated_at": record.updated_at,
                                "active": record.active,
                            },
                        }
                    )
                    continue
            ok, mailbox, reason = self.create_mailbox_credential(address, note=clean_note)
            if not ok or not mailbox:
                invalid += 1
                results.append(
                    {
                        "line": line_no,
                        "input": source,
                        "address": address,
                        "status": "invalid",
                        "reason": reason,
                    }
                )
                continue
            if access_key:
                with self._lock:
                    self._conn.execute(
                        """
                        UPDATE mailbox_credentials
                        SET access_key = ?, active = 1, updated_at = ?
                        WHERE id = ?
                        """,
                        (access_key, utcnow_iso(), mailbox.mailbox_id),
                    )
                    self._conn.commit()
                    row = self._conn.execute(
                        """
                        SELECT id, address, access_key, active, note, created_at, updated_at
                        FROM mailbox_credentials
                        WHERE id = ?
                        LIMIT 1
                        """,
                        (mailbox.mailbox_id,),
                    ).fetchone()
                if row:
                    mailbox = self._mailbox_record_from_row(row)
            created += 1
            results.append(
                {
                    "line": line_no,
                    "input": source,
                    "address": mailbox.address,
                    "status": "created",
                    "reason": "ok",
                    "mailbox": {
                        "id": mailbox.mailbox_id,
                        "address": mailbox.address,
                        "access_key": mailbox.access_key,
                        "credential": f"{mailbox.address}----{mailbox.access_key}",
                        "note": mailbox.note,
                        "created_at": mailbox.created_at,
                        "updated_at": mailbox.updated_at,
                        "active": mailbox.active,
                    },
                }
            )

        return {
            "total_lines": len(lines),
            "accepted_lines": accepted_lines,
            "created": created,
            "updated": updated,
            "invalid": invalid,
            "results": results,
        }

    def get_mailbox_credential_by_id(self, mailbox_id: int) -> Optional[MailboxCredentialRecord]:
        if mailbox_id <= 0:
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, address, access_key, active, note, created_at, updated_at
                FROM mailbox_credentials
                WHERE id = ?
                LIMIT 1
                """,
                (int(mailbox_id),),
            ).fetchone()
        if not row:
            return None
        return self._mailbox_record_from_row(row)

    def get_mailbox_id_by_address(self, address: str) -> int:
        normalized = normalize_address(address)
        if not normalized:
            return 0
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM mailbox_credentials WHERE address = ? LIMIT 1",
                (normalized,),
            ).fetchone()
        return int(row["id"] or 0) if row else 0

    def verify_mailbox_access(self, address: str, access_key: str) -> tuple[bool, Optional[MailboxCredentialRecord], str]:
        normalized_address = normalize_address(address)
        raw_key = str(access_key or "").strip()
        if not normalized_address:
            return False, None, "missing_address"
        if not raw_key:
            return False, None, "missing_access_key"
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, address, access_key, active, note, created_at, updated_at
                FROM mailbox_credentials
                WHERE address = ?
                LIMIT 1
                """,
                (normalized_address,),
            ).fetchone()
        if not row:
            return False, None, "invalid_credential"
        record = self._mailbox_record_from_row(row)
        if not record.active:
            return False, None, "mailbox_inactive"
        if not secrets.compare_digest(record.access_key, raw_key):
            return False, None, "invalid_credential"
        return True, record, "ok"

    def reset_mailbox_access_key(self, mailbox_id: int) -> tuple[bool, Optional[MailboxCredentialRecord], str]:
        if mailbox_id <= 0:
            return False, None, "invalid_mailbox_id"
        access_key = generate_access_key()
        now = utcnow_iso()
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE mailbox_credentials
                SET access_key = ?, updated_at = ?
                WHERE id = ?
                """,
                (access_key, now, int(mailbox_id)),
            )
            self._conn.commit()
            if int(cursor.rowcount or 0) <= 0:
                return False, None, "mailbox_not_found"
            row = self._conn.execute(
                """
                SELECT id, address, access_key, active, note, created_at, updated_at
                FROM mailbox_credentials
                WHERE id = ?
                LIMIT 1
                """,
                (int(mailbox_id),),
            ).fetchone()
        if not row:
            return False, None, "mailbox_not_found"
        return True, self._mailbox_record_from_row(row), "ok"

    def reset_user_mailbox_access_key(
        self, user_id: int, address: str
    ) -> tuple[bool, Optional[MailboxCredentialRecord], str]:
        """Buyer self-service: reset the access key of a mailbox the user owns.
        Authoritative on owner_user_id (set when a logged-in buyer redeems)."""
        clean_user_id = int(user_id or 0)
        normalized = normalize_address(address)
        if clean_user_id <= 0:
            return False, None, "invalid_user"
        if not normalized:
            return False, None, "missing_address"
        access_key = generate_access_key()
        now = utcnow_iso()
        with self._lock:
            row = self._conn.execute(
                "SELECT id, owner_user_id FROM mailbox_credentials WHERE address = ? LIMIT 1",
                (normalized,),
            ).fetchone()
            if not row:
                return False, None, "mailbox_not_found"
            if int(row["owner_user_id"] or 0) != clean_user_id:
                return False, None, "not_owned"
            mailbox_id = int(row["id"] or 0)
            self._conn.execute(
                "UPDATE mailbox_credentials SET access_key = ?, updated_at = ? WHERE id = ?",
                (access_key, now, mailbox_id),
            )
            self._conn.commit()
            fresh = self._conn.execute(
                """
                SELECT id, address, access_key, active, note, created_at, updated_at
                FROM mailbox_credentials WHERE id = ? LIMIT 1
                """,
                (mailbox_id,),
            ).fetchone()
        if not fresh:
            return False, None, "mailbox_not_found"
        return True, self._mailbox_record_from_row(fresh), "ok"

    def replace_sold_mailbox(
        self, address: str
    ) -> tuple[bool, Optional[MailboxCredentialRecord], str]:
        """Admin 换货: mark a bad SOLD mailbox dead and dispense a same-tag
        replacement to the same owner/order. Atomic under the single lock."""
        normalized = normalize_address(address)
        if not normalized:
            return False, None, "missing_address"
        now = utcnow_iso()
        with self._lock:
            old = self._conn.execute(
                "SELECT id, status, owner_user_id, order_id FROM mailbox_credentials WHERE address = ? LIMIT 1",
                (normalized,),
            ).fetchone()
            if not old:
                return False, None, "mailbox_not_found"
            if str(old["status"] or "") != "sold":
                return False, None, "not_sold"
            old_id = int(old["id"] or 0)
            owner_user_id = int(old["owner_user_id"] or 0)
            order_id = int(old["order_id"] or 0)
            tag_row = self._conn.execute(
                "SELECT tag_id FROM mailbox_tag_links WHERE mailbox_id = ? LIMIT 1", (old_id,)
            ).fetchone()
            tag_id = int(tag_row["tag_id"] or 0) if tag_row else 0
            if tag_id > 0:
                repl = self._conn.execute(
                    """
                    SELECT mc.id AS id, mc.address AS address FROM mailbox_credentials mc
                    WHERE mc.status = 'available' AND mc.active = 1
                      AND EXISTS (SELECT 1 FROM mailbox_tag_links l WHERE l.mailbox_id = mc.id AND l.tag_id = ?)
                    ORDER BY mc.id ASC LIMIT 1
                    """,
                    (tag_id,),
                ).fetchone()
            else:
                repl = self._conn.execute(
                    "SELECT id, address FROM mailbox_credentials WHERE status = 'available' AND active = 1 ORDER BY id ASC LIMIT 1"
                ).fetchone()
            if not repl:
                return False, None, "insufficient_stock"
            new_id = int(repl["id"] or 0)
            new_address = str(repl["address"] or "")
            self._conn.execute(
                "UPDATE mailbox_credentials SET status = 'dead', active = 0, owner_user_id = 0, updated_at = ? WHERE id = ? AND status = 'sold'",
                (now, old_id),
            )
            updated = self._conn.execute(
                """
                UPDATE mailbox_credentials
                SET status = 'sold', owner_user_id = ?, sold_at = ?, order_id = ?, updated_at = ?
                WHERE id = ? AND status = 'available'
                """,
                (owner_user_id, now, order_id, now, new_id),
            )
            if int(updated.rowcount or 0) != 1:
                self._conn.rollback()
                return False, None, "insufficient_stock"
            if owner_user_id > 0:
                self._conn.execute(
                    "DELETE FROM user_mailboxes WHERE user_id = ? AND address = ?",
                    (owner_user_id, normalized),
                )
                self._conn.execute(
                    "INSERT OR IGNORE INTO user_mailboxes (user_id, address, created_at) VALUES (?, ?, ?)",
                    (owner_user_id, new_address, now),
                )
            fresh = self._conn.execute(
                """
                SELECT id, address, access_key, active, note, created_at, updated_at
                FROM mailbox_credentials WHERE id = ? LIMIT 1
                """,
                (new_id,),
            ).fetchone()
            self._conn.commit()
        if not fresh:
            return False, None, "mailbox_not_found"
        return True, self._mailbox_record_from_row(fresh), "ok"

    # ----- CDK / 卡密售卖 -----

    def generate_cdk_codes(
        self,
        count: int,
        *,
        tag_id: int = 0,
        quantity: int = 1,
        max_uses: int = 1,
        batch_label: str = "",
        note: str = "",
        expires_at: str = "",
        created_by: int = 0,
    ) -> dict[str, Any]:
        clean_count = max(1, min(int(count or 0), 1000))
        clean_tag_id = max(0, int(tag_id or 0))
        clean_quantity = max(1, int(quantity or 1))
        clean_max_uses = max(1, int(max_uses or 1))
        clean_label = str(batch_label or "").strip()
        clean_note = str(note or "").strip()
        clean_expires = str(expires_at or "").strip()
        now = utcnow_iso()
        created: list[dict[str, Any]] = []
        # Reserve enough presale mailboxes to back every potential delivery from
        # this batch (codes x copies x uses). Atomic under the global lock; SQL
        # inlined so we never re-enter the non-reentrant lock.
        required = clean_count * clean_quantity * clean_max_uses
        with self._lock:
            if clean_tag_id > 0:
                presale_available = int(self._conn.execute(
                    """
                    SELECT COUNT(*) FROM mailbox_credentials mc
                    WHERE mc.status = 'presale' AND mc.active = 1
                      AND EXISTS (
                          SELECT 1 FROM mailbox_tag_links l
                          WHERE l.mailbox_id = mc.id AND l.tag_id = ?
                      )
                    """,
                    (clean_tag_id,),
                ).fetchone()[0] or 0)
            else:
                presale_available = int(self._conn.execute(
                    "SELECT COUNT(*) FROM mailbox_credentials WHERE status = 'presale' AND active = 1"
                ).fetchone()[0] or 0)
            if presale_available < required:
                return {
                    "ok": False,
                    "codes": [],
                    "error": "insufficient_presale",
                    "available": presale_available,
                    "required": required,
                }
            for _ in range(clean_count):
                code = ""
                for _attempt in range(10):
                    candidate = generate_cdk_code()
                    exists = self._conn.execute(
                        "SELECT 1 FROM cdks WHERE code = ? LIMIT 1", (candidate,)
                    ).fetchone()
                    if not exists:
                        code = candidate
                        break
                if not code:
                    continue
                cursor = self._conn.execute(
                    """
                    INSERT INTO cdks
                        (code, tag_id, quantity, max_uses, used_count, active,
                         batch_label, note, expires_at, created_by, created_at)
                    VALUES (?, ?, ?, ?, 0, 1, ?, ?, ?, ?, ?)
                    """,
                    (
                        code,
                        clean_tag_id,
                        clean_quantity,
                        clean_max_uses,
                        clean_label,
                        clean_note,
                        clean_expires,
                        int(created_by or 0),
                        now,
                    ),
                )
                created.append(
                    {
                        "id": int(cursor.lastrowid or 0),
                        "code": code,
                        "tag_id": clean_tag_id,
                        "quantity": clean_quantity,
                        "max_uses": clean_max_uses,
                        "batch_label": clean_label,
                        "expires_at": clean_expires,
                    }
                )
            # Move the actually-created batch's worth of mailboxes from the
            # presale pool into the redeemable (available) pool.
            moved = len(created) * clean_quantity * clean_max_uses
            if moved > 0:
                if clean_tag_id > 0:
                    self._conn.execute(
                        """
                        UPDATE mailbox_credentials
                        SET status = 'available', updated_at = ?
                        WHERE id IN (
                            SELECT mc.id FROM mailbox_credentials mc
                            WHERE mc.status = 'presale' AND mc.active = 1
                              AND EXISTS (
                                  SELECT 1 FROM mailbox_tag_links l
                                  WHERE l.mailbox_id = mc.id AND l.tag_id = ?
                              )
                            ORDER BY mc.id ASC
                            LIMIT ?
                        )
                        """,
                        (now, clean_tag_id, moved),
                    )
                else:
                    self._conn.execute(
                        """
                        UPDATE mailbox_credentials
                        SET status = 'available', updated_at = ?
                        WHERE id IN (
                            SELECT id FROM mailbox_credentials
                            WHERE status = 'presale' AND active = 1
                            ORDER BY id ASC
                            LIMIT ?
                        )
                        """,
                        (now, moved),
                    )
            self._conn.commit()
        return {"ok": True, "codes": created, "moved": moved}

    def redeem_cdk(self, code: str, user_id: int) -> tuple[bool, Optional[dict[str, Any]], str]:
        clean_code = str(code or "").strip().upper()
        clean_user_id = int(user_id or 0)
        if not clean_code:
            return False, None, "missing_code"
        # user_id == 0 means an anonymous (not-logged-in) redemption — the
        # buyer keeps the dispensed credential in their browser localStorage.
        # user_id > 0 additionally binds the mailbox to the account.
        if clean_user_id < 0:
            return False, None, "invalid_user"
        now = utcnow_iso()
        # Whole operation is atomic under the single global lock. Do NOT call
        # other locking methods (assign_mailbox etc.) here — the lock is not
        # reentrant and would deadlock. All SQL is inlined.
        with self._lock:
            cdk = self._conn.execute(
                "SELECT * FROM cdks WHERE code = ? LIMIT 1", (clean_code,)
            ).fetchone()
            if not cdk:
                return False, None, "cdk_not_found"
            if int(cdk["active"] or 0) != 1:
                return False, None, "cdk_disabled"
            if int(cdk["used_count"] or 0) >= int(cdk["max_uses"] or 1):
                return False, None, "cdk_used"
            expires_at = str(cdk["expires_at"] or "")
            if expires_at and expires_at <= now:
                return False, None, "cdk_expired"

            cdk_id = int(cdk["id"] or 0)
            tag_id = int(cdk["tag_id"] or 0)
            quantity = max(1, int(cdk["quantity"] or 1))

            if tag_id > 0:
                rows = self._conn.execute(
                    """
                    SELECT mc.id AS id, mc.address AS address
                    FROM mailbox_credentials mc
                    WHERE mc.status = 'available' AND mc.active = 1
                      AND EXISTS (
                          SELECT 1 FROM mailbox_tag_links l
                          WHERE l.mailbox_id = mc.id AND l.tag_id = ?
                      )
                    ORDER BY mc.id ASC
                    LIMIT ?
                    """,
                    (tag_id, quantity),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT mc.id AS id, mc.address AS address
                    FROM mailbox_credentials mc
                    WHERE mc.status = 'available' AND mc.active = 1
                    ORDER BY mc.id ASC
                    LIMIT ?
                    """,
                    (quantity,),
                ).fetchall()

            if len(rows) < quantity:
                return False, None, "insufficient_stock"

            mailbox_ids = [int(r["id"] or 0) for r in rows]
            addresses = [str(r["address"] or "") for r in rows]

            # Record the order first to obtain order_id.
            order_cursor = self._conn.execute(
                """
                INSERT INTO cdk_redemptions
                    (cdk_id, code, user_id, mailbox_ids, addresses, status, redeemed_at)
                VALUES (?, ?, ?, ?, ?, 'success', ?)
                """,
                (
                    cdk_id,
                    clean_code,
                    clean_user_id,
                    json.dumps(mailbox_ids),
                    json.dumps(addresses),
                    now,
                ),
            )
            order_id = int(order_cursor.lastrowid or 0)

            placeholders = ",".join("?" for _ in mailbox_ids)
            update_cursor = self._conn.execute(
                f"""
                UPDATE mailbox_credentials
                SET status = 'sold', owner_user_id = ?, sold_at = ?, order_id = ?, updated_at = ?
                WHERE id IN ({placeholders}) AND status = 'available'
                """,
                (clean_user_id, now, order_id, now, *mailbox_ids),
            )
            if int(update_cursor.rowcount or 0) != quantity:
                # Race lost despite the lock — abort the whole transaction.
                self._conn.rollback()
                return False, None, "insufficient_stock"

            used_cursor = self._conn.execute(
                "UPDATE cdks SET used_count = used_count + 1 WHERE id = ? AND used_count < max_uses",
                (cdk_id,),
            )
            if int(used_cursor.rowcount or 0) <= 0:
                self._conn.rollback()
                return False, None, "cdk_used"

            # Link to the user (inlined assign_mailbox body) so the user
            # dashboard and user_has_mailbox checks light up immediately.
            # Anonymous redemptions (user_id == 0) skip this — not bound to any account.
            if clean_user_id > 0:
                for address in addresses:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO user_mailboxes (user_id, address, created_at) VALUES (?, ?, ?)",
                        (clean_user_id, address, now),
                    )

            # Fetch dispensed credentials for the response.
            dispensed = self._conn.execute(
                f"""
                SELECT id, address, access_key
                FROM mailbox_credentials
                WHERE id IN ({placeholders})
                ORDER BY id ASC
                """,
                tuple(mailbox_ids),
            ).fetchall()
            self._conn.commit()

        mailboxes = [
            {
                "id": int(r["id"] or 0),
                "address": str(r["address"] or ""),
                "access_key": str(r["access_key"] or ""),
                "credential": f"{r['address']}----{r['access_key']}",
            }
            for r in dispensed
        ]
        return True, {"redemption_id": order_id, "quantity": quantity, "mailboxes": mailboxes}, "ok"

    def list_cdks(
        self,
        *,
        keyword: str = "",
        status: str = "",
        tag_id: int = 0,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        clean_keyword = str(keyword or "").strip().lower()
        clean_status = str(status or "").strip().lower()
        clean_tag_id = max(0, int(tag_id or 0))
        clean_limit = max(1, min(int(limit or 50), 200))
        clean_offset = max(0, int(offset or 0))
        now = utcnow_iso()

        where: list[str] = []
        params: list[Any] = []
        if clean_keyword:
            where.append("(lower(c.code) LIKE ? OR lower(c.batch_label) LIKE ? OR lower(c.note) LIKE ?)")
            like = f"%{clean_keyword}%"
            params.extend([like, like, like])
        if clean_tag_id > 0:
            where.append("c.tag_id = ?")
            params.append(clean_tag_id)
        if clean_status == "active":
            where.append("c.active = 1 AND c.used_count < c.max_uses AND (c.expires_at = '' OR c.expires_at > ?)")
            params.append(now)
        elif clean_status == "used":
            where.append("c.used_count >= c.max_uses")
        elif clean_status == "expired":
            where.append("c.expires_at != '' AND c.expires_at <= ?")
            params.append(now)
        elif clean_status == "disabled":
            where.append("c.active = 0")
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        with self._lock:
            total = int(
                self._conn.execute(
                    f"SELECT COUNT(*) FROM cdks c {where_sql}", tuple(params)
                ).fetchone()[0]
                or 0
            )
            rows = self._conn.execute(
                f"""
                SELECT c.*, mt.name AS tag_name
                FROM cdks c
                LEFT JOIN mailbox_tags mt ON mt.id = c.tag_id
                {where_sql}
                ORDER BY c.id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, clean_limit, clean_offset),
            ).fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            used = int(row["used_count"] or 0)
            max_uses = int(row["max_uses"] or 1)
            expires_at = str(row["expires_at"] or "")
            if int(row["active"] or 0) != 1:
                state = "disabled"
            elif used >= max_uses:
                state = "used"
            elif expires_at and expires_at <= now:
                state = "expired"
            else:
                state = "active"
            items.append(
                {
                    "id": int(row["id"] or 0),
                    "code": str(row["code"] or ""),
                    "tag_id": int(row["tag_id"] or 0),
                    "tag_name": str(row["tag_name"] or "") if row["tag_id"] else "",
                    "quantity": int(row["quantity"] or 1),
                    "max_uses": max_uses,
                    "used_count": used,
                    "active": int(row["active"] or 0),
                    "batch_label": str(row["batch_label"] or ""),
                    "note": str(row["note"] or ""),
                    "expires_at": expires_at,
                    "state": state,
                    "created_at": str(row["created_at"] or ""),
                }
            )
        return items, total

    def set_cdk_active(self, cdk_id: int, active: bool) -> tuple[bool, str]:
        clean_id = int(cdk_id or 0)
        if clean_id <= 0:
            return False, "invalid_cdk_id"
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE cdks SET active = ? WHERE id = ?",
                (1 if active else 0, clean_id),
            )
            self._conn.commit()
        if int(cursor.rowcount or 0) <= 0:
            return False, "cdk_not_found"
        return True, "ok"

    def stock_summary_by_tag(self) -> dict[str, Any]:
        with self._lock:
            tag_rows = self._conn.execute(
                """
                SELECT
                    mt.id AS id,
                    mt.name AS name,
                    SUM(CASE WHEN mc.status = 'available' AND mc.active = 1 THEN 1 ELSE 0 END) AS available,
                    SUM(CASE WHEN mc.status = 'presale' AND mc.active = 1 THEN 1 ELSE 0 END) AS presale,
                    SUM(CASE WHEN mc.status = 'sold' THEN 1 ELSE 0 END) AS sold
                FROM mailbox_tags mt
                LEFT JOIN mailbox_tag_links l ON l.tag_id = mt.id
                LEFT JOIN mailbox_credentials mc ON mc.id = l.mailbox_id
                GROUP BY mt.id, mt.name
                ORDER BY available DESC, mt.name ASC
                """
            ).fetchall()
            untagged = self._conn.execute(
                """
                SELECT
                    SUM(CASE WHEN mc.status = 'available' AND mc.active = 1 THEN 1 ELSE 0 END) AS available,
                    SUM(CASE WHEN mc.status = 'presale' AND mc.active = 1 THEN 1 ELSE 0 END) AS presale,
                    SUM(CASE WHEN mc.status = 'sold' THEN 1 ELSE 0 END) AS sold
                FROM mailbox_credentials mc
                WHERE NOT EXISTS (SELECT 1 FROM mailbox_tag_links l WHERE l.mailbox_id = mc.id)
                """
            ).fetchone()
            totals = self._conn.execute(
                """
                SELECT
                    SUM(CASE WHEN status = 'available' AND active = 1 THEN 1 ELSE 0 END) AS available,
                    SUM(CASE WHEN status = 'presale' AND active = 1 THEN 1 ELSE 0 END) AS presale,
                    SUM(CASE WHEN status = 'sold' THEN 1 ELSE 0 END) AS sold,
                    SUM(CASE WHEN status != 'deleted' THEN 1 ELSE 0 END) AS total
                FROM mailbox_credentials
                """
            ).fetchone()
        tags = [
            {
                "id": int(r["id"] or 0),
                "name": str(r["name"] or ""),
                "available": int(r["available"] or 0),
                "presale": int(r["presale"] or 0),
                "sold": int(r["sold"] or 0),
            }
            for r in tag_rows
        ]
        return {
            "tags": tags,
            "untagged": {
                "available": int((untagged["available"] if untagged else 0) or 0),
                "presale": int((untagged["presale"] if untagged else 0) or 0),
                "sold": int((untagged["sold"] if untagged else 0) or 0),
            },
            "totals": {
                "available": int((totals["available"] if totals else 0) or 0),
                "presale": int((totals["presale"] if totals else 0) or 0),
                "sold": int((totals["sold"] if totals else 0) or 0),
                "total": int((totals["total"] if totals else 0) or 0),
            },
        }

    def sales_stats(self) -> dict[str, Any]:
        # Start of today in Beijing, as a UTC ISO string with microseconds so it
        # compares lexically against stored utcnow_iso() timestamps.
        # ponytail: lexical compare; a row at an exact microsecond-zero midnight
        # could miscount by one — negligible for a display counter.
        boundary = (
            datetime.now(BEIJING_TIMEZONE)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        with self._lock:
            totals = self._conn.execute(
                """
                SELECT
                    SUM(CASE WHEN status = 'available' AND active = 1 THEN 1 ELSE 0 END) AS available,
                    SUM(CASE WHEN status = 'sold' THEN 1 ELSE 0 END) AS sold,
                    COUNT(*) AS total
                FROM mailbox_credentials
                """
            ).fetchone()
            today_sold = self._conn.execute(
                "SELECT COUNT(*) AS c FROM mailbox_credentials WHERE status = 'sold' AND sold_at >= ?",
                (boundary,),
            ).fetchone()
            today_redemptions = self._conn.execute(
                "SELECT COUNT(*) AS c FROM cdk_redemptions WHERE status = 'success' AND redeemed_at >= ?",
                (boundary,),
            ).fetchone()
        return {
            "available": int((totals["available"] if totals else 0) or 0),
            "sold": int((totals["sold"] if totals else 0) or 0),
            "total": int((totals["total"] if totals else 0) or 0),
            "today_sold": int((today_sold["c"] if today_sold else 0) or 0),
            "today_redemptions": int((today_redemptions["c"] if today_redemptions else 0) or 0),
        }

    def list_user_purchased_mailboxes(self, user_id: int) -> list[dict[str, Any]]:
        clean_user_id = int(user_id or 0)
        if clean_user_id <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, address, access_key, sold_at, created_at
                FROM mailbox_credentials
                WHERE owner_user_id = ?
                ORDER BY sold_at DESC, id DESC
                """,
                (clean_user_id,),
            ).fetchall()
        mailbox_ids = [int(r["id"] or 0) for r in rows]
        tag_map = self._list_tags_for_mailbox_ids(mailbox_ids)
        return [
            {
                "id": int(r["id"] or 0),
                "address": str(r["address"] or ""),
                "access_key": str(r["access_key"] or ""),
                "credential": f"{r['address']}----{r['access_key']}",
                "sold_at": str(r["sold_at"] or ""),
                "tags": tag_map.get(int(r["id"] or 0), []),
            }
            for r in rows
        ]

    def list_user_redemptions(self, user_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        clean_user_id = int(user_id or 0)
        if clean_user_id <= 0:
            return []
        clean_limit = max(1, min(int(limit or 50), 200))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, cdk_id, code, addresses, status, redeemed_at
                FROM cdk_redemptions
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (clean_user_id, clean_limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            try:
                addresses = json.loads(str(r["addresses"] or "[]"))
            except Exception:
                addresses = []
            result.append(
                {
                    "id": int(r["id"] or 0),
                    "code": str(r["code"] or ""),
                    "addresses": addresses,
                    "status": str(r["status"] or ""),
                    "redeemed_at": str(r["redeemed_at"] or ""),
                }
            )
        return result

    def list_mailbox_tags(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    mt.id AS id,
                    mt.name AS name,
                    mt.created_at AS created_at,
                    COUNT(mtl.mailbox_id) AS mailbox_count
                FROM mailbox_tags mt
                LEFT JOIN mailbox_tag_links mtl ON mtl.tag_id = mt.id
                GROUP BY mt.id, mt.name, mt.created_at
                ORDER BY lower(mt.name) ASC, mt.id ASC
                """
            ).fetchall()
        return [
            {
                "id": record.tag_id,
                "name": record.name,
                "created_at": record.created_at,
                "mailbox_count": record.mailbox_count,
            }
            for record in (self._mailbox_tag_record_from_row(row) for row in rows)
        ]

    def create_mailbox_tag(self, name: str) -> tuple[bool, Optional[dict[str, Any]], str]:
        clean_name = str(name or "").strip()
        if not clean_name:
            return False, None, "missing_tag_name"
        now = utcnow_iso()
        with self._lock:
            existing = self._conn.execute(
                "SELECT id, name, created_at FROM mailbox_tags WHERE lower(name) = lower(?) LIMIT 1",
                (clean_name,),
            ).fetchone()
            if existing:
                return False, None, "tag_exists"
            cursor = self._conn.execute(
                "INSERT INTO mailbox_tags (name, created_at) VALUES (?, ?)",
                (clean_name, now),
            )
            self._conn.commit()
            tag_id = int(cursor.lastrowid or 0)
            row = self._conn.execute(
                """
                SELECT id, name, created_at, 0 AS mailbox_count
                FROM mailbox_tags
                WHERE id = ?
                LIMIT 1
                """,
                (tag_id,),
            ).fetchone()
        if not row:
            return False, None, "create_failed"
        record = self._mailbox_tag_record_from_row(row)
        return True, {
            "id": record.tag_id,
            "name": record.name,
            "created_at": record.created_at,
            "mailbox_count": record.mailbox_count,
        }, "ok"

    def delete_mailbox_tag(self, tag_id: int) -> tuple[bool, str]:
        clean_tag_id = int(tag_id or 0)
        if clean_tag_id <= 0:
            return False, "invalid_tag_id"
        with self._lock:
            exists = self._conn.execute("SELECT id FROM mailbox_tags WHERE id = ? LIMIT 1", (clean_tag_id,)).fetchone()
            if not exists:
                return False, "tag_not_found"
            self._conn.execute("DELETE FROM mailbox_tag_links WHERE tag_id = ?", (clean_tag_id,))
            self._conn.execute("DELETE FROM mailbox_tags WHERE id = ?", (clean_tag_id,))
            self._conn.commit()
        return True, "ok"

    def set_mailbox_tags(self, mailbox_id: int, tag_ids: list[Any]) -> tuple[bool, Optional[dict[str, Any]], str]:
        clean_mailbox_id = int(mailbox_id or 0)
        if clean_mailbox_id <= 0:
            return False, None, "invalid_mailbox_id"
        normalized_tag_ids = sorted({int(tag_id) for tag_id in tag_ids if int(tag_id or 0) > 0})
        with self._lock:
            mailbox_row = self._conn.execute(
                """
                SELECT id, address, access_key, active, note, created_at, updated_at
                FROM mailbox_credentials
                WHERE id = ?
                LIMIT 1
                """,
                (clean_mailbox_id,),
            ).fetchone()
            if not mailbox_row:
                return False, None, "mailbox_not_found"
            if normalized_tag_ids:
                placeholders = ",".join("?" for _ in normalized_tag_ids)
                count_row = self._conn.execute(
                    f"SELECT COUNT(1) AS total FROM mailbox_tags WHERE id IN ({placeholders})",
                    tuple(normalized_tag_ids),
                ).fetchone()
                if int((count_row["total"] if count_row else 0) or 0) != len(normalized_tag_ids):
                    return False, None, "tag_not_found"
            self._conn.execute("DELETE FROM mailbox_tag_links WHERE mailbox_id = ?", (clean_mailbox_id,))
            now = utcnow_iso()
            for tag_id_value in normalized_tag_ids:
                self._conn.execute(
                    """
                    INSERT INTO mailbox_tag_links (mailbox_id, tag_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (clean_mailbox_id, tag_id_value, now),
                )
            self._conn.commit()
        record = self._mailbox_record_from_row(mailbox_row)
        tags = self._list_tags_for_mailbox_ids([record.mailbox_id]).get(record.mailbox_id, [])
        return True, {
            "id": record.mailbox_id,
            "address": record.address,
            "access_key": record.access_key,
            "active": record.active,
            "note": record.note,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "tags": tags,
        }, "ok"


    def set_mailbox_active(self, mailbox_id: int, active: bool) -> tuple[bool, Optional[MailboxCredentialRecord], str]:
        if mailbox_id <= 0:
            return False, None, "invalid_mailbox_id"
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE mailbox_credentials
                SET active = ?, updated_at = ?
                WHERE id = ?
                """,
                (1 if active else 0, utcnow_iso(), int(mailbox_id)),
            )
            self._conn.commit()
            if int(cursor.rowcount or 0) <= 0:
                return False, None, "mailbox_not_found"
            row = self._conn.execute(
                """
                SELECT id, address, access_key, active, note, created_at, updated_at
                FROM mailbox_credentials
                WHERE id = ?
                LIMIT 1
                """,
                (int(mailbox_id),),
            ).fetchone()
        if not row:
            return False, None, "mailbox_not_found"
        return True, self._mailbox_record_from_row(row), "ok"

    def delete_mailbox_credential(self, mailbox_id: int) -> tuple[bool, str]:
        # Soft delete: mark status='deleted' and deactivate. Tags, ownership and
        # mail are kept; redeem (status='available' only) already skips it.
        clean_id = int(mailbox_id or 0)
        if clean_id <= 0:
            return False, "invalid_mailbox_id"
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE mailbox_credentials SET status = 'deleted', active = 0, updated_at = ? WHERE id = ?",
                (utcnow_iso(), clean_id),
            )
            self._conn.commit()
            if int(cursor.rowcount or 0) <= 0:
                return False, "mailbox_not_found"
        return True, "ok"

    def update_mailbox_note(self, mailbox_id: int, note: str) -> tuple[bool, Optional[MailboxCredentialRecord], str]:
        if mailbox_id <= 0:
            return False, None, "invalid_mailbox_id"
        clean_note = str(note or "").strip()
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE mailbox_credentials
                SET note = ?, updated_at = ?
                WHERE id = ?
                """,
                (clean_note, utcnow_iso(), int(mailbox_id)),
            )
            self._conn.commit()
            if int(cursor.rowcount or 0) <= 0:
                return False, None, "mailbox_not_found"
            row = self._conn.execute(
                """
                SELECT id, address, access_key, active, note, created_at, updated_at
                FROM mailbox_credentials
                WHERE id = ?
                LIMIT 1
                """,
                (int(mailbox_id),),
            ).fetchone()
        if not row:
            return False, None, "mailbox_not_found"
        return True, self._mailbox_record_from_row(row), "ok"

    @staticmethod
    def _user_record_from_row(row: sqlite3.Row) -> UserRecord:
        return UserRecord(
            user_id=int(row["id"] or 0),
            username=str(row["username"] or ""),
            role=str(row["role"] or "user"),
            active=bool(int(row["active"] or 0)),
            created_at=format_beijing_time(row["created_at"]),
        )

    def has_any_admin(self) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM users WHERE role = 'admin' AND active = 1 LIMIT 1"
            ).fetchone()
        return row is not None

    def ensure_admin_user(self, username: str, password_hash: str) -> None:
        normalized_username = normalize_username(username)
        normalized_hash = str(password_hash or "").strip()
        if not normalized_username or not normalized_hash:
            return
        with self._lock:
            existing = self._conn.execute(
                """
                SELECT id
                FROM users
                WHERE username = ?
                LIMIT 1
                """,
                (normalized_username,),
            ).fetchone()
            if existing:
                self._conn.execute(
                    """
                    UPDATE users
                    SET password_hash = ?, role = 'admin', active = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (normalized_hash, utcnow_iso(), int(existing["id"] or 0)),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO users (username, password_hash, role, active, created_at, updated_at)
                    VALUES (?, ?, 'admin', 1, ?, ?)
                    """,
                    (normalized_username, normalized_hash, utcnow_iso(), utcnow_iso()),
                )
            self._conn.commit()

    def create_user(self, username: str, password_hash: str, role: str = "user") -> tuple[bool, Optional[UserRecord], str]:
        normalized_username = normalize_username(username)
        normalized_hash = str(password_hash or "").strip()
        normalized_role = str(role or "user").strip().lower()
        if normalized_role not in {"user", "admin"}:
            return False, None, "invalid_role"
        if not normalized_username:
            return False, None, "missing_username"
        if not normalized_hash:
            return False, None, "missing_password_hash"
        with self._lock:
            existing = self._conn.execute(
                """
                SELECT id, username, role, active, created_at
                FROM users
                WHERE username = ?
                LIMIT 1
                """,
                (normalized_username,),
            ).fetchone()
            if existing:
                return False, None, "username_exists"
            now = utcnow_iso()
            cursor = self._conn.execute(
                """
                INSERT INTO users (username, password_hash, role, active, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (normalized_username, normalized_hash, normalized_role, now, now),
            )
            self._conn.commit()
            user_id = int(cursor.lastrowid or 0)
            row = self._conn.execute(
                """
                SELECT id, username, role, active, created_at
                FROM users
                WHERE id = ?
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        if not row:
            return False, None, "create_failed"
        return True, self._user_record_from_row(row), "ok"

    def get_user_by_username(self, username: str) -> Optional[dict[str, Any]]:
        normalized_username = normalize_username(username)
        if not normalized_username:
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, username, password_hash, role, active, created_at
                FROM users
                WHERE username = ?
                LIMIT 1
                """,
                (normalized_username,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": int(row["id"] or 0),
            "username": str(row["username"] or ""),
            "password_hash": str(row["password_hash"] or ""),
            "role": str(row["role"] or "user"),
            "active": bool(int(row["active"] or 0)),
            "created_at": format_beijing_time(row["created_at"]),
        }

    def list_users_with_mailboxes(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    u.id AS id,
                    u.username AS username,
                    u.role AS role,
                    u.active AS active,
                    u.created_at AS created_at,
                    um.address AS mailbox
                FROM users u
                LEFT JOIN user_mailboxes um ON um.user_id = u.id
                ORDER BY u.id ASC, um.address ASC
                """
            ).fetchall()
        by_user: dict[int, dict[str, Any]] = {}
        for row in rows:
            user_id = int(row["id"] or 0)
            if user_id not in by_user:
                by_user[user_id] = {
                    "id": user_id,
                    "username": str(row["username"] or ""),
                    "role": str(row["role"] or "user"),
                    "active": bool(int(row["active"] or 0)),
                    "created_at": format_beijing_time(row["created_at"]),
                    "mailboxes": [],
                }
            mailbox = normalize_address(row["mailbox"])
            if mailbox:
                by_user[user_id]["mailboxes"].append(mailbox)
        return list(by_user.values())

    def assign_mailbox(self, user_id: int, address: str) -> tuple[bool, str]:
        normalized_address = normalize_address(address)
        if user_id <= 0:
            return False, "invalid_user_id"
        if not normalized_address:
            return False, "missing_address"
        with self._lock:
            existing_user = self._conn.execute(
                "SELECT id FROM users WHERE id = ? LIMIT 1",
                (int(user_id),),
            ).fetchone()
            if not existing_user:
                return False, "user_not_found"
            self._conn.execute(
                """
                INSERT OR IGNORE INTO user_mailboxes (user_id, address, created_at)
                VALUES (?, ?, ?)
                """,
                (int(user_id), normalized_address, utcnow_iso()),
            )
            self._conn.commit()
        return True, "ok"

    def unassign_mailbox(self, user_id: int, address: str) -> tuple[bool, str]:
        normalized_address = normalize_address(address)
        if user_id <= 0:
            return False, "invalid_user_id"
        if not normalized_address:
            return False, "missing_address"
        with self._lock:
            cursor = self._conn.execute(
                """
                DELETE FROM user_mailboxes
                WHERE user_id = ? AND address = ?
                """,
                (int(user_id), normalized_address),
            )
            self._conn.commit()
            if int(cursor.rowcount or 0) <= 0:
                return False, "not_found"
        return True, "ok"

    def list_user_mailboxes(self, user_id: int) -> list[str]:
        if user_id <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT address
                FROM user_mailboxes
                WHERE user_id = ?
                ORDER BY address ASC
                """,
                (int(user_id),),
            ).fetchall()
        return [normalize_address(row["address"]) for row in rows if normalize_address(row["address"])]

    def user_has_mailbox(self, user_id: int, address: str) -> bool:
        normalized_address = normalize_address(address)
        if user_id <= 0 or not normalized_address:
            return False
        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1
                FROM user_mailboxes
                WHERE user_id = ? AND address = ?
                LIMIT 1
                """,
                (int(user_id), normalized_address),
            ).fetchone()
        return row is not None

    def reset_user_password(self, user_id: int, password_hash: str) -> tuple[bool, str]:
        normalized_hash = str(password_hash or "").strip()
        if user_id <= 0:
            return False, "invalid_user_id"
        if not normalized_hash:
            return False, "missing_password_hash"
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE users
                SET password_hash = ?, updated_at = ?
                WHERE id = ?
                """,
                (normalized_hash, utcnow_iso(), int(user_id)),
            )
            self._conn.commit()
            if int(cursor.rowcount or 0) <= 0:
                return False, "user_not_found"
            self._conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at = ''",
                (utcnow_iso(), int(user_id)),
            )
            self._conn.commit()
        return True, "ok"

    def create_session(self, user_id: int, token_hash: str, expires_at: str) -> bool:
        if user_id <= 0:
            return False
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions (user_id, token_hash, expires_at, last_seen_at, created_at, revoked_at)
                VALUES (?, ?, ?, ?, ?, '')
                """,
                (int(user_id), str(token_hash), str(expires_at), utcnow_iso(), utcnow_iso()),
            )
            self._conn.commit()
        return True

    def get_user_by_session_token_hash(self, token_hash: str) -> Optional[dict[str, Any]]:
        normalized_hash = str(token_hash or "").strip()
        if not normalized_hash:
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    s.id AS session_id,
                    s.user_id AS user_id,
                    s.expires_at AS expires_at,
                    s.revoked_at AS revoked_at,
                    u.username AS username,
                    u.role AS role,
                    u.active AS active
                FROM sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ?
                LIMIT 1
                """,
                (normalized_hash,),
            ).fetchone()
            if not row:
                return None
            if str(row["revoked_at"] or "").strip():
                return None
            expires_at = parse_utc_iso(row["expires_at"])
            if not expires_at or expires_at <= datetime.now(timezone.utc):
                return None
            if not bool(int(row["active"] or 0)):
                return None
            self._conn.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
                (utcnow_iso(), int(row["session_id"] or 0)),
            )
            self._conn.commit()
        return {
            "session_id": int(row["session_id"] or 0),
            "user_id": int(row["user_id"] or 0),
            "username": str(row["username"] or ""),
            "role": str(row["role"] or "user"),
            "active": bool(int(row["active"] or 0)),
            "expires_at": format_beijing_time(row["expires_at"]),
        }

    def revoke_session(self, token_hash: str) -> None:
        normalized_hash = str(token_hash or "").strip()
        if not normalized_hash:
            return
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at = ''",
                (utcnow_iso(), normalized_hash),
            )
            self._conn.commit()

    def next_pending_invite(self, address: str = "") -> Optional[MailRecord]:
        normalized = normalize_address(address)
        with self._lock:
            if normalized:
                row = self._conn.execute(
                    """
                    SELECT
                        id, address, from_address, subject, text, html, body, raw_headers, raw_mail, raw_header_text,
                        received_at, verification_code,
                        mail_type, invite_link, process_status, processed_at, process_note
                    FROM messages
                    WHERE address = ? AND mail_type = 'team_invite' AND process_status = 'pending'
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (normalized,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    """
                    SELECT
                        id, address, from_address, subject, text, html, body, raw_headers, raw_mail, raw_header_text,
                        received_at, verification_code,
                        mail_type, invite_link, process_status, processed_at, process_note
                    FROM messages
                    WHERE mail_type = 'team_invite' AND process_status = 'pending'
                    ORDER BY id ASC
                    LIMIT 1
                    """
                ).fetchone()
        if not row:
            return None
        return self._mail_record_from_row(row)

    def mark_invite(self, message_id: int, status: str, note: str = "") -> tuple[bool, Optional[MailRecord], str]:
        if message_id <= 0:
            return False, None, "invalid_id"
        normalized_status = str(status or "").strip().lower()
        if normalized_status != "accepted":
            return False, None, "invalid_status"
        processed_at = utcnow_iso()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    id, address, from_address, subject, text, html, body, raw_headers, raw_mail, raw_header_text,
                    received_at, verification_code,
                    mail_type, invite_link, process_status, processed_at, process_note
                FROM messages
                WHERE id = ?
                LIMIT 1
                """,
                (int(message_id),),
            ).fetchone()
            if not row:
                return False, None, "not_found"
            if str(row["mail_type"] or "") != "team_invite":
                return False, None, "not_invite"
            if str(row["process_status"] or "") == normalized_status:
                return True, self._mail_record_from_row(row), "ok"
            self._conn.execute(
                """
                UPDATE messages
                SET process_status = ?, processed_at = ?, process_note = ?
                WHERE id = ?
                """,
                (normalized_status, processed_at, str(note or "").strip(), int(message_id)),
            )
            self._conn.commit()
            updated = self._conn.execute(
                """
                SELECT
                    id, address, from_address, subject, text, html, body, raw_headers, raw_mail, raw_header_text,
                    received_at, verification_code,
                    mail_type, invite_link, process_status, processed_at, process_note
                FROM messages
                WHERE id = ?
                LIMIT 1
                """,
                (int(message_id),),
            ).fetchone()
        if not updated:
            return False, None, "not_found"
        return True, self._mail_record_from_row(updated), "ok"

    def delete_message(self, message_id: int) -> bool:
        if message_id <= 0:
            return False
        with self._lock:
            cursor = self._conn.execute("DELETE FROM messages WHERE id = ?", (int(message_id),))
            self._conn.commit()
            return int(cursor.rowcount or 0) > 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class RateLimiter:
    """In-memory sliding-window rate limiter.

    ponytail: per-process only — counters live in memory, so they reset on
    restart and are not shared across workers. Fine for a single-process server
    fronting one box; move to the DB or a shared cache if you scale out.
    """

    def __init__(self, max_events: int, window_seconds: int):
        self.max_events = max(1, int(max_events))
        self.window = max(1, int(window_seconds))
        self._events: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window
        items = [t for t in self._events.get(key, ()) if t > cutoff]
        if items:
            self._events[key] = items
        else:
            self._events.pop(key, None)
        return items

    def retry_after(self, key: str) -> int:
        """Seconds to wait if the key is at/over the limit, else 0."""
        now = time.monotonic()
        with self._lock:
            items = self._prune(key, now)
            if len(items) >= self.max_events:
                return max(1, int(self.window - (now - items[0])))
            return 0

    def record(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            items = self._prune(key, now)
            items.append(now)
            self._events[key] = items

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


class MailBridgeApplication:
    def __init__(
        self,
        *,
        store: MailBridgeStore,
        logger: logging.Logger,
        api_token: str,
        inbound_token: str,
        default_domain: str = "",
        admin_username: str = "",
        admin_password_hash: str = "",
        session_secret: str = "",
    ):
        self.store = store
        self.logger = logger
        self.api_token = api_token.strip()
        self.inbound_token = inbound_token.strip()
        self.default_domain = normalize_address(default_domain)
        self.admin_username = normalize_username(admin_username)
        self.admin_password_hash = str(admin_password_hash or "").strip()
        self.session_secret = str(session_secret or "").strip() or self.api_token or "CHANGE_ME_SESSION_SECRET"
        self.login_limiter = RateLimiter(LOGIN_MAX_FAILURES, LOGIN_WINDOW_SECONDS)
        self.redeem_limiter = RateLimiter(REDEEM_MAX_FAILURES, REDEEM_WINDOW_SECONDS)

    def check_bearer(self, raw_header: str, expected_token: str) -> bool:
        token = str(raw_header or "").strip()
        if not expected_token:
            return True
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return secrets.compare_digest(token, expected_token)

    def check_admin(self, x_admin_auth: str, auth_header: str) -> bool:
        token = str(x_admin_auth or "").strip()
        if token:
            return self.check_bearer(token, self.api_token)
        return self.check_bearer(auth_header, self.api_token)

    def warn_on_weak_config(self) -> None:
        """Log a startup warning when the session secret is missing or still a
        placeholder — forgeable sessions are a production footgun."""
        secret = self.session_secret
        if not secret or secret.upper().startswith("CHANGE_ME"):
            self.logger.warning(
                "auth.session_secret is missing or a placeholder (%r); set a strong "
                "random value before production — sessions are otherwise forgeable.",
                secret,
            )

    def bootstrap_admin_user(self) -> None:
        # Configured admin (config.json / env) is ensured as before. Otherwise we
        # do NOT create any default admin — first run sets the password via the
        # web setup page. ponytail: no fallback creds, no shipped secret.
        if self.admin_username and self.admin_password_hash:
            self.store.ensure_admin_user(self.admin_username, self.admin_password_hash)
            self.logger.info("web admin bootstrap ensured for user=%s", self.admin_username)
            return
        if self.store.has_any_admin():
            return
        self.logger.warning(
            "no admin configured and none exists yet — set one via /web/admin/login (首次设置)."
        )

    def build_session_token_hash(self, token: str) -> str:
        material = f"{self.session_secret}:{str(token or '').strip()}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def create_user_session(self, user_id: int) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        token_hash = self.build_session_token_hash(token)
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=DEFAULT_SESSION_TTL_SECONDS)).isoformat().replace(
            "+00:00", "Z"
        )
        self.store.create_session(user_id, token_hash, expires_at)
        return token, expires_at


class MailBridgeHandler(BaseHTTPRequestHandler):
    server_version = "MailBridge/1.0"

    @property
    def app(self) -> MailBridgeApplication:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        self.app.logger.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, content: str) -> None:
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        *,
        content_type: str,
        filename: str = "",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_redirect(self, location: str, status: int = HTTPStatus.FOUND) -> None:
        self.send_response(status)
        self.send_header("Location", str(location or "/"))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _set_session_cookie(self, token: str, expires_at: str) -> None:
        parsed = parse_utc_iso(expires_at)
        if parsed:
            expires = parsed.strftime("%a, %d %b %Y %H:%M:%S GMT")
            value = (
                f"{SESSION_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Expires={expires}"
            )
        else:
            value = f"{SESSION_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax"
        self.send_header("Set-Cookie", value)

    def _clear_session_cookie(self) -> None:
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
        )

    def _extract_session_token(self) -> str:
        raw_cookie = str(self.headers.get("Cookie") or "").strip()
        if not raw_cookie:
            return ""
        jar = SimpleCookie()
        try:
            jar.load(raw_cookie)
        except Exception:
            return ""
        morsel = jar.get(SESSION_COOKIE_NAME)
        if not morsel:
            return ""
        return str(morsel.value or "").strip()

    def _client_ip(self) -> str:
        # Honor the first X-Forwarded-For hop when behind a reverse proxy,
        # otherwise the direct peer address.
        forwarded = str(self.headers.get("X-Forwarded-For") or "").strip()
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.client_address[0] if self.client_address else "?"

    def _current_session_user(self) -> Optional[dict[str, Any]]:
        token = self._extract_session_token()
        if not token:
            return None
        token_hash = self.app.build_session_token_hash(token)
        return self.app.store.get_user_by_session_token_hash(token_hash)

    def _require_session_user(self) -> Optional[dict[str, Any]]:
        user = self._current_session_user()
        if user:
            return user
        self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
        return None

    def _require_admin_session_user(self) -> Optional[dict[str, Any]]:
        user = self._current_session_user()
        if not user:
            self._send_redirect("/web/admin/login")
            return None
        if str(user.get("role") or "") != "admin":
            self._send_redirect("/web/admin/login")
            return None
        return user

    def _read_request_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        return self.rfile.read(max(0, length))

    def _read_json_body(self) -> Dict[str, Any]:
        raw = self._read_request_body()
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}

    def _parse_rfc822_payload(self, raw: bytes) -> Dict[str, Any]:
        if not raw:
            return {}

        raw_text = raw.decode("utf-8", errors="replace")
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        text_parts: list[str] = []
        html_parts: list[str] = []

        if parsed.is_multipart():
            for part in parsed.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                try:
                    content = part.get_content()
                except Exception:
                    content = ""
                if not isinstance(content, str):
                    continue
                content_type = part.get_content_type().lower()
                if content_type == "text/plain":
                    text_parts.append(content)
                elif content_type == "text/html":
                    html_parts.append(content)
        else:
            try:
                content = parsed.get_content()
            except Exception:
                content = ""
            if isinstance(content, str):
                content_type = parsed.get_content_type().lower()
                if content_type == "text/html":
                    html_parts.append(content)
                else:
                    text_parts.append(content)

        subject = decode_mime_header_value(self.headers.get("x-mail-subject") or parsed.get("subject") or "")
        to_address = normalize_address(decode_mime_header_value(self.headers.get("x-mail-to") or parsed.get("to") or ""))
        from_address = decode_mime_header_value(self.headers.get("x-mail-from") or parsed.get("from") or "")
        raw_headers = dict(parsed.items())

        message_id = str(self.headers.get("x-mail-message-id") or parsed.get("message-id") or "").strip()
        if message_id and "message-id" not in {key.lower() for key in raw_headers}:
            raw_headers["Message-ID"] = message_id

        return {
            "to": to_address,
            "from": from_address,
            "subject": subject,
            "text": "\n".join(part for part in text_parts if part).strip(),
            "html": "\n".join(part for part in html_parts if part).strip(),
            "body": raw_text,
            "raw": raw_text,
            "raw_mail": raw_text,
            "raw_header_text": extract_raw_header_text(raw_text),
            "headers": raw_headers,
            "received_at": utcnow_iso(),
        }

    def _read_inbound_payload(self) -> Dict[str, Any]:
        raw = self._read_request_body()
        if not raw:
            return {}

        content_type = str(self.headers.get("Content-Type") or "").lower()
        if "application/json" in content_type:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}

        return self._parse_rfc822_payload(raw)

    def _require_auth(self, expected_token: str) -> bool:
        if self.app.check_bearer(self.headers.get("Authorization", ""), expected_token):
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
        return False

    def _require_admin_auth(self) -> bool:
        if self.app.check_admin(self.headers.get("x-admin-auth", ""), self.headers.get("Authorization", "")):
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
        return False

    @staticmethod
    def _parse_int(value: str, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(str(value).strip())
        except Exception:
            parsed = default
        if parsed < minimum:
            return minimum
        if parsed > maximum:
            return maximum
        return parsed

    @staticmethod
    def _normalize_query_address(value: Any) -> str:
        # Query strings decode '+' as space. Mail addresses commonly use '+'
        # aliases, so restore them for address parameters only.
        return normalize_address(str(value or "").strip().replace(" ", "+"))

    @staticmethod
    def _compose_raw(subject: str, text: str, html: str, body: str) -> str:
        head = f"Subject: {subject}".strip() if subject else "Subject:"
        payload = text or body or html or ""
        return f"{head}\n\n{payload}".strip()

    @staticmethod
    def _mail_summary_payload(record: MailRecord) -> dict[str, Any]:
        preview = build_mail_preview_text(
            record.subject,
            record.text,
            record.html,
            record.body,
            record.verification_code,
            record.invite_link,
        )
        return {
            "id": record.message_id,
            "to": record.address,
            "from": record.from_address,
            "subject": record.subject,
            "received_at": record.received_at,
            "mail_type": record.mail_type,
            "verification_code": record.verification_code,
            "invite_link": record.invite_link,
            "preview": preview,
        }

    @staticmethod
    def _mail_detail_payload(record: MailRecord) -> dict[str, Any]:
        return {
            "id": record.message_id,
            "to": record.address,
            "from": record.from_address,
            "subject": record.subject,
            "text": record.text or record.body,
            "html": record.html,
            "body": record.body,
            "raw_mail": record.raw_mail,
            "raw_header_text": record.raw_header_text,
            "received_at": record.received_at,
            "verification_code": record.verification_code,
            "mail_type": record.mail_type,
            "invite_link": record.invite_link,
            "process_status": record.process_status,
        }

    @staticmethod
    def _normalize_domain(value: Any) -> str:
        domain = str(value or "").strip().lower()
        if domain.startswith("@"):
            domain = domain[1:]
        return domain

    @staticmethod
    def _build_cf_style_local_part() -> str:
        # 格式: yymmdd-xxxx (xxxx 为 4 位小写随机字母)
        date_part = datetime.now().strftime("%y%m%d")
        rand_part = "".join(secrets.choice(string.ascii_lowercase) for _ in range(4))
        return f"{date_part}-{rand_part}"

    def _resolve_new_address_domain(self, payload: Dict[str, Any]) -> str:
        # /admin/new_address must always return the fixed business domain.
        return self._normalize_domain(FORCED_NEW_ADDRESS_DOMAIN)

    @staticmethod
    def _extract_admin_user_id(path: str) -> int:
        parts = [part for part in path.split("/") if part]
        if len(parts) < 4 or parts[0] != "web" or parts[1] != "admin" or parts[2] != "users":
            return 0
        try:
            return int(parts[3])
        except Exception:
            return 0

    @staticmethod
    def _extract_admin_mailbox_id(path: str) -> int:
        parts = [part for part in path.split("/") if part]
        if len(parts) < 4 or parts[0] != "web" or parts[1] != "admin" or parts[2] != "mailboxes":
            return 0
        try:
            return int(parts[3])
        except Exception:
            return 0

    @staticmethod
    def _extract_admin_inbox_message_id(path: str) -> int:
        parts = [part for part in path.split("/") if part]
        if len(parts) < 4 or parts[0] != "web" or parts[1] != "admin" or parts[2] != "inbox":
            return 0
        try:
            return int(parts[3])
        except Exception:
            return 0

    @staticmethod
    def _render_admin_login_page() -> str:
        return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>管理员登录</title>
  <style>
    @import url("https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap");
    :root { --bg:#eef2ff; --card:#fff; --line:#d8e4f2; --fg:#10233e; --primary:#5b5ff6; --teal:#7c3aed; --muted:#5f708f; --warn:#8d2639; --ok:#006a5a; --shadow:0 20px 56px rgba(80,76,160,.16); }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; display:grid; place-items:center; font-family:"IBM Plex Sans","Noto Sans SC",sans-serif; color:var(--fg);
      background:
        radial-gradient(circle at 20% 0%, rgba(91,95,246,.18), transparent 30%),
        radial-gradient(circle at 85% 20%, rgba(124,58,237,.18), transparent 25%),
        linear-gradient(180deg,#f8faff 0%,#eef2ff 100%); }
    .shell { width:min(980px,100%); padding:18px; display:grid; grid-template-columns:1.1fr .9fr; gap:16px; }
    .hero, .panel { background:#fff; border:1px solid var(--line); border-radius:22px; box-shadow:var(--shadow); overflow:hidden; }
    .hero { position:relative; padding:28px; background:linear-gradient(135deg, rgba(91,95,246,.94) 0%, rgba(124,58,237,.94) 100%); color:#fff; }
    .hero::after { content:""; position:absolute; right:-80px; bottom:-80px; width:260px; height:260px; border-radius:999px; background:radial-gradient(circle, rgba(255,255,255,.18) 0%, rgba(255,255,255,0) 70%); }
    .badge { display:inline-flex; align-items:center; gap:8px; padding:8px 12px; border-radius:999px; background:rgba(255,255,255,.14); border:1px solid rgba(255,255,255,.18); font-size:12px; }
    .dot { width:8px; height:8px; border-radius:999px; background:#d1fae5; box-shadow:0 0 0 6px rgba(209,250,229,.18); }
    .hero h1 { margin:16px 0 10px; font-family:"Space Grotesk","Noto Sans SC",sans-serif; font-size:36px; letter-spacing:.4px; }
    .hero p { margin:0; max-width:46ch; line-height:1.7; color:rgba(255,255,255,.92); }
    .hint-list { margin-top:18px; display:grid; gap:10px; }
    .hint-item { padding:10px 12px; border-radius:14px; background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.16); }
    .panel { padding:24px 22px; }
    .panel h2 { margin:0 0 8px; font-family:"Space Grotesk","Noto Sans SC",sans-serif; font-size:26px; }
    .panel p { margin:0 0 16px; color:var(--muted); line-height:1.6; }
    .field { margin-bottom:12px; }
    label { display:block; font-size:13px; color:var(--muted); margin-bottom:6px; }
    input { width:100%; min-height:46px; border:1px solid #c8d3e6; border-radius:14px; padding:10px 12px; font:inherit; outline:none; }
    input:focus { border-color:#6f86ff; box-shadow:0 0 0 3px rgba(91,95,246,.12); }
    button { width:100%; min-height:46px; border:none; border-radius:14px; padding:10px 14px; font:inherit; font-weight:700; cursor:pointer; color:#fff; background:linear-gradient(135deg,var(--primary),var(--teal)); box-shadow:0 12px 28px rgba(91,95,246,.22); }
    .status { min-height:22px; margin-top:12px; font-size:13px; color:#17457c; }
    .status.error { color:var(--warn); }
    .status.ok { color:var(--ok); }
    .sub-link { margin-top:14px; text-align:center; font-size:13px; color:var(--muted); }
    .sub-link a { color:var(--primary); text-decoration:none; font-weight:700; }
    @media (max-width: 880px) { .shell { grid-template-columns:1fr; } .hero h1 { font-size:30px; } }
    @media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition:none !important; animation:none !important; } }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="badge"><span class="dot"></span><span>Admin Access</span></div>
      <h1>邮箱密钥管理后台</h1>
      <p>管理员登录后可创建查询邮箱、生成随机密钥、停用或重置凭据，并查看每个邮箱最近邮件活动。</p>
      <div class="hint-list">
        <div class="hint-item">1. 使用管理员账号登录后台</div>
        <div class="hint-item">2. 新增邮箱后系统自动生成密钥</div>
        <div class="hint-item">3. 用户通过“邮箱----密钥”在前台查询</div>
      </div>
    </section>
    <section class="panel">
      <h2>管理员登录</h2>
      <p>此页面仅用于后台入口，普通用户查询请访问前台查询页。</p>
      <form id="login-form">
        <div class="field">
          <label for="login-user">管理员用户名</label>
          <input id="login-user" autocomplete="username" placeholder="请输入管理员用户名">
        </div>
        <div class="field">
          <label for="login-pass">管理员密码</label>
          <input id="login-pass" type="password" autocomplete="current-password" placeholder="请输入管理员密码">
        </div>
        <button id="btn-login" type="submit">登录后台</button>
      </form>
      <div id="status" class="status"></div>
      <div class="sub-link">前台查询入口：<a href="/web/query">/web/query</a></div>
    </section>
  </div>
<script>
async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "include", headers: { "Content-Type": "application/json" }, ...opts });
  const text = await r.text();
  let data = {};
  try { data = JSON.parse(text || "{}"); } catch {}
  return { status: r.status, data };
}
function el(id) { return document.getElementById(id); }
function setStatus(text, kind = "") {
  const node = el("status");
  node.textContent = text;
  node.classList.remove("ok", "error");
  if (kind) node.classList.add(kind);
}
let needsSetup = false;
el("login-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const username = el("login-user").value.trim();
  const password = el("login-pass").value.trim();
  if (!username || !password) {
    setStatus("请输入管理员用户名和密码", "error");
    return;
  }
  if (needsSetup) {
    if (password.length < 8) { setStatus("管理员密码至少 8 位", "error"); return; }
    setStatus("正在创建管理员...", "");
    const res = await api("/web/auth/setup", { method: "POST", body: JSON.stringify({ username, password }) });
    if (res.status === 200 && res.data.ok) {
      setStatus("管理员已创建，正在进入后台...", "ok");
      location.href = "/web/admin";
      return;
    }
    setStatus(`创建失败: ${res.data.error || "操作失败"}`, "error");
    return;
  }
  setStatus("正在登录...", "");
  const res = await api("/web/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
  if (res.status === 200 && res.data.ok && (((res.data.user || {}).role || "").toLowerCase() === "admin")) {
    setStatus("登录成功，正在跳转后台...", "ok");
    location.href = "/web/admin";
    return;
  }
  setStatus(`登录失败: ${res.data.error || "操作失败"}`, "error");
};
(async () => {
  const me = await api("/web/me");
  if (me.status === 200 && me.data.ok && (((me.data.user || {}).role || "").toLowerCase() === "admin")) {
    location.href = "/web/admin";
    return;
  }
  const st = await api("/web/auth/admin-status");
  if (st.status === 200 && st.data.ok && st.data.needs_setup) {
    needsSetup = true;
    document.querySelector(".panel h2").textContent = "首次设置管理员";
    document.querySelector(".panel p").textContent = "系统尚未设置管理员，请创建首个管理员账号（密码至少 8 位）。";
    el("btn-login").textContent = "创建管理员";
    el("login-pass").setAttribute("autocomplete", "new-password");
    el("login-pass").setAttribute("placeholder", "请设置管理员密码（至少 8 位）");
  }
})();
</script>
</body>
</html>"""

    @staticmethod
    def _render_login_page() -> str:
        return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>邮箱兑换 & 邮件查看</title>
  <style>
    @import url("https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap");
    :root { --bg:#eef2ff; --card:#ffffff; --line:#e2e8f5; --fg:#1f2937; --primary:#5b5ff6; --primary2:#7c3aed; --accent:#8b5cf6; --muted:#667085; --shadow:0 18px 42px rgba(80,76,160,.12); --good:#157347; --bad:#b42318; }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; font-family:"IBM Plex Sans","Noto Sans SC",sans-serif; color:var(--fg);
      background:
        radial-gradient(circle at 20% 0%, rgba(91,95,246,.18), transparent 30%),
        radial-gradient(circle at 80% 20%, rgba(124,58,237,.18), transparent 25%),
        linear-gradient(180deg,#f8faff 0%,#eef2ff 100%);
    }
    .page-wrapper { min-height:100vh; display:flex; flex-direction:column; position:relative; overflow:hidden; }
    .page-wrapper::before { content:""; position:fixed; inset:0; pointer-events:none; opacity:.22; background-image:
      linear-gradient(rgba(91,95,246,.04) 1px, transparent 1px),
      linear-gradient(90deg, rgba(91,95,246,.04) 1px, transparent 1px);
      background-size: 36px 36px; }
    .app-header { position:relative; min-height:220px; overflow:hidden; }
    .app-header::before { content:""; position:absolute; inset:0; background:
      linear-gradient(135deg, rgba(91,95,246,.88) 0%, rgba(124,58,237,.88) 100%),
      url('data:image/svg+xml;utf8,<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"1600\" height=\"400\" viewBox=\"0 0 1600 400\"><defs><linearGradient id=\"g\" x1=\"0\" x2=\"1\" y1=\"0\" y2=\"1\"><stop stop-color=\"%23ffffff\" stop-opacity=\"0.18\"/><stop offset=\"1\" stop-color=\"%23ffffff\" stop-opacity=\"0.02\"/></linearGradient></defs><path d=\"M0 300 C260 180 420 360 700 250 C980 140 1180 330 1600 170 L1600 0 L0 0 Z\" fill=\"url(%23g)\"/></svg>');
      background-size:cover; }
    .app-header::after { content:""; position:absolute; inset:auto -120px -120px auto; width:420px; height:420px; border-radius:999px; background:radial-gradient(circle, rgba(255,255,255,.18) 0%, rgba(255,255,255,0) 70%); filter:blur(4px); }
    .header-inner { position:relative; z-index:1; max-width:1100px; margin:0 auto; padding:32px 18px 64px; display:flex; align-items:center; justify-content:center; flex-direction:column; gap:12px; text-align:center; }
    .hero-badge { display:inline-flex; align-items:center; gap:8px; border-radius:999px; padding:8px 14px; background:rgba(255,255,255,.14); border:1px solid rgba(255,255,255,.22); color:#fff; font-size:13px; letter-spacing:.4px; backdrop-filter:blur(8px); }
    .hero-badge-dot { width:8px; height:8px; border-radius:999px; background:#d1fae5; box-shadow:0 0 0 6px rgba(209,250,229,.16); }
    .header-inner h1 { margin:0; color:#fff; font-size:40px; font-family:"Space Grotesk","Noto Sans SC",sans-serif; letter-spacing:1px; text-shadow:0 10px 30px rgba(29,25,84,.28); }
    .header-inner p { margin:0; color:rgba(255,255,255,.95); background:rgba(255,255,255,.12); padding:10px 18px; border-radius:999px; border:1px solid rgba(255,255,255,.18); }
    .hero-note { color:rgba(255,255,255,.84); max-width:760px; line-height:1.65; font-size:14px; }
    .app-main { width:min(1120px,100%); margin:-54px auto 0; padding:0 16px 34px; position:relative; z-index:2; }
    .card { background:var(--card); border:1px solid var(--line); border-radius:20px; box-shadow:var(--shadow); }
    .search-panel { padding:22px; background:linear-gradient(180deg,#ffffff 0%,#fafbff 100%); }
    .search-caption { margin:0 0 14px; display:flex; justify-content:space-between; gap:10px; align-items:center; flex-wrap:wrap; }
    .search-caption-title { font-family:"Space Grotesk","Noto Sans SC",sans-serif; font-size:21px; letter-spacing:.4px; }
    .search-caption-sub { color:var(--muted); font-size:13px; }
    .search-input-group { display:flex; align-items:center; gap:12px; background:linear-gradient(180deg,#fbfcff 0%,#f7f8ff 100%); border:1px solid #d8def1; border-radius:20px; padding:12px 12px 12px 16px; box-shadow:inset 0 1px 0 rgba(255,255,255,.8); }
    .search-icon { font-size:20px; color:#6d63ff; filter:drop-shadow(0 4px 8px rgba(109,99,255,.18)); }
    .search-input-group input { flex:1; min-height:50px; border:none; background:transparent; outline:none; font:inherit; font-size:16px; color:#1f2937; }
    .search-input-group input::placeholder { color:#98a2b3; }
    .button-primary,.button-secondary,.button-ghost { min-height:44px; border-radius:12px; border:1px solid transparent; font:inherit; cursor:pointer; padding:10px 16px; font-weight:600; transition:transform .18s ease, box-shadow .18s ease, border-color .18s ease; }
    .button-primary { background:linear-gradient(135deg,var(--primary),var(--primary2)); color:#fff; box-shadow:0 10px 20px rgba(91,95,246,.25); }
    .button-secondary { background:#fff; color:var(--fg); border-color:#cdd6ec; }
    .button-ghost { background:#f5f7ff; color:#364152; border-color:#d8def1; }
    .button-primary:hover,.button-secondary:hover,.button-ghost:hover { transform:translateY(-1px); }
    .message-area { min-height:22px; margin:14px 0 0; font-size:14px; }
    .message-area.ok { color:var(--good); }
    .message-area.error { color:var(--bad); }
    .results-panel { margin-top:14px; }
    .total-results-info { color:var(--muted); font-size:13px; text-align:center; margin-top:10px; }
    .email-list { display:grid; gap:12px; }
    .email-item { position:relative; overflow:hidden; background:linear-gradient(180deg,#fff 0%,#fcfcff 100%); border:1px solid var(--line); border-radius:20px; padding:16px 16px 14px; box-shadow:var(--shadow); cursor:pointer; transition:transform .18s ease,border-color .18s ease, box-shadow .18s ease; }
    .email-item::before { content:""; position:absolute; inset:0 0 auto 0; height:3px; background:linear-gradient(90deg,var(--primary),var(--accent),#22c55e); opacity:.95; }
    .email-item:hover { transform:translateY(-2px); border-color:#cbd5ff; box-shadow:0 22px 44px rgba(80,76,160,.15); }
    .email-top { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }
    .email-subject { font-size:17px; font-weight:700; margin:0; line-height:1.45; }
    .email-meta { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; color:var(--muted); font-size:13px; }
    .pill { display:inline-flex; align-items:center; border-radius:999px; padding:4px 10px; background:#f5f7ff; border:1px solid #d8def1; font-size:12px; color:#3b4a67; }
    .email-preview { margin-top:12px; color:#475467; line-height:1.6; min-height:44px; }
    .email-actions { display:flex; justify-content:flex-end; margin-top:12px; }
    .admin-entry { margin-top:14px; text-align:center; color:var(--muted); font-size:13px; }
    .admin-entry a { color:var(--primary); font-weight:700; text-decoration:none; }
    .query-hints { display:flex; flex-wrap:wrap; gap:8px; justify-content:center; margin-top:14px; }
    .query-chip { display:inline-flex; align-items:center; gap:8px; padding:8px 12px; border-radius:999px; border:1px solid #e3e8f6; background:#fbfbff; color:#4b5563; font-size:12px; }
    .query-chip b { color:#2f3d59; }
    .mail-kv { display:flex; gap:10px; flex-wrap:wrap; margin-top:10px; }
    .mail-kv-item { display:inline-flex; align-items:center; gap:6px; padding:6px 10px; border-radius:999px; background:#f8f9ff; border:1px solid #e6eafd; color:#475467; font-size:12px; }
    .empty-state { text-align:center; padding:36px 18px; color:var(--muted); background:linear-gradient(180deg,#fff 0%,#fbfcff 100%); border:1px dashed #d8def1; border-radius:20px; }
    .modal { position:fixed; inset:0; background:rgba(15,23,42,.52); display:none; align-items:center; justify-content:center; padding:16px; z-index:40; backdrop-filter:blur(10px); }
    .modal.open { display:flex; }
    .modal-dialog { width:min(960px,100%); }
    .modal-content { background:#fff; border-radius:24px; overflow:hidden; box-shadow:0 28px 84px rgba(15,23,42,.32); border:1px solid rgba(255,255,255,.45); }
    .modal-header { display:flex; justify-content:space-between; gap:16px; align-items:center; padding:18px 22px; border-bottom:1px solid var(--line); }
    .modal-title { margin:0; font-size:20px; }
    .close-button { border:none; background:#eef2ff; width:36px; height:36px; border-radius:999px; font-size:22px; cursor:pointer; }
    .modal-body { padding:18px 22px 22px; }
    .email-meta-modal { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; color:#344054; font-size:14px; }
    .email-meta-modal p { margin:0; }
    .modal-header-copy { color:var(--muted); font-size:13px; margin-top:4px; }
    .modal-content-switch { display:flex; gap:8px; flex-wrap:wrap; margin-top:14px; }
    .modal-switch { background:#f4f7ff; color:#284167; border:1px solid #d8e3ff; }
    .modal-switch.active { background:linear-gradient(120deg,var(--primary),var(--teal)); color:#fff; border-color:var(--primary); }
    iframe { width:100%; min-height:420px; border:none; border-radius:14px; background:#fff; }
    .modal-fallback { display:none; white-space:pre-wrap; word-break:break-word; background:#f9faff; border:1px solid var(--line); border-radius:14px; padding:14px; min-height:220px; }
    .login-card { margin-top:16px; padding:18px 20px; background:linear-gradient(180deg,#fff 0%,#fcfcff 100%); }
    .login-row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
    .login-row input { flex:1; min-width:160px; min-height:42px; border:1px solid #d0d8ed; border-radius:12px; padding:10px 12px; font:inherit; }
    .login-status { min-height:20px; margin-top:10px; font-size:13px; color:var(--muted); }
    @media (max-width: 720px) {
      .search-input-group { flex-direction:column; align-items:stretch; }
      .email-top { flex-direction:column; }
      .email-meta-modal { grid-template-columns:1fr; }
      .header-inner h1 { font-size:30px; }
      .search-caption { align-items:flex-start; }
    }
    @media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } }
    .tabbar { display:inline-flex; gap:4px; margin-bottom:16px; padding:5px; background:#eef1fb; border:1px solid #e0e5f5; border-radius:16px; }
    .tab-btn { min-height:42px; border-radius:12px; border:none; background:transparent; color:#667085; font:inherit; font-weight:700; cursor:pointer; padding:9px 24px; box-shadow:none; transition:color .18s ease, background .18s ease; }
    .tab-btn:not(.active):hover { color:#475467; background:rgba(255,255,255,.5); }
    .tab-btn.active { background:#fff; color:var(--primary); box-shadow:0 2px 6px rgba(16,24,40,.08); }
    .mailbox-list { display:grid; gap:10px; }
    .mailbox-row { display:flex; align-items:center; gap:10px; flex-wrap:wrap; padding:10px 14px; border:1px solid var(--line); border-radius:14px; background:linear-gradient(180deg,#fff 0%,#fbfcff 100%); }
    .mailbox-row .addr { font-weight:600; color:#1f2937; word-break:break-all; flex:1; min-width:160px; }
    .mailbox-row button { min-height:38px; padding:8px 14px; }
  </style>
</head>
<body>
  <div class="page-wrapper">
    <header class="app-header">
      <div class="header-inner">
        <div class="hero-badge"><span class="hero-badge-dot"></span><span>Redeem &amp; Mailbox Lookup</span></div>
        <h1>邮箱兑换中心</h1>
        <p>输入卡密兑换邮箱 · 凭密钥查收邮件</p>
        <div class="hero-note">散户无需注册：输入 CDK 卡密即可领取邮箱，凭据自动保存到本机，随时回来查收验证码、邀请等邮件。需要长期管理可注册账号。</div>
      </div>
    </header>
    <main class="app-main">
      <div class="tabbar">
        <button class="tab-btn active" data-tab-btn="redeem" type="button">🎁 兑换 CDK</button>
        <button class="tab-btn" data-tab-btn="query" type="button">🔎 邮件查询</button>
      </div>

      <div class="tab-panel" data-tab="redeem">
        <section class="search-panel card">
          <div class="search-caption">
            <div>
              <div class="search-caption-title">输入卡密兑换邮箱</div>
              <div class="search-caption-sub">兑换成功后邮箱凭据自动保存到本机，可随时切到「邮件查询」查收</div>
            </div>
            <div class="query-chip"><b>格式</b><span>CDK-XXXXX-XXXXX-XXXXX-XXXXX</span></div>
          </div>
          <div class="search-input-group">
            <span class="search-icon">🎁</span>
            <input id="cdk-code" autocomplete="off" placeholder="请输入卡密，例如 CDK-ABCDE-FGHJK-LMNPQ-RSTUV">
            <button id="btn-redeem" class="button-primary" type="button">立即兑换</button>
          </div>
          <div id="redeem-status" class="message-area"></div>
          <div id="redeem-result" class="email-list" style="margin-top:14px"></div>
          <div class="admin-entry">需要长期管理已购邮箱？ <a href="/web/user">登录 / 注册账号 →</a></div>
        </section>
      </div>

      <div class="tab-panel" data-tab="query" hidden>
        <section class="search-panel card" id="my-mailbox-panel" style="display:none; margin-bottom:14px">
          <div class="search-caption">
            <div>
              <div class="search-caption-title">我的邮箱（本机缓存）</div>
              <div class="search-caption-sub">点击「查邮件」自动填入密钥并查询</div>
            </div>
          </div>
          <div id="my-mailbox-list" class="mailbox-list"></div>
        </section>
        <section class="search-panel card">
          <div class="search-caption">
            <div>
              <div class="search-caption-title">邮箱凭据查询</div>
              <div class="search-caption-sub">输入完整凭据后，一次性查看该邮箱最近 20 封邮件</div>
            </div>
            <div class="query-chip"><b>格式</b><span>邮箱----密钥</span></div>
          </div>
          <div class="search-input-group">
            <span class="search-icon">🔎</span>
            <input id="credential-input" autocomplete="off" placeholder="请输入 邮箱----密钥 格式，例如 lofts.terns2i@icloud.com----cw7FQRu346oV">
            <button id="search-button" class="button-primary" type="button">搜索</button>
          </div>
          <label style="display:inline-flex; align-items:center; gap:6px; margin-top:10px; font-size:13px; color:var(--muted); cursor:pointer;">
            <input type="checkbox" id="auto-refresh"> 自动刷新（每 5 秒，方便等验证码邮件）
          </label>
          <div id="totalResultsInfo" class="total-results-info"></div>
          <div id="messageArea" class="message-area"></div>
          <div class="query-hints">
            <div class="query-chip"><b>支持</b><span>验证码 / 邀请邮件 / 普通邮件</span></div>
            <div class="query-chip"><b>详情</b><span>点击列表可查看完整正文</span></div>
            <div class="query-chip"><b>安全</b><span>密钥错误将拒绝访问</span></div>
          </div>
        </section>
        <section class="results-panel">
          <div id="resultsArea" class="email-list">
            <div class="empty-state">请输入“邮箱----密钥”后查询邮件列表</div>
          </div>
        </section>
      </div>
    </main>
  </div>
  <div id="emailModal" class="modal">
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <div>
            <h2 id="modalSubject" class="modal-title">邮件内容</h2>
            <div class="modal-header-copy">支持 HTML 正文预览与纯文本回退</div>
          </div>
          <button type="button" class="close-button" id="closeModalButton" aria-label="关闭">×</button>
        </div>
        <div class="modal-body">
          <div class="email-meta-modal">
            <p><strong>发件人：</strong><span id="modalFrom"></span></p>
            <p><strong>收件人：</strong><span id="modalTo"></span></p>
            <p><strong>日期：</strong><span id="modalDate"></span></p>
          </div>
          <div style="margin-top:14px">
            <span id="modalType" class="pill">未分类</span>
          </div>
          <div class="modal-content-switch">
            <button type="button" id="viewRenderedButton" class="modal-switch active">正文预览</button>
            <button type="button" id="viewRawButton" class="modal-switch">邮件源码</button>
          </div>
          <div style="margin-top:14px">
            <iframe id="emailBodyFrame" src="about:blank" title="邮件正文"></iframe>
            <pre id="emailBodyFallback" class="modal-fallback"></pre>
          </div>
        </div>
      </div>
    </div>
  </div>
<script>
async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "include", headers: { "Content-Type": "application/json" }, ...opts });
  const text = await r.text();
  let data = {};
  try { data = JSON.parse(text || "{}"); } catch {}
  return { status: r.status, data };
}
function el(id) { return document.getElementById(id); }
let currentAddress = "";
let currentKey = "";
let currentCredential = "";
let currentItems = [];
let currentDetailEmail = null;
function setStatus(text, kind = "") {
  const node = el("messageArea");
  node.textContent = text;
  node.className = "message-area" + (kind ? ` ${kind}` : "");
}
function parseCredential(raw) {
  const value = String(raw || "").trim();
  const idx = value.indexOf("----");
  if (idx <= 0) return { address: "", key: "" };
  return { address: value.slice(0, idx).trim(), key: value.slice(idx + 4).trim() };
}
function renderEmpty(text) {
  el("resultsArea").innerHTML = `<div class="empty-state">${text}</div>`;
}
function renderResults(items, total) {
  const area = el("resultsArea");
  if (!items.length) {
    renderEmpty("该邮箱暂无邮件");
    return;
  }
  const typeLabel = (value) => {
    if (value === "verification_code") return "验证码";
    if (value === "team_invite") return "邀请邮件";
    return value || "普通邮件";
  };
  area.innerHTML = items.map((item) => `
    <article class="email-item" data-id="${item.id}">
      <div class="email-top">
        <div>
          <h3 class="email-subject">${item.subject || "(无主题)"}</h3>
          <div class="email-meta">
            <span class="pill">${typeLabel(item.mail_type)}</span>
            <span>发件人：${item.from || "-"}</span>
            <span>收件人：${item.to || "-"}</span>
          </div>
        </div>
        <div class="pill">${formatBeijingDateTime(item.received_at)}</div>
      </div>
      <div class="mail-kv">
        ${item.verification_code ? `<span class="mail-kv-item">验证码 <b>${item.verification_code}</b></span>` : ""}
        ${item.invite_link ? `<span class="mail-kv-item">邀请链接 已提取</span>` : ""}
      </div>
      <div class="email-preview">${item.preview || "（无摘要）"}</div>
      <div class="email-actions"><button class="button-ghost" data-open="${item.id}" type="button">查看详情</button></div>
    </article>
  `).join("");
  el("totalResultsInfo").textContent = `共找到 ${total} 封邮件，当前展示 ${items.length} 封`;
}
function escapeHtml(value) {
  return String(value || "").replace(/[&<>"]/g, (ch) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[ch] || ch));
}
const beijingDateTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false
});
function formatBeijingDateTime(value) {
  const text = String(value || "").trim();
  if (!text) return "-";
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return text;
  const parts = {};
  for (const part of beijingDateTimeFormatter.formatToParts(parsed)) {
    if (part.type !== "literal") parts[part.type] = part.value;
  }
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}
function getQueryErrorMessage(errorCode) {
  const code = String(errorCode || "").trim();
  if (code === "invalid_credential") return "邮箱或密钥错误，请检查后重试";
  if (code === "mailbox_inactive") return "该邮箱已停用，请联系管理员";
  if (code === "missing_address" || code === "missing_access_key") return "请输入正确的邮箱凭据后再查询";
  return "查询失败，请稍后重试";
}
function getQueryEmptyMessage(errorCode) {
  const code = String(errorCode || "").trim();
  if (code === "invalid_credential") return "邮箱或密钥错误，请检查后重试";
  if (code === "mailbox_inactive") return "该邮箱已停用，请联系管理员";
  if (code === "missing_address" || code === "missing_access_key") return "请输入正确的邮箱凭据后再查询";
  return "暂无可展示结果";
}
let autoRefreshTimer = null;
let lastTopMailId = null;
function stopAutoRefresh() {
  if (autoRefreshTimer) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; }
  const cb = el("auto-refresh");
  if (cb) cb.checked = false;
}
function startAutoRefresh() {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer);
  autoRefreshTimer = setInterval(() => {
    const cb = el("auto-refresh");
    if (!cb || !cb.checked) { stopAutoRefresh(); return; }
    if (document.hidden) return;  // pause when tab not visible
    queryMails(true);
  }, 5000);
}
async function queryMails(silent) {
  const credential = el("credential-input").value.trim();
  const parsed = parseCredential(credential);
  if (!parsed.address || !parsed.key) {
    setStatus("请输入正确的 邮箱----密钥 格式", "error");
    renderEmpty("请输入正确的邮箱凭据后再查询");
    el("totalResultsInfo").textContent = "";
    stopAutoRefresh();
    return;
  }
  currentAddress = parsed.address.toLowerCase();
  currentKey = parsed.key;
  currentCredential = credential;
  if (!silent) setStatus("查询中...", "");
  const res = await api("/web/query-mails", { method: "POST", body: JSON.stringify({ credential }) });
  if (res.status === 200 && res.data.ok) {
    currentItems = res.data.emails || [];
    renderResults(currentItems, Number(res.data.total || currentItems.length || 0));
    const topId = currentItems.length ? currentItems[0].id : null;
    if (silent && topId && lastTopMailId !== null && topId !== lastTopMailId) {
      setStatus("🔔 收到新邮件", "ok");
    } else if (!silent) {
      setStatus("查询成功", "ok");
    }
    lastTopMailId = topId;
    return;
  }
  // On error during silent polling, stop to avoid hammering a bad credential.
  if (silent) { stopAutoRefresh(); }
  const friendlyMessage = getQueryErrorMessage(res.data.error);
  currentItems = [];
  renderEmpty(getQueryEmptyMessage(res.data.error));
  el("totalResultsInfo").textContent = "";
  setStatus(friendlyMessage, "error");
}
function closeModal() {
  el("emailModal").classList.remove("open");
  el("emailBodyFrame").src = "about:blank";
  currentDetailEmail = null;
}
function setModalView(view) {
  const email = currentDetailEmail || {};
  const frame = el("emailBodyFrame");
  const fallback = el("emailBodyFallback");
  const rawButton = el("viewRawButton");
  const renderedButton = el("viewRenderedButton");
  const showRaw = view === "raw";
  rawButton.classList.toggle("active", showRaw);
  renderedButton.classList.toggle("active", !showRaw);
  if (showRaw) {
    frame.style.display = "none";
    fallback.style.display = "block";
    fallback.textContent = email.raw_mail || email.body || email.text || "（无正文）";
    return;
  }
  if (email.html) {
    frame.style.display = "block";
    fallback.style.display = "none";
    frame.srcdoc = email.html;
    return;
  }
  frame.style.display = "none";
  fallback.style.display = "block";
  fallback.textContent = email.text || email.body || email.raw_mail || "（无正文）";
}
async function openMailDetail(mailId) {
  if (!currentAddress || !currentKey) return;
  const res = await api("/web/query-mail-detail", {
    method: "POST",
    body: JSON.stringify({ address: currentAddress, key: currentKey, id: Number(mailId || 0) })
  });
  if (!(res.status === 200 && res.data.ok && res.data.email)) {
    setStatus(`加载详情失败: ${res.data.error || "操作失败"}`, "error");
    return;
  }
  const email = res.data.email;
  currentDetailEmail = email;
  el("modalSubject").textContent = email.subject || "(无主题)";
  el("modalFrom").textContent = email.from || "-";
  el("modalTo").textContent = email.to || "-";
  el("modalDate").textContent = formatBeijingDateTime(email.received_at);
  el("modalType").textContent = email.mail_type || "unknown";
  setModalView("rendered");
  el("emailModal").classList.add("open");
}
el("search-button").onclick = async () => { await queryMails(); };
el("auto-refresh").addEventListener("change", (ev) => {
  if (ev.target.checked) {
    if (!el("credential-input").value.trim()) {
      setStatus("请先查询一次再开启自动刷新", "error");
      ev.target.checked = false;
      return;
    }
    startAutoRefresh();
    queryMails(true);
  } else {
    stopAutoRefresh();
  }
});
el("credential-input").addEventListener("keydown", async (ev) => {
  if (ev.key === "Enter") {
    ev.preventDefault();
    await queryMails();
  }
});
el("resultsArea").addEventListener("click", async (ev) => {
  const target = ev.target;
  if (!target) return;
  const id = target.dataset ? (target.dataset.open || "") : "";
  if (id) {
    await openMailDetail(id);
  }
});
el("closeModalButton").onclick = closeModal;
el("viewRawButton").onclick = () => setModalView("raw");
el("viewRenderedButton").onclick = () => setModalView("rendered");
el("emailModal").addEventListener("click", (ev) => {
  if (ev.target === el("emailModal")) closeModal();
});

// ---- Tab 切换 ----
function switchTab(name) {
  document.querySelectorAll("[data-tab-btn]").forEach((b) => b.classList.toggle("active", b.dataset.tabBtn === name));
  document.querySelectorAll("[data-tab]").forEach((p) => { p.hidden = p.dataset.tab !== name; });
}
document.querySelectorAll("[data-tab-btn]").forEach((b) => { b.onclick = () => switchTab(b.dataset.tabBtn); });

// ---- 本地「我的邮箱」缓存（凭据明文存本机，仅本浏览器可见）----
const MB_LS_KEY = "mb_mailboxes";
function loadMailboxes() {
  try { const v = JSON.parse(localStorage.getItem(MB_LS_KEY) || "[]"); return Array.isArray(v) ? v : []; }
  catch { return []; }
}
function saveMailboxes(list) {
  const byAddr = new Map(loadMailboxes().map((m) => [m.address, m]));
  for (const m of list || []) {
    if (!m || !m.address) continue;
    const key = m.access_key || m.key || "";
    byAddr.set(m.address, {
      address: m.address,
      key,
      credential: m.credential || (m.address + "----" + key),
      code: m.code || "",
      ts: Date.now()
    });
  }
  const merged = Array.from(byAddr.values()).sort((a, b) => (b.ts || 0) - (a.ts || 0)).slice(0, 100);
  localStorage.setItem(MB_LS_KEY, JSON.stringify(merged));
  renderMyMailboxes();
}
function removeMailbox(address) {
  localStorage.setItem(MB_LS_KEY, JSON.stringify(loadMailboxes().filter((m) => m.address !== address)));
  renderMyMailboxes();
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}
function renderMyMailboxes() {
  const panel = el("my-mailbox-panel");
  const box = el("my-mailbox-list");
  if (!panel || !box) return;
  const list = loadMailboxes();
  if (!list.length) { panel.style.display = "none"; box.innerHTML = ""; return; }
  panel.style.display = "";
  box.innerHTML = list.map((m) => `
    <div class="mailbox-row" data-addr="${escapeHtml(m.address)}">
      <span class="addr">${escapeHtml(m.address)}</span>
      <button class="button-primary" data-act="view" type="button">查邮件</button>
      <button class="button-ghost" data-act="copy" type="button">复制</button>
      <button class="button-ghost" data-act="del" type="button">删除</button>
    </div>`).join("");
}
el("my-mailbox-list").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button");
  const row = ev.target.closest(".mailbox-row");
  if (!btn || !row) return;
  const address = row.dataset.addr;
  const rec = loadMailboxes().find((m) => m.address === address);
  if (!rec) return;
  const act = btn.dataset.act;
  if (act === "del") { removeMailbox(address); return; }
  if (act === "copy") { await copyText(rec.credential); btn.textContent = "已复制"; setTimeout(() => { btn.textContent = "复制"; }, 1200); return; }
  if (act === "view") { el("credential-input").value = rec.credential; await queryMails(); }
});

// ---- 兑换 CDK ----
function getRedeemErrorMessage(code) {
  const c = String(code || "");
  if (c === "cdk_not_found") return "卡密不存在，请检查后重试";
  if (c === "cdk_used") return "该卡密已被使用";
  if (c === "cdk_expired") return "该卡密已过期";
  if (c === "cdk_disabled") return "该卡密已停用";
  if (c === "insufficient_stock") return "库存不足，请联系卖家补货";
  if (c === "missing_code") return "请输入卡密";
  if (c === "too_many_attempts") return "操作过于频繁，请稍后再试";
  if (c === "payload_too_large") return "请求内容过大";
  return "兑换失败，请稍后重试";
}
function setRedeemStatus(text, kind = "") {
  const node = el("redeem-status");
  node.textContent = text;
  node.className = "message-area" + (kind ? ` ${kind}` : "");
}
async function redeem() {
  const code = el("cdk-code").value.trim();
  if (!code) { setRedeemStatus("请输入卡密", "error"); return; }
  setRedeemStatus("兑换中...", "");
  const res = await api("/web/user/redeem", { method: "POST", body: JSON.stringify({ code }) });
  if (res.status === 200 && res.data.ok) {
    const boxes = res.data.mailboxes || [];
    boxes.forEach((m) => { m.code = code; });
    saveMailboxes(boxes);
    setRedeemStatus(`兑换成功，已发放 ${boxes.length} 个邮箱（已存入本机「我的邮箱」）`, "ok");
    el("cdk-code").value = "";
    el("redeem-result").innerHTML = boxes.map((m) => `
      <article class="email-item">
        <div class="email-top"><div>
          <h3 class="email-subject">${escapeHtml(m.address)}</h3>
          <div class="email-meta"><span class="pill">密钥</span><span>${escapeHtml(m.access_key || m.key || "")}</span></div>
        </div></div>
        <div class="email-actions">
          <button class="button-ghost" data-redeem-copy="${escapeHtml(m.credential)}" type="button">复制凭据</button>
          <button class="button-primary" data-redeem-view="${escapeHtml(m.credential)}" type="button">查邮件</button>
        </div>
      </article>`).join("");
    return;
  }
  el("redeem-result").innerHTML = "";
  setRedeemStatus(getRedeemErrorMessage(res.data.error), "error");
}
el("btn-redeem").onclick = redeem;
el("cdk-code").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); redeem(); } });
el("redeem-result").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button");
  if (!btn) return;
  if (btn.dataset.redeemCopy != null) { await copyText(btn.dataset.redeemCopy); btn.textContent = "已复制"; setTimeout(() => { btn.textContent = "复制凭据"; }, 1200); return; }
  if (btn.dataset.redeemView != null) { el("credential-input").value = btn.dataset.redeemView; switchTab("query"); await queryMails(); }
});
renderMyMailboxes();
</script>
</body>
</html>"""

    @staticmethod
    def _render_user_dashboard_page() -> str:
        return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>用户工作台</title>
  <style>
    @import url("https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap");
    :root { --bg:#f2f8ff; --card:#fff; --line:#d6e3f2; --fg:#10233e; --primary:#0a67dd; --teal:#00858d; --muted:#5a7190; --good:#006a5a; --bad:#96253a; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:"IBM Plex Sans","Noto Sans SC",sans-serif; color:var(--fg); background:
      radial-gradient(circle at 86% 18%, #d2ebff, transparent 32%),
      radial-gradient(circle at 18% 82%, #d8f2f1, transparent 34%),
      linear-gradient(130deg,#eef6ff,#ffffff 72%); }
    .top { display:flex; justify-content:space-between; align-items:center; padding:14px 18px; border-bottom:1px solid var(--line); background:rgba(255,255,255,.92); backdrop-filter: blur(6px); position:sticky; top:0; z-index:30; }
    .brand { font-family:"Space Grotesk","Noto Sans SC",sans-serif; font-weight:700; letter-spacing:.4px; }
    .wrap { max-width:1140px; margin:18px auto; padding:0 14px 16px; display:grid; gap:12px; }
    .grid { display:grid; grid-template-columns: 320px 1fr; gap:12px; align-items:start; }
    .card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px; box-shadow:0 12px 28px rgba(14,41,76,.07); }
    .title { margin:0; font-family:"Space Grotesk","Noto Sans SC",sans-serif; font-size:20px; }
    .sub { margin:4px 0 0; color:var(--muted); font-size:13px; }
    .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
    input,button { font:inherit; border:1px solid #bdd1e6; border-radius:10px; padding:8px 10px; min-height:44px; }
    input { flex:1; min-width:200px; }
    button { background:linear-gradient(120deg,var(--primary),var(--teal)); color:#fff; border-color:var(--primary); cursor:pointer; font-weight:600; }
    button.secondary { background:#fff; color:var(--fg); border-color:#bdd1e6; }
    button.ghost { background:#f5f9ff; color:#11427b; border-color:#c9dcf1; }
    .mailbox-list { display:grid; gap:8px; margin-top:10px; max-height:430px; overflow:auto; padding-right:4px; }
    .mailbox-item { display:flex; justify-content:space-between; align-items:center; gap:8px; border:1px solid #cfe0f2; background:#f9fcff; color:#0f345f; border-radius:10px; padding:8px 10px; min-height:44px; cursor:pointer; transition:all .18s ease; }
    .mailbox-item:hover { transform:translateY(-1px); border-color:#a9c7e7; }
    .mailbox-item.active { border-color:#4b95e9; background:#ecf5ff; box-shadow:0 6px 16px rgba(32,97,184,.18); }
    .mono { font-family: ui-monospace, "Cascadia Mono", "SF Mono", Menlo, Consolas, monospace; font-size:13px; }
    .status { font-size:13px; min-height:20px; margin-top:8px; color:#18457a; }
    .status.error { color:var(--bad); }
    .status.ok { color:var(--good); }
    .meta { display:grid; grid-template-columns:repeat(2,minmax(160px,1fr)); gap:8px; margin-top:10px; }
    .meta div { border:1px solid #d8e5f3; border-radius:10px; padding:8px 10px; background:#fafcff; }
    .meta b { display:block; font-size:12px; color:var(--muted); margin-bottom:2px; }
    pre { margin:0; padding:12px; border-radius:10px; background:#f7fbff; border:1px solid var(--line); white-space:pre-wrap; word-break:break-word; min-height:220px; max-height:460px; overflow:auto; }
    .pill { display:inline-block; padding:3px 9px; border-radius:999px; font-size:12px; border:1px solid #c8dbef; background:#f5f9ff; }
    @media (max-width: 960px) { .grid { grid-template-columns:1fr; } .meta { grid-template-columns:1fr; } }
    @media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition:none !important; animation:none !important; } }
  </style>
</head>
<body>
  <header class="top">
    <div class="brand">邮箱商城 · 用户中心</div>
    <div class="row">
      <span id="who" class="muted">未登录</span>
      <button id="btn-change-pass" class="ghost" style="display:none">修改密码</button>
      <button id="btn-logout" class="secondary" style="display:none">退出登录</button>
    </div>
  </header>

  <main class="wrap">
    <!-- 未登录：登录 / 注册 -->
    <section id="auth-gate" class="card" style="display:none; max-width:460px; margin:40px auto;">
      <h2 class="title" id="auth-title">登录账号</h2>
      <p class="sub">登录后即可使用 CDK 兑换邮箱、查看已购邮箱的邮件。</p>
      <div class="row" style="margin-top:10px; flex-direction:column; align-items:stretch; gap:8px;">
        <input id="auth-user" placeholder="用户名" autocomplete="username">
        <input id="auth-pass" type="password" placeholder="密码（至少6位）" autocomplete="current-password">
        <button id="btn-auth-submit">登录</button>
        <button id="btn-auth-toggle" class="ghost">没有账号？去注册</button>
      </div>
      <div id="auth-status" class="status"></div>
    </section>

    <!-- 已登录：兑换 + 我的邮箱 -->
    <div id="dash" style="display:none;">
      <section class="card">
        <h2 class="title">兑换 CDK 卡密</h2>
        <p class="sub">输入卡密兑换邮箱，兑换成功后邮箱会出现在下方「我的邮箱」中。</p>
        <div class="row" style="margin-top:10px">
          <input id="cdk-code" class="mono" placeholder="CDK-XXXXX-XXXXX-XXXXX-XXXXX">
          <button id="btn-redeem">兑换</button>
        </div>
        <div id="redeem-status" class="status"></div>
      </section>

      <section class="grid">
        <aside class="card">
          <div class="row" style="justify-content:space-between">
            <h3 style="margin:0">我的邮箱</h3>
            <button id="btn-refresh" class="ghost">刷新</button>
          </div>
          <p class="sub">点击「查看邮件」读取最新邮件；点击「复制」复制 邮箱----密钥。</p>
          <div id="mailbox-list" class="mailbox-list"></div>
          <div id="status" class="status"></div>
        </aside>
        <section class="card">
          <div class="row" style="justify-content:space-between">
            <h3 style="margin:0">最新邮件</h3>
            <span id="mail-type" class="pill">暂无类型</span>
          </div>
          <div class="meta">
            <div><b>邮箱</b><span id="m-to">-</span></div>
            <div><b>发件人</b><span id="m-from">-</span></div>
            <div><b>主题</b><span id="m-subject">-</span></div>
            <div><b>接收时间</b><span id="m-time">-</span></div>
          </div>
          <div style="margin-top:10px"><b style="font-size:12px;color:var(--muted)">正文</b></div>
          <pre id="result">请选择一个邮箱查看内容</pre>
        </section>
      </section>
    </div>
  </main>
<script>
async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "include", headers: { "Content-Type": "application/json" }, ...opts });
  const text = await r.text();
  let data = {};
  try { data = JSON.parse(text || "{}"); } catch {}
  return { status: r.status, data };
}
function el(id) { return document.getElementById(id); }
let selectedMailbox = "";
let cachedMailboxes = [];

function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function formatBeijingDateTime(value) {
  const raw = String(value || "").trim();
  if (!raw) return "-";
  const d = new Date(raw);
  if (isNaN(d.getTime())) return raw;
  try {
    return d.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
  } catch { return raw; }
}
async function copyText(text) {
  const value = String(text || "");
  if (!value) return false;
  if (navigator.clipboard && window.isSecureContext) {
    try { await navigator.clipboard.writeText(value); return true; } catch {}
  }
  const ta = document.createElement("textarea");
  ta.value = value; ta.setAttribute("readonly", "");
  ta.style.position = "fixed"; ta.style.top = "-9999px";
  document.body.appendChild(ta); ta.focus(); ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch {}
  document.body.removeChild(ta);
  return !!ok;
}

function setStatus(text, kind = "") {
  const node = el("status");
  if (!node) return;
  node.textContent = text;
  node.classList.remove("ok", "error");
  if (kind) node.classList.add(kind);
}
function setRedeemStatus(text, kind = "") {
  const node = el("redeem-status");
  node.textContent = text;
  node.classList.remove("ok", "error");
  if (kind) node.classList.add(kind);
}
function setAuthStatus(text, kind = "") {
  const node = el("auth-status");
  node.textContent = text;
  node.classList.remove("ok", "error");
  if (kind) node.classList.add(kind);
}

function setMailDetails(email) {
  if (!email) {
    el("mail-type").textContent = "暂无类型";
    el("m-to").textContent = "-";
    el("m-from").textContent = "-";
    el("m-subject").textContent = "-";
    el("m-time").textContent = "-";
    el("result").textContent = "该邮箱暂无邮件";
    return;
  }
  el("mail-type").textContent = email.mail_type || "未分类";
  el("m-to").textContent = email.to || "-";
  el("m-from").textContent = email.from || "-";
  el("m-subject").textContent = email.subject || "-";
  el("m-time").textContent = formatBeijingDateTime(email.received_at);
  el("result").textContent = email.text || email.body || "（无正文）";
}

function renderMailboxItems(mailboxes) {
  const list = el("mailbox-list");
  list.innerHTML = "";
  if (!mailboxes.length) {
    list.innerHTML = '<div class="sub">你还没有邮箱，请使用上方 CDK 兑换。</div>';
    return;
  }
  for (const m of mailboxes) {
    const address = m.address || "";
    const credential = m.credential || `${address}----${m.access_key || ""}`;
    const tags = Array.isArray(m.tags) ? m.tags.map((t) => t.name).filter(Boolean) : [];
    const item = document.createElement("div");
    item.className = "mailbox-item" + (address === selectedMailbox ? " active" : "");
    item.style.flexDirection = "column";
    item.style.alignItems = "stretch";
    item.style.cursor = "default";
    item.innerHTML = `
      <div class="mono" style="word-break:break-all">${escapeHtml(credential)}</div>
      <div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:6px;">
        ${tags.map((t) => `<span class="pill">${escapeHtml(t)}</span>`).join("")}
      </div>
      <div style="display:flex; gap:8px; margin-top:8px;">
        <button type="button" class="ghost" data-view-mail="${escapeHtml(address)}">查看邮件</button>
        <button type="button" class="secondary" data-copy="${escapeHtml(credential)}">复制</button>
        <button type="button" class="ghost" data-reset-key="${escapeHtml(address)}">重置密钥</button>
      </div>`;
    list.appendChild(item);
  }
}

async function loadMailboxes() {
  const res = await api("/web/me/mailboxes");
  if (res.status === 200 && res.data.ok) {
    cachedMailboxes = res.data.mailboxes || [];
    if (!selectedMailbox && cachedMailboxes.length > 0) {
      selectedMailbox = cachedMailboxes[0].address || "";
    }
    renderMailboxItems(cachedMailboxes);
    setStatus(`已加载 ${cachedMailboxes.length} 个邮箱`, "ok");
    return cachedMailboxes;
  }
  setStatus(`加载邮箱列表失败: ${res.data.error || "操作失败"}`, "error");
  renderMailboxItems([]);
  return [];
}

async function queryMailbox(address) {
  const addressRaw = (address || "").trim();
  if (!addressRaw) { setStatus("请先选择邮箱", "error"); return; }
  selectedMailbox = addressRaw;
  renderMailboxItems(cachedMailboxes);
  setStatus("查询中...", "");
  const res = await api(`/web/me/latest?address=${encodeURIComponent(addressRaw)}`);
  if (res.status === 200 && res.data.ok) {
    setMailDetails(res.data.email);
    setStatus("查询成功", "ok");
    return;
  }
  if (res.status === 403) setStatus("该邮箱不属于当前账号", "error");
  else setStatus(`查询失败: ${res.data.error || "操作失败"}`, "error");
  setMailDetails(null);
}

el("mailbox-list").addEventListener("click", async (ev) => {
  const t = ev.target;
  if (!t || typeof t.getAttribute !== "function") return;
  const viewAddr = t.getAttribute("data-view-mail");
  if (viewAddr) { await queryMailbox(viewAddr); return; }
  const copyVal = t.getAttribute("data-copy");
  if (copyVal) {
    const ok = await copyText(copyVal);
    setStatus(ok ? "已复制 邮箱----密钥" : "复制失败，请手动选择文本复制", ok ? "ok" : "error");
    return;
  }
  const resetAddr = t.getAttribute("data-reset-key");
  if (resetAddr) {
    if (!confirm(`确定重置 ${resetAddr} 的访问密钥？旧密钥将立即失效。`)) return;
    setStatus("重置中...", "");
    const res = await api("/web/me/mailboxes/reset-key", { method: "POST", body: JSON.stringify({ address: resetAddr }) });
    if (res.status === 200 && res.data.ok) {
      setStatus("密钥已重置，请使用新凭据", "ok");
      await loadMailboxes();
    } else {
      setStatus(`重置失败: ${res.data.error || "操作失败"}`, "error");
    }
  }
});
el("btn-refresh").onclick = async () => { await loadMailboxes(); };

el("btn-redeem").onclick = async () => {
  const code = el("cdk-code").value.trim();
  if (!code) { setRedeemStatus("请输入卡密", "error"); return; }
  const button = el("btn-redeem");
  button.disabled = true;
  setRedeemStatus("兑换中...", "");
  const res = await api("/web/user/redeem", { method: "POST", body: JSON.stringify({ code }) });
  if (res.status === 200 && res.data.ok) {
    const boxes = res.data.mailboxes || [];
    setRedeemStatus(`兑换成功，已发放 ${boxes.length} 个邮箱`, "ok");
    el("cdk-code").value = "";
    await loadMailboxes();
    if (boxes.length) await queryMailbox(boxes[0].address);
  } else {
    const map = {
      cdk_not_found: "卡密不存在", cdk_used: "卡密已被使用", cdk_expired: "卡密已过期",
      cdk_disabled: "卡密已被撤销", insufficient_stock: "库存不足，请联系卖家", missing_code: "请输入卡密",
      too_many_attempts: "操作过于频繁，请稍后再试", payload_too_large: "请求内容过大",
    };
    setRedeemStatus(`兑换失败: ${map[res.data.error] || res.data.error || "操作失败"}`, "error");
  }
  button.disabled = false;
};

el("btn-change-pass").onclick = async () => {
  const oldPassword = prompt("请输入当前密码");
  if (!oldPassword) return;
  const newPassword = prompt("请输入新密码（至少6位）");
  if (!newPassword) return;
  const res = await api("/web/me/change-password", {
    method: "POST",
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
  alert(res.status === 200 && res.data.ok ? "密码修改成功，请使用新密码登录。" : `修改失败: ${res.data.error || "操作失败"}`);
};

el("btn-logout").onclick = async () => {
  await api("/web/auth/logout", { method: "POST" });
  location.reload();
};

// ---- 登录 / 注册 ----
let authMode = "login";
el("btn-auth-toggle").onclick = () => {
  authMode = authMode === "login" ? "register" : "login";
  el("auth-title").textContent = authMode === "login" ? "登录账号" : "注册账号";
  el("btn-auth-submit").textContent = authMode === "login" ? "登录" : "注册";
  el("btn-auth-toggle").textContent = authMode === "login" ? "没有账号？去注册" : "已有账号？去登录";
  setAuthStatus("");
};
function authErrorText(code) {
  const map = {
    invalid_credentials: "用户名或密码错误",
    username_exists: "该用户名已被注册",
    missing_username: "请输入用户名",
    username_too_long: "用户名过长",
    password_too_short: "密码至少 6 位",
    password_too_long: "密码过长",
    user_inactive: "账号已被停用",
    too_many_attempts: "尝试过于频繁，请稍后再试",
  };
  return map[code] || code || "操作失败";
}
el("btn-auth-submit").onclick = async () => {
  const username = el("auth-user").value.trim();
  const password = el("auth-pass").value.trim();
  if (!username || !password) { setAuthStatus("请输入用户名和密码", "error"); return; }
  const button = el("btn-auth-submit");
  button.disabled = true;
  if (authMode === "register") {
    setAuthStatus("注册中...", "");
    const reg = await api("/web/auth/register", { method: "POST", body: JSON.stringify({ username, password }) });
    if (!(reg.status === 200 && reg.data.ok)) {
      setAuthStatus(`注册失败: ${authErrorText(reg.data.error)}`, "error");
      button.disabled = false;
      return;
    }
  }
  setAuthStatus("登录中...", "");
  const res = await api("/web/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
  if (res.status === 200 && res.data.ok) {
    const role = ((res.data.user || {}).role || "").toLowerCase();
    if (role === "admin") { location.href = "/web/admin"; return; }
    await boot();
  } else {
    setAuthStatus(`登录失败: ${authErrorText(res.data.error)}`, "error");
  }
  button.disabled = false;
};

function showAuth() {
  el("auth-gate").style.display = "";
  el("dash").style.display = "none";
  el("btn-change-pass").style.display = "none";
  el("btn-logout").style.display = "none";
  el("who").textContent = "未登录";
}
async function showDashboard(user) {
  el("auth-gate").style.display = "none";
  el("dash").style.display = "";
  el("btn-change-pass").style.display = "";
  el("btn-logout").style.display = "";
  el("who").textContent = `当前用户: ${user.username}`;
  const mailboxes = await loadMailboxes();
  if (mailboxes.length > 0) await queryMailbox(selectedMailbox);
  else setMailDetails(null);
}

async function boot() {
  const me = await api("/web/me");
  if (me.status === 200 && me.data.ok) {
    const role = ((me.data.user || {}).role || "").toLowerCase();
    if (role === "admin") { location.href = "/web/admin"; return; }
    await showDashboard(me.data.user);
  } else {
    showAuth();
  }
}
boot();
</script>
</body>
</html>"""

    @staticmethod
    def _render_admin_page() -> str:
        return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>邮箱密钥管理后台</title>
  <style>
    @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@500;600;700;800&family=IBM+Plex+Sans:wght@400;500;600&display=swap");
    :root { --bg:#f3f5f8; --panel:#ffffff; --panel-alt:#f8fafc; --line:#dde5ee; --line-strong:#c9d4e1; --fg:#142033; --muted:#62738a; --accent:#2363eb; --accent-soft:#e9f1ff; --ok:#0d7a56; --warn:#b43f2e; --shadow:0 14px 38px rgba(15,23,42,.07); }
    * { box-sizing:border-box; }
    body { margin:0; font-family:"IBM Plex Sans","Noto Sans SC",sans-serif; color:var(--fg); background:linear-gradient(180deg,#f7f9fc 0%, var(--bg) 100%); }
    .top { position:sticky; top:0; z-index:30; display:flex; justify-content:space-between; align-items:center; gap:14px; padding:14px 20px; border-bottom:1px solid var(--line); background:rgba(243,245,248,.92); backdrop-filter:blur(14px); }
    .brand { font-family:"Manrope","Noto Sans SC",sans-serif; font-size:20px; font-weight:800; letter-spacing:.01em; }
    .top .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
    .shell { max-width:1680px; margin:0; padding:22px 18px 28px; }
    .workspace { display:grid; grid-template-columns:320px minmax(0,1fr); gap:18px; align-items:start; }
    .sidebar { display:grid; gap:16px; position:sticky; top:84px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:22px; box-shadow:var(--shadow); overflow:hidden; }
    .panel-head { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:18px 20px 0; }
    .panel-title { margin:0; font-family:"Manrope","Noto Sans SC",sans-serif; font-size:18px; font-weight:800; letter-spacing:.01em; }
    .panel-body { padding:18px 20px 20px; }
    .content { display:grid; gap:16px; }
    .toolbar { display:grid; gap:12px; padding:18px 20px; }
    .toolbar-row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
    .toolbar-row input { flex:1; min-width:220px; }
    .status-bar { min-height:20px; color:var(--muted); font-size:13px; }
    .status-bar.ok { color:var(--ok); }
    .status-bar.error { color:var(--warn); }
    .form-grid { display:grid; gap:12px; }
    .field { display:grid; gap:8px; }
    .field-label { font-size:12px; color:var(--muted); letter-spacing:.06em; text-transform:uppercase; font-weight:600; }
    .input-row { display:flex; gap:10px; flex-wrap:wrap; }
    .input-row input { flex:1; min-width:0; }
    input,button,textarea { width:100%; font:inherit; border:1px solid var(--line-strong); border-radius:14px; padding:11px 13px; min-height:44px; background:#fff; color:var(--fg); }
    textarea { min-height:180px; resize:vertical; }
    button { width:auto; background:var(--accent); color:#fff; border-color:var(--accent); cursor:pointer; font-weight:700; padding:0 16px; transition:background .18s ease, border-color .18s ease, transform .18s ease; }
    button:hover { transform:translateY(-1px); background:#1d56cf; border-color:#1d56cf; }
    button.secondary { background:#fff; color:var(--fg); border-color:var(--line-strong); }
    button.ghost { background:var(--accent-soft); color:var(--accent); border-color:#d4e2ff; }
    button[disabled] { opacity:.6; cursor:not-allowed; transform:none; }
    .button-block { width:100%; justify-content:center; }
    .mono { font-family:ui-monospace, "Cascadia Mono", "SF Mono", Menlo, Consolas, monospace; }
    .muted { color:var(--muted); font-size:13px; }
    .tag { display:inline-flex; align-items:center; gap:8px; min-height:28px; padding:0 10px; border-radius:999px; background:var(--panel-alt); border:1px solid var(--line); color:var(--muted); font-size:12px; font-weight:600; }
    .tag-list { display:flex; flex-wrap:wrap; gap:8px; }
    .tag-chip { display:inline-flex; align-items:center; gap:6px; min-height:30px; padding:0 10px; border-radius:999px; background:#f6f8ff; border:1px solid #d8e3ff; color:#36507a; font-size:12px; font-weight:600; }
    .tag-chip button { min-height:auto; padding:0; border:none; background:transparent; color:#8f2341; cursor:pointer; font-size:14px; line-height:1; }
    .tag-filter-row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
    select { font:inherit; border:1px solid #bdd1e6; border-radius:10px; padding:8px 10px; min-height:44px; background:#fff; }
    .table-panel { overflow:hidden; }
    .table-wrap { overflow:auto; border-top:1px solid var(--line); }
    table { width:100%; border-collapse:collapse; min-width:1240px; background:#fff; }
    th,td { border-bottom:1px solid #edf1f5; padding:14px 12px; text-align:left; vertical-align:top; font-size:14px; }
    th { position:sticky; top:0; background:#f8fafc; color:#5d6d83; font-size:12px; text-transform:uppercase; letter-spacing:.08em; z-index:1; }
    td code { display:inline-block; padding:4px 8px; border-radius:10px; background:#f4f7fb; }
    .email-preview, .note-preview, .key-preview-cell { display:inline-block; max-width:220px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; vertical-align:top; }
    .email-preview { max-width:240px; }
    .key-preview-cell { max-width:170px; }
    .status-chip { display:inline-flex; align-items:center; gap:8px; min-height:28px; padding:0 10px; border-radius:999px; font-size:12px; font-weight:700; border:1px solid transparent; }
    .status-chip::before { content:""; width:8px; height:8px; border-radius:999px; background:currentColor; opacity:.9; }
    .status-chip.active { color:#0d7a56; background:#ecfbf4; border-color:#cdeedd; }
    .status-chip.inactive { color:#a14a16; background:#fff5eb; border-color:#f2dcc6; }
    .status-chip.warn { color:#b42318; background:#fef3f2; border-color:#fbd9d3; }
    .table-actions { display:flex; flex-wrap:nowrap; gap:8px; align-items:center; }
    .icon-btn { width:36px; min-width:36px; height:36px; min-height:36px; padding:0; display:inline-flex; align-items:center; justify-content:center; border-radius:10px; font-size:15px; line-height:1; }
    .icon-btn.secondary { background:#fff; }
    .icon-btn.ghost { background:var(--accent-soft); }
    .icon-btn:hover { transform:translateY(-1px); }
    .result-panel pre { margin:0; padding:16px 18px 18px; background:#0f172a; color:#dbe5f4; white-space:pre-wrap; word-break:break-word; min-height:240px; max-height:420px; overflow:auto; }
    .result-panel.collapsed pre { display:none; }
    .empty-state { padding:18px; color:var(--muted); }
    .compact-info { display:grid; gap:8px; }
    .compact-info .tag { width:max-content; }
    .right-foot { display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; align-items:center; padding:14px 20px 18px; border-top:1px solid var(--line); }
    .page-buttons { display:flex; gap:10px; flex-wrap:wrap; }
    .page-buttons button { min-height:40px; }
    .who { white-space:nowrap; }
    .tag-modal { position:fixed; inset:0; background:rgba(15,23,42,.46); display:none; align-items:center; justify-content:center; padding:18px; z-index:60; backdrop-filter:blur(8px); }
    .tag-modal.open { display:flex; }
    .tag-modal-dialog { width:min(560px,100%); background:#fff; border:1px solid var(--line); border-radius:22px; box-shadow:0 28px 84px rgba(15,23,42,.22); overflow:hidden; }
    .tag-modal-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; padding:18px 20px; border-bottom:1px solid var(--line); }
    .tag-modal-title { margin:0; font-family:"Manrope","Noto Sans SC",sans-serif; font-size:18px; font-weight:800; }
    .tag-modal-sub { margin:6px 0 0; color:var(--muted); font-size:13px; }
    .tag-modal-body { padding:18px 20px; display:grid; gap:12px; max-height:420px; overflow:auto; }
    .tag-checkbox { display:flex; align-items:center; gap:10px; padding:12px 14px; border:1px solid var(--line); border-radius:14px; background:#fff; cursor:pointer; }
    .tag-checkbox input { width:18px; height:18px; min-height:auto; margin:0; }
    .tag-checkbox-meta { display:grid; gap:4px; }
    .tag-checkbox-meta strong { font-size:14px; }
    .tag-checkbox-meta span { color:var(--muted); font-size:12px; }
    .tag-modal-foot { display:flex; justify-content:flex-end; gap:10px; padding:16px 20px 20px; border-top:1px solid var(--line); }
    .tag-check-list { display:grid; gap:5px; max-height:130px; overflow-y:auto; padding:2px 0; }
    .tag-check-list .tag-checkbox { padding:8px 10px; border-radius:10px; }
    .tag-check-list .tag-checkbox-meta strong { font-size:13px; }
    @media (max-width: 1180px) {
      .workspace { grid-template-columns:1fr; }
      .sidebar { position:static; }
      .table-actions { flex-wrap:wrap; }
    }
    @media (max-width: 720px) {
      .shell { padding:16px 12px 20px; }
      .top { padding:12px; align-items:flex-start; }
      .toolbar-row, .input-row { flex-direction:column; align-items:stretch; }
      button, .button-block { width:100%; }
      .page-buttons { width:100%; }
      .page-buttons button { flex:1; }
    }
    @media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition:none !important; animation:none !important; } }
    .layout { display:grid; grid-template-columns:212px minmax(0,1fr); gap:18px; max-width:1680px; margin:0 auto; padding:22px 18px 28px; align-items:start; }
    .nav { display:grid; gap:6px; position:sticky; top:84px; background:var(--panel); border:1px solid var(--line); border-radius:22px; box-shadow:var(--shadow); padding:12px; }
    .nav-item { display:flex; align-items:center; gap:10px; width:100%; justify-content:flex-start; min-height:46px; padding:0 14px; border-radius:14px; border:1px solid transparent; background:transparent; color:var(--fg); font-weight:600; cursor:pointer; transition:background .16s ease, color .16s ease, border-color .16s ease; }
    .nav-item:hover { transform:none; background:var(--panel-alt); border-color:var(--line); }
    .nav-item.active { background:var(--accent-soft); border-color:#d4e2ff; color:var(--accent); }
    .nav-ico { width:22px; text-align:center; font-size:16px; }
    .main { display:grid; gap:16px; min-width:0; }
    .view { display:grid; gap:16px; }
    .view.hidden { display:none; }
    .create-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:16px; align-items:start; }
    .stat-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:14px; }
    .stat-card { display:grid; gap:8px; padding:18px; border:1px solid var(--line); border-radius:18px; background:linear-gradient(180deg,#fff,#f8fafc); }
    .stat-label { font-size:12px; color:var(--muted); letter-spacing:.06em; text-transform:uppercase; font-weight:600; }
    .stat-value { font-family:"Manrope","Noto Sans SC",sans-serif; font-size:30px; font-weight:800; line-height:1.1; }
    .mail-list { display:grid; gap:12px; padding:18px 20px 20px; }
    .mail-item { position:relative; overflow:hidden; background:linear-gradient(180deg,#fff 0%,#fcfcff 100%); border:1px solid var(--line); border-radius:18px; padding:16px; box-shadow:var(--shadow); cursor:pointer; transition:transform .18s ease,border-color .18s ease, box-shadow .18s ease; }
    .mail-item::before { content:""; position:absolute; inset:0 0 auto 0; height:3px; background:linear-gradient(90deg,var(--accent),#7c3aed,#22c55e); opacity:.95; }
    .mail-item:hover { transform:translateY(-2px); border-color:#cbd5ff; box-shadow:0 22px 44px rgba(80,76,160,.15); }
    .mail-top { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }
    .mail-subject { font-size:17px; font-weight:700; margin:0; line-height:1.45; }
    .mail-meta { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; color:var(--muted); font-size:13px; }
    .mail-preview { margin:12px 0 0; color:#344054; line-height:1.7; white-space:pre-wrap; word-break:break-word; }
    .mail-address { display:inline-flex; align-items:center; gap:6px; min-height:28px; padding:0 10px; border-radius:999px; background:#f8fafc; border:1px solid var(--line); font-size:12px; color:#475467; }
    .mail-pagination { display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; align-items:center; padding:14px 20px 18px; border-top:1px solid var(--line); }
    .modal { position:fixed; inset:0; background:rgba(15,23,42,.46); display:none; align-items:center; justify-content:center; padding:18px; z-index:60; backdrop-filter:blur(8px); }
    .modal.open { display:flex; }
    .modal-dialog { width:min(1100px,100%); max-height:92vh; overflow:hidden; background:#fff; border:1px solid var(--line); border-radius:24px; box-shadow:0 28px 84px rgba(15,23,42,.22); }
    .modal-content { display:grid; grid-template-rows:auto 1fr; max-height:92vh; }
    .modal-header { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; padding:18px 20px; border-bottom:1px solid var(--line); }
    .modal-title { margin:0; font-family:"Manrope","Noto Sans SC",sans-serif; font-size:22px; font-weight:800; }
    .modal-header-copy { margin-top:4px; color:var(--muted); font-size:13px; }
    .modal-body { padding:18px 20px 22px; overflow:auto; }
    .email-meta-modal { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:10px 18px; color:#344054; }
    .email-meta-modal p { margin:0; line-height:1.7; }
    .modal-content-switch { display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }
    .modal-switch.active { background:var(--accent); border-color:var(--accent); color:#fff; }
    iframe { width:100%; min-height:58vh; border:1px solid var(--line); border-radius:16px; background:#fff; }
    .modal-fallback { margin:0; padding:16px; border-radius:16px; background:#0f172a; color:#dbe5f4; white-space:pre-wrap; word-break:break-word; min-height:58vh; overflow:auto; display:none; }
    @media (max-width: 1180px) {
      .layout { grid-template-columns:1fr; }
      .nav { position:static; grid-auto-flow:column; grid-auto-columns:max-content; overflow-x:auto; }
      .create-grid { grid-template-columns:1fr; }
    }
  </style>
</head>
<body>
  <header class="top">
    <div class="brand">邮箱服务 · 管理后台</div>
    <div class="row">
      <span id="who" class="muted who">加载中...</span>
      <button id="btn-change-pass" class="ghost">修改密码</button>
      <button id="btn-logout" class="secondary">退出登录</button>
    </div>
  </header>
  <div class="layout">
    <nav class="nav" id="nav">
      <button class="nav-item active" data-view="dashboard"><span class="nav-ico">▣</span><span>概览</span></button>
      <button class="nav-item" data-view="mailboxes"><span class="nav-ico">▤</span><span>邮箱列表</span></button>
      <button class="nav-item" data-view="create"><span class="nav-ico">＋</span><span>新建 / 导入</span></button>
      <button class="nav-item" data-view="tags"><span class="nav-ico">#</span><span>标签</span></button>
      <button class="nav-item" data-view="inbox"><span class="nav-ico">✉</span><span>收件箱</span></button>
      <button class="nav-item" data-view="cdk"><span class="nav-ico">🎟</span><span>卡密售卖</span></button>
    </nav>
    <main class="main">
      <section class="view" data-view="dashboard">
        <section class="panel">
          <div class="panel-head">
            <h2 class="panel-title">概览</h2>
            <button id="btn-refresh-dashboard" class="ghost">刷新</button>
          </div>
          <div class="panel-body">
            <div class="stat-grid">
              <div class="stat-card"><span class="stat-label">邮箱总数</span><strong id="stat-mailboxes" class="stat-value">-</strong></div>
              <div class="stat-card"><span class="stat-label">启用邮箱</span><strong id="stat-active" class="stat-value">-</strong></div>
              <div class="stat-card"><span class="stat-label">标签数</span><strong id="stat-tags" class="stat-value">-</strong></div>
              <div class="stat-card"><span class="stat-label">收件总数</span><strong id="stat-inbox" class="stat-value">-</strong></div>
            </div>
            <p class="muted" style="margin-top:14px">数据来自当前邮箱与收件箱统计；点击左侧导航查看明细。</p>
          </div>
        </section>
      </section>

      <section class="view hidden" data-view="mailboxes">
        <section class="panel toolbar">
          <div class="toolbar-row">
            <input id="search-mailbox" placeholder="搜索邮箱或备注">
            <select id="filter-tag">
              <option value="">全部标签</option>
            </select>
            <select id="filter-status">
              <option value="">全部状态</option>
              <option value="presale">预售池</option>
              <option value="available">可兑换</option>
              <option value="sold">已售出</option>
              <option value="deleted">已删除</option>
            </select>
            <button id="btn-refresh" class="ghost">刷新</button>
            <button id="btn-export-mailboxes" class="secondary">导出 CSV</button>
          </div>
          <div class="toolbar-row">
            <span id="mailboxes-summary" class="muted">加载中...</span>
            <span id="mailboxes-page-info" class="tag">第 1 页</span>
          </div>
          <div id="status" class="status-bar"></div>
        </section>

        <section class="panel table-panel">
          <div class="panel-head">
            <h2 class="panel-title">邮箱列表</h2>
            <div class="compact-info">
              <span class="tag">支持备注 · 启停 · 重置密钥</span>
            </div>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr><th>ID</th><th>邮箱</th><th>密钥</th><th>备注</th><th>状态</th><th>创建时间</th><th>邮件数</th><th>操作</th></tr>
              </thead>
              <tbody id="mailboxes-body"></tbody>
            </table>
          </div>
          <div class="right-foot">
            <div class="muted">在左侧「收件箱」可查看全局收件箱；列表内仍可查看单个邮箱最近邮件。</div>
            <div class="page-buttons">
              <button id="btn-prev-page" class="secondary">上一页</button>
              <button id="btn-next-page" class="secondary">下一页</button>
            </div>
          </div>
        </section>

        <section class="panel result-panel collapsed" id="result-panel">
          <div class="panel-head">
            <h2 class="panel-title">结果</h2>
            <button id="btn-toggle-result" class="ghost">展开结果</button>
          </div>
          <pre id="admin-result">暂无结果</pre>
        </section>
      </section>

      <section class="view hidden" data-view="create">
        <div class="create-grid">
          <section class="panel">
            <div class="panel-head">
              <h2 class="panel-title">单个创建</h2>
              <span class="tag">邮箱----密钥</span>
            </div>
            <div class="panel-body">
              <div class="form-grid">
                <label class="field">
                  <span class="field-label">邮箱</span>
                  <input id="new-mailbox" class="mono" placeholder="name@example.com">
                </label>
                <label class="field">
                  <span class="field-label">备注</span>
                  <input id="new-mailbox-note" placeholder="可选">
                </label>
                <label class="field">
                  <span class="field-label">标签</span>
                  <div id="new-mailbox-tags" class="tag-check-list"><span class="muted" style="font-size:12px">暂无标签</span></div>
                  <span class="muted" style="font-size:12px">可选；需先在「标签」页创建标签</span>
                </label>
                <button id="btn-create-mailbox" class="button-block">创建邮箱</button>
              </div>
            </div>
          </section>

          <section class="panel">
            <div class="panel-head">
              <h2 class="panel-title">批量导入</h2>
              <span class="tag">一行一个</span>
            </div>
            <div class="panel-body">
              <div class="form-grid">
                <label class="field">
                  <span class="field-label">统一备注</span>
                  <input id="bulk-mailbox-note" placeholder="可选">
                </label>
                <label class="field">
                  <span class="field-label">统一标签</span>
                  <div id="bulk-mailbox-tags" class="tag-check-list"><span class="muted" style="font-size:12px">暂无标签</span></div>
                  <span class="muted" style="font-size:12px">可选，仅作用于新建的邮箱</span>
                </label>
                <label class="field">
                  <span class="field-label">邮箱列表</span>
                  <textarea id="bulk-mailboxes" class="mono" placeholder="alpha@example.com&#10;bravo@example.com&#10;charlie@icloud.com"></textarea>
                </label>
                <button id="btn-bulk-import" class="button-block">批量创建</button>
              </div>
            </div>
          </section>

          <section class="panel">
            <div class="panel-head">
              <h2 class="panel-title">CSV 导入</h2>
              <span class="tag">有则覆盖 · 无则新建</span>
            </div>
            <div class="panel-body">
              <div class="form-grid">
                <label class="field">
                  <span class="field-label">CSV 文件</span>
                  <input id="csv-import-file" type="file" accept=".csv,text/csv">
                </label>
                <label class="field">
                  <span class="field-label">统一备注</span>
                  <input id="csv-import-note" placeholder="可选，不填则保留原备注">
                </label>
                <button id="btn-import-csv" class="button-block">导入 CSV</button>
              </div>
            </div>
          </section>
        </div>
      </section>

      <section class="view hidden" data-view="tags">
        <section class="panel">
          <div class="panel-head">
            <h2 class="panel-title">标签管理</h2>
            <span class="tag">仅管理员可见</span>
          </div>
          <div class="panel-body">
            <div class="form-grid">
              <label class="field">
                <span class="field-label">新标签</span>
                <input id="new-tag-name" placeholder="例如：重点 / 已分组 / 待复查">
              </label>
              <button id="btn-create-tag" class="button-block">创建标签</button>
            </div>
            <div id="admin-tags" class="tag-list" style="margin-top:12px">
              <span class="muted">暂无标签</span>
            </div>
          </div>
        </section>
      </section>

      <section class="view hidden" data-view="cdk">
        <section class="panel">
          <div class="panel-head">
            <h2 class="panel-title">生成 CDK 卡密</h2>
            <span class="tag">单次兑换 · 按品类发货</span>
          </div>
          <div class="panel-body">
            <div class="form-grid">
              <label class="field">
                <span class="field-label">品类（标签）</span>
                <select id="cdk-tag"></select>
                <span class="muted" style="font-size:12px">「任意品类」从全部未售邮箱发货；选标签则仅发该标签下的未售邮箱</span>
              </label>
              <label class="field">
                <span class="field-label">每码发放邮箱数</span>
                <input id="cdk-quantity" type="number" min="1" value="1">
              </label>
              <label class="field">
                <span class="field-label">生成数量</span>
                <input id="cdk-count" type="number" min="1" value="10">
              </label>
              <label class="field">
                <span class="field-label">有效期（可选）</span>
                <input id="cdk-expires" type="datetime-local">
              </label>
              <label class="field">
                <span class="field-label">批次标签（可选）</span>
                <input id="cdk-batch" placeholder="例如：闲鱼-0627">
              </label>
              <label class="field">
                <span class="field-label">备注（可选）</span>
                <input id="cdk-note" placeholder="可选">
              </label>
              <button id="btn-gen-cdk" class="button-block">生成卡密并复制</button>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <h2 class="panel-title">库存概览</h2>
            <button id="btn-refresh-stock" class="ghost">刷新库存</button>
          </div>
          <div class="panel-body">
            <div id="sales-stats" class="tag-list" style="margin-bottom:12px">
              <span class="muted">加载中...</span>
            </div>
            <div id="stock-summary" class="tag-list">
              <span class="muted">加载中...</span>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <h2 class="panel-title">换货 / 标记失效</h2>
          </div>
          <div class="panel-body">
            <p class="muted" style="margin-top:0">输入一个<strong>已售</strong>邮箱地址，标记其失效并从同标签库存补发一个给原买家。</p>
            <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
              <input id="replace-address" placeholder="待换货的已售邮箱地址" style="flex:1; min-width:220px; min-height:38px; border:1px solid var(--line); border-radius:8px; padding:0 10px;">
              <button id="btn-replace" class="primary">换货</button>
            </div>
            <div id="replace-status" class="muted" style="margin-top:8px; word-break:break-all;"></div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <h2 class="panel-title">卡密列表</h2>
            <span class="tag">/web/admin/cdks</span>
          </div>
          <div class="panel-body">
            <div class="toolbar-row">
              <input id="cdk-search" placeholder="搜索卡密 / 批次 / 备注">
              <select id="cdk-status-filter">
                <option value="">全部状态</option>
                <option value="active">可用</option>
                <option value="used">已用</option>
                <option value="expired">已过期</option>
                <option value="disabled">已撤销</option>
              </select>
              <select id="cdk-tag-filter">
                <option value="">全部分类</option>
              </select>
              <button id="cdk-export" class="ghost">导出 TXT</button>
              <span id="cdk-summary" class="muted">加载中...</span>
            </div>
            <div id="cdk-status-bar" class="status-bar"></div>
            <div id="cdk-list" class="mail-list" style="margin-top:12px">
              <div class="empty-state">正在加载卡密...</div>
            </div>
            <div class="mail-pagination">
              <span id="cdk-page-info" class="tag">第 1 页</span>
              <div class="page-buttons">
                <button id="cdk-prev-page" class="secondary">上一页</button>
                <button id="cdk-next-page" class="secondary">下一页</button>
              </div>
            </div>
          </div>
        </section>
      </section>

      <section class="view hidden" data-view="inbox">
        <section class="panel toolbar">
          <div class="toolbar-row">
            <input id="search-inbox" placeholder="搜索收件邮箱 / 发件人 / 主题">
            <span id="inbox-summary" class="muted">加载中...</span>
            <span id="inbox-page-info" class="tag">第 1 页</span>
            <button id="inbox-refresh" class="ghost">刷新</button>
          </div>
          <div id="inbox-status" class="status-bar"></div>
        </section>

        <section class="panel" id="inbox-panel">
          <div class="panel-head">
            <h2 class="panel-title">最近收到的邮件</h2>
            <span class="tag">/web/admin/inbox/list</span>
          </div>
          <div id="inboxList" class="mail-list">
            <div class="empty-state">正在加载收件箱...</div>
          </div>
          <div class="mail-pagination">
            <div class="muted">点击任意邮件卡片查看详情</div>
            <div class="page-buttons">
              <button id="inbox-prev-page" class="secondary">上一页</button>
              <button id="inbox-next-page" class="secondary">下一页</button>
            </div>
          </div>
        </section>
      </section>
    </main>
  </div>

  <div id="emailModal" class="modal">
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <div>
            <h2 id="modalSubject" class="modal-title">邮件内容</h2>
            <div class="modal-header-copy">支持 HTML 正文预览与纯文本回退</div>
          </div>
          <button type="button" class="secondary" id="closeModalButton" aria-label="关闭">关闭</button>
        </div>
        <div class="modal-body">
          <div class="email-meta-modal">
            <p><strong>发件人：</strong><span id="modalFrom"></span></p>
            <p><strong>收件人：</strong><span id="modalTo"></span></p>
            <p><strong>日期：</strong><span id="modalDate"></span></p>
            <p><strong>类型：</strong><span id="modalType"></span></p>
          </div>
          <div class="modal-content-switch">
            <button type="button" id="viewRenderedButton" class="modal-switch active">正文预览</button>
            <button type="button" id="viewRawButton" class="modal-switch secondary">邮件源码</button>
          </div>
          <div style="margin-top:14px">
            <iframe id="emailBodyFrame" src="about:blank" title="邮件正文"></iframe>
            <pre id="emailBodyFallback" class="modal-fallback"></pre>
          </div>
        </div>
      </div>
    </div>
  </div>
  <div id="mailboxMailsModal" class="modal">
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <div>
            <h2 id="mbMailsTitle" class="modal-title">邮箱邮件</h2>
            <div id="mbMailsSub" class="modal-header-copy"></div>
          </div>
          <button type="button" class="secondary" id="mbMailsClose" aria-label="关闭">关闭</button>
        </div>
        <div class="modal-body">
          <div id="mbMailsList" class="mail-list"></div>
        </div>
      </div>
    </div>
  </div>
  <div id="tagEditModal" class="tag-modal">
    <div class="tag-modal-dialog">
      <div class="tag-modal-head">
        <div>
          <h3 class="tag-modal-title">编辑邮箱标签</h3>
          <div id="tagEditModalSub" class="tag-modal-sub">为当前邮箱选择一个或多个标签</div>
        </div>
        <button type="button" id="btn-close-tag-modal" class="secondary">关闭</button>
      </div>
      <div id="tagEditModalBody" class="tag-modal-body">
        <div class="muted">暂无标签，请先在左侧创建标签。</div>
      </div>
      <div class="tag-modal-foot">
        <button type="button" id="btn-cancel-tag-modal" class="secondary">取消</button>
        <button type="button" id="btn-save-tag-modal">保存标签</button>
      </div>
    </div>
  </div>
<script>
async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "include", headers: { "Content-Type": "application/json" }, ...opts });
  const text = await r.text();
  let data = {};
  try { data = JSON.parse(text || "{}"); } catch {}
  return { status: r.status, data };
}
function el(id) { return document.getElementById(id); }
function setResultOpen(open) {
  const panel = el("result-panel");
  const button = el("btn-toggle-result");
  if (!panel || !button) return;
  panel.classList.toggle("collapsed", !open);
  button.textContent = open ? "收起结果" : "展开结果";
}
function setResult(v) {
  el("admin-result").textContent = typeof v === "string" ? v : JSON.stringify(v, null, 2);
  setResultOpen(true);
}
let mailboxesCache = [];
let mailboxPagination = { keyword: "", limit: 20, offset: 0, total: 0 };
let tagFilterValue = "";
let statusFilterValue = "";
let tagCache = [];
let currentTagEditMailboxId = 0;

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatDateOnly(value) {
  const text = String(value || "").trim();
  if (!text) return "-";
  const match = text.match(/^\\d{4}-\\d{2}-\\d{2}/);
  if (match) return match[0];
  const parsed = new Date(text);
  if (!Number.isNaN(parsed.getTime())) {
    const year = parsed.getFullYear();
    const month = String(parsed.getMonth() + 1).padStart(2, "0");
    const day = String(parsed.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }
  return text;
}

function setStatus(text, kind = "") {
  const node = el("status");
  node.textContent = text;
  node.classList.remove("ok", "error");
  if (kind) node.classList.add(kind);
}

function closeTagEditModal() {
  currentTagEditMailboxId = 0;
  el("tagEditModal").classList.remove("open");
}

function openTagEditModal(mailbox) {
  currentTagEditMailboxId = Number(mailbox.id || 0);
  el("tagEditModalSub").textContent = `当前邮箱：${mailbox.address || "-"}`;
  const body = el("tagEditModalBody");
  if (!tagCache.length) {
    body.innerHTML = '<div class="muted">暂无标签，请先在左侧创建标签。</div>';
    el("tagEditModal").classList.add("open");
    return;
  }
  const selected = new Set((Array.isArray(mailbox.tags) ? mailbox.tags : []).map((tag) => String(tag.id)));
  body.innerHTML = tagCache.map((tag) => `
    <label class="tag-checkbox">
      <input type="checkbox" data-tag-option="${tag.id}" ${selected.has(String(tag.id)) ? "checked" : ""}>
      <span class="tag-checkbox-meta">
        <strong>${escapeHtml(tag.name || "")}</strong>
        <span>已绑定邮箱 ${Number(tag.mailbox_count || 0)} 个</span>
      </span>
    </label>
  `).join("");
  el("tagEditModal").classList.add("open");
}

async function copyText(text) {
  const value = String(text || "");
  if (!value) return false;
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {}
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "-9999px";
  textarea.style.left = "-9999px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  document.body.removeChild(textarea);
  return !!copied;
}

async function copyCreatedCredentials(credentials, { successMessage, fallbackMessage, resultPayload } = {}) {
  const lines = Array.isArray(credentials)
    ? credentials.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  if (resultPayload !== undefined) {
    if (lines.length) {
      setResult({
        ...(resultPayload && typeof resultPayload === "object" && !Array.isArray(resultPayload) ? resultPayload : { result: resultPayload }),
        copied_credentials: lines.join("\\n"),
      });
    } else {
      setResult(resultPayload);
    }
  }
  if (!lines.length) return false;
  const copied = await copyText(lines.join("\\n"));
  if (copied) {
    setStatus(successMessage || "凭据已复制", "ok");
    return true;
  }
  setStatus(fallbackMessage || "浏览器不支持自动复制，请从结果面板手动复制", "error");
  return false;
}

function renderTagOptions() {
  const select = el("filter-tag");
  const current = String(tagFilterValue || "");
  select.innerHTML = '<option value="">全部标签</option>';
  for (const tag of tagCache) {
    const option = document.createElement("option");
    option.value = String(tag.id);
    option.textContent = `${tag.name} (${tag.mailbox_count || 0})`;
    if (String(tag.id) === current) option.selected = true;
    select.appendChild(option);
  }
  renderCreateTagSelectors();
}

function renderCreateTagSelectors() {
  for (const divId of ["new-mailbox-tags", "bulk-mailbox-tags"]) {
    const div = el(divId);
    if (!div) continue;
    const previously = new Set(Array.from(div.querySelectorAll('[data-tag-create]:checked')).map((cb) => cb.getAttribute('data-tag-create')));
    if (!tagCache.length) {
      div.innerHTML = '<span class="muted" style="font-size:12px">暂无标签</span>';
      continue;
    }
    div.innerHTML = tagCache.map((tag) =>
      `<label class="tag-checkbox"><input type="checkbox" data-tag-create="${tag.id}"${previously.has(String(tag.id)) ? " checked" : ""}><span class="tag-checkbox-meta"><strong>${escapeHtml(tag.name)}</strong><span>${tag.mailbox_count || 0} 个邮箱</span></span></label>`
    ).join('');
  }
}

function selectedTagIds(divId) {
  const div = el(divId);
  if (!div) return [];
  return Array.from(div.querySelectorAll('[data-tag-create]:checked'))
    .map((cb) => Number(cb.getAttribute('data-tag-create')))
    .filter((v) => Number.isInteger(v) && v > 0);
}

function renderAdminTags() {
  const container = el("admin-tags");
  if (!tagCache.length) {
    container.innerHTML = '<span class="muted">暂无标签</span>';
    renderTagOptions();
    return;
  }
  container.innerHTML = tagCache.map((tag) => `
    <span class="tag-chip">
      <span>${escapeHtml(tag.name)} (${Number(tag.mailbox_count || 0)})</span>
      <button type="button" data-tag-delete="${tag.id}" aria-label="删除标签">×</button>
    </span>
  `).join("");
  renderTagOptions();
}

async function refreshTags() {
  const res = await api("/web/admin/tags", { method: "GET" });
  if (!(res.status === 200 && res.data.ok)) {
    setResult(res.data);
    setStatus("读取标签失败", "error");
    return;
  }
  tagCache = res.data.tags || [];
  if (tagFilterValue && !tagCache.some((tag) => String(tag.id) === String(tagFilterValue))) {
    tagFilterValue = "";
  }
  renderAdminTags();
}

async function ensureAdmin() {
  const me = await api("/web/me");
  if (me.status !== 200 || !me.data.ok) { location.href = "/web/query"; return false; }
  const role = ((me.data.user || {}).role || "").toLowerCase();
  if (role !== "admin") { location.href = "/web/query"; return false; }
  el("who").textContent = `管理员: ${me.data.user.username}`;
  return true;
}

function renderMailboxesTable() {
  const tbody = el("mailboxes-body");
  tbody.innerHTML = "";
  if (!mailboxesCache.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="8" class="empty-state">当前页没有邮箱记录</td>';
    tbody.appendChild(tr);
  }
  for (const m of mailboxesCache) {
    const tr = document.createElement("tr");
    const addressText = String(m.address || "").trim();
    const noteText = String(m.note || "").trim();
    const keyText = String(m.access_key || "").trim();
    const poolMap = { presale: ["预售池", "warn"], available: ["可兑换", "active"], sold: ["已售出", "inactive"], deleted: ["已删除", "inactive"] };
    const pool = poolMap[m.status] || ["可兑换", "active"];
    const statusLabel = m.active ? pool[0] : "停用";
    const statusClass = m.active ? pool[1] : "inactive";
    const addressDisplay = addressText
      ? `<code class="email-preview" title="${escapeHtml(addressText)}">${escapeHtml(addressText)}</code>`
      : `<code>-</code>`;
    const noteDisplay = noteText
      ? `<span class="note-preview" title="${escapeHtml(noteText)}">${escapeHtml(noteText)}</span>`
      : "-";
    const keyDisplay = keyText
      ? `<code class="key-preview-cell" title="${escapeHtml(keyText)}">${escapeHtml(keyText)}</code>`
      : `<code>-</code>`;
    const tags = Array.isArray(m.tags) ? m.tags : [];
    const tagsDisplay = tags.length
      ? tags.map((tag) => `<span class="tag-chip">${escapeHtml(tag.name || "")}</span>`).join("")
      : "-";
    tr.innerHTML = `<td>${m.id}</td><td>${addressDisplay}</td><td>${keyDisplay}</td><td>${noteDisplay}<div class="tag-list" style="margin-top:6px">${tagsDisplay}</div></td><td><span class="status-chip ${statusClass}" title="${statusLabel}">${statusLabel}</span></td><td>${formatDateOnly(m.created_at)}</td><td>${m.message_count || 0}</td>
      <td>
        <div class="table-actions">
          <button data-a="copy" data-id="${m.id}" class="icon-btn ghost" title="复制凭据" aria-label="复制凭据">⧉</button>
          <button data-a="edit-note" data-id="${m.id}" class="icon-btn ghost" title="编辑备注" aria-label="编辑备注">✎</button>
          <button data-a="edit-tags" data-id="${m.id}" class="icon-btn ghost" title="编辑标签" aria-label="编辑标签">🏷</button>
          <button data-a="reset-key" data-id="${m.id}" class="icon-btn" title="重置密钥" aria-label="重置密钥">↻</button>
          <button data-a="toggle" data-id="${m.id}" class="icon-btn secondary" title="${m.active ? "停用邮箱" : "启用邮箱"}" aria-label="${m.active ? "停用邮箱" : "启用邮箱"}">${m.active ? "⏸" : "▶"}</button>
          <button data-a="view-mails" data-id="${m.id}" class="icon-btn ghost" title="查看最近邮件" aria-label="查看最近邮件">✉</button>
          <button data-a="delete" data-id="${m.id}" class="icon-btn warn" title="删除邮箱" aria-label="删除邮箱">🗑</button>
        </div>
      </td>`;
    tbody.appendChild(tr);
  }
  const total = Number(mailboxPagination.total || 0);
  const offset = Number(mailboxPagination.offset || 0);
  const limit = Number(mailboxPagination.limit || 50);
  const page = Math.floor(offset / limit) + 1;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const start = total === 0 ? 0 : offset + 1;
  const end = total === 0 ? 0 : offset + mailboxesCache.length;
  el("mailboxes-summary").textContent = total === 0
    ? "当前没有邮箱记录"
    : `共 ${total} 个邮箱，当前显示 ${start}-${end}`;
  el("mailboxes-page-info").textContent = `第 ${page} / ${totalPages} 页`;
  el("btn-prev-page").disabled = offset <= 0;
  el("btn-next-page").disabled = offset + limit >= total;
}

async function refreshMailboxes(resetOffset = false) {
  const keyword = el("search-mailbox").value.trim();
  if (resetOffset || keyword !== mailboxPagination.keyword) {
    mailboxPagination.offset = 0;
  }
  mailboxPagination.keyword = keyword;
  const params = new URLSearchParams({
    limit: String(mailboxPagination.limit),
    offset: String(mailboxPagination.offset),
  });
  if (keyword) params.set("keyword", keyword);
  if (tagFilterValue) params.set("tag_id", tagFilterValue);
  if (statusFilterValue) params.set("status", statusFilterValue);
  const res = await api(`/web/admin/mailboxes?${params.toString()}`);
  if (res.status !== 200 || !res.data.ok) { setResult(res.data); setStatus("读取邮箱列表失败", "error"); return; }
  mailboxesCache = res.data.mailboxes || [];
  mailboxPagination.total = Number(res.data.total || 0);
  mailboxPagination.limit = Number(res.data.limit || mailboxPagination.limit || 50);
  mailboxPagination.offset = Number(res.data.offset || 0);
  renderMailboxesTable();
  setStatus(`已加载 ${mailboxesCache.length} 个邮箱，本次分页总数 ${mailboxPagination.total}`, "ok");
}

el("btn-create-mailbox").onclick = async () => {
  const button = el("btn-create-mailbox");
  button.disabled = true;
  const address = el("new-mailbox").value.trim();
  const note = el("new-mailbox-note").value.trim();
  if (!address) {
    setStatus("请输入邮箱地址", "error");
    button.disabled = false;
    return;
  }
  const tagIds = selectedTagIds("new-mailbox-tags");
  setStatus("创建邮箱中...", "");
  const createRes = await api("/web/admin/mailboxes", { method: "POST", body: JSON.stringify({ address, note, tag_ids: tagIds }) });
  if (!(createRes.status === 200 && createRes.data.ok)) {
    setResult(createRes.data);
    setStatus(`创建失败: ${createRes.data.error || "操作失败"}`, "error");
    button.disabled = false;
    return;
  }
  const mailbox = createRes.data.mailbox || {};
  const credential = String(createRes.data.credential || "").trim() || `${mailbox.address || ""}----${createRes.data.access_key || ""}`.trim();
  if (credential) {
    await copyCreatedCredentials([credential], {
      successMessage: "邮箱与密钥创建成功，凭据已复制到剪贴板",
      fallbackMessage: "邮箱与密钥创建成功，但浏览器不支持自动复制，请从结果面板手动复制",
      resultPayload: createRes.data,
    });
  } else {
    setResult(createRes.data);
    setStatus("邮箱与密钥创建成功", "ok");
  }
  el("new-mailbox").value = "";
  el("new-mailbox-note").value = "";
  el("new-mailbox-tags").selectedIndex = -1;
  await refreshMailboxes();
  button.disabled = false;
};
el("btn-bulk-import").onclick = async () => {
  const button = el("btn-bulk-import");
  button.disabled = true;
  const content = el("bulk-mailboxes").value;
  const note = el("bulk-mailbox-note").value.trim();
  if (!String(content || "").trim()) {
    setStatus("请先粘贴要导入的邮箱列表", "error");
    button.disabled = false;
    return;
  }
  const tagIds = selectedTagIds("bulk-mailbox-tags");
  setStatus("批量导入中...", "");
  const res = await api("/web/admin/mailboxes/import-bulk", {
    method: "POST",
    body: JSON.stringify({ content, note, tag_ids: tagIds }),
  });
  if (!(res.status === 200 && res.data.ok)) {
    setResult(res.data);
    setStatus(`批量导入失败: ${res.data.error || "操作失败"}`, "error");
    button.disabled = false;
    return;
  }
  const summary = res.data.summary || {};
  const summaryMessage = `批量导入完成：成功 ${summary.created || 0}，重置 ${summary.reset || 0}，跳过 ${summary.skipped || 0}，非法 ${summary.invalid || 0}`;
  const createdCredentials = Array.isArray(summary.results)
    ? summary.results
        .filter((item) => item && (item.status === "created" || item.status === "reset") && item.mailbox && item.mailbox.credential)
        .map((item) => String(item.mailbox.credential || "").trim())
        .filter(Boolean)
    : [];
  if (createdCredentials.length) {
    await copyCreatedCredentials(createdCredentials, {
      successMessage: `${summaryMessage}；新凭据已复制到剪贴板`,
      fallbackMessage: `${summaryMessage}；浏览器不支持自动复制，请从结果面板手动复制`,
      resultPayload: res.data,
    });
  } else {
    setResult(res.data);
    setStatus(summaryMessage, "ok");
  }
  el("bulk-mailboxes").value = "";
  el("bulk-mailbox-tags").selectedIndex = -1;
  await refreshMailboxes(true);
  button.disabled = false;
};
el("btn-import-csv").onclick = async () => {
  const button = el("btn-import-csv");
  button.disabled = true;
  const fileInput = el("csv-import-file");
  const note = el("csv-import-note").value.trim();
  const file = fileInput && fileInput.files ? fileInput.files[0] : null;
  if (!file) {
    setStatus("请先选择 CSV 文件", "error");
    button.disabled = false;
    return;
  }
  setStatus("正在导入 CSV...", "");
  try {
    const content = await file.text();
    const res = await api("/web/admin/mailboxes/import-csv", {
      method: "POST",
      body: JSON.stringify({ content, note, filename: file.name }),
    });
    setResult(res.data);
    if (!(res.status === 200 && res.data.ok)) {
      setStatus(`CSV 导入失败: ${res.data.error || "操作失败"}`, "error");
      button.disabled = false;
      return;
    }
    const summary = res.data.summary || {};
    setStatus(`CSV 导入完成：新增 ${summary.created || 0}，覆盖 ${summary.updated || 0}，非法 ${summary.invalid || 0}`, "ok");
    if (fileInput) fileInput.value = "";
    el("csv-import-note").value = "";
    await refreshMailboxes(true);
  } finally {
    button.disabled = false;
  }
};
el("btn-create-tag").onclick = async () => {
  const button = el("btn-create-tag");
  button.disabled = true;
  const name = el("new-tag-name").value.trim();
  if (!name) {
    setStatus("请输入标签名", "error");
    button.disabled = false;
    return;
  }
  const res = await api("/web/admin/tags", { method: "POST", body: JSON.stringify({ name }) });
  setResult(res.data);
  if (!(res.status === 200 && res.data.ok)) {
    setStatus(`创建标签失败: ${res.data.error || "操作失败"}`, "error");
    button.disabled = false;
    return;
  }
  el("new-tag-name").value = "";
  await refreshTags();
  await refreshMailboxes(true);
  setStatus("标签创建成功", "ok");
  button.disabled = false;
};
el("btn-refresh").onclick = () => refreshMailboxes(true);
el("btn-toggle-result").onclick = () => {
  const panel = el("result-panel");
  setResultOpen(panel.classList.contains("collapsed"));
};
el("btn-export-mailboxes").onclick = () => {
  const keyword = el("search-mailbox").value.trim();
  const params = new URLSearchParams();
  if (keyword) params.set("keyword", keyword);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  window.location.href = `/web/admin/mailboxes/export.csv${suffix}`;
};
el("search-mailbox").oninput = () => refreshMailboxes(true);
el("filter-tag").onchange = async () => {
  tagFilterValue = el("filter-tag").value;
  await refreshMailboxes(true);
};
el("filter-status").onchange = async () => {
  statusFilterValue = el("filter-status").value;
  await refreshMailboxes(true);
};
el("btn-prev-page").onclick = async () => {
  mailboxPagination.offset = Math.max(0, mailboxPagination.offset - mailboxPagination.limit);
  await refreshMailboxes(false);
};
el("btn-next-page").onclick = async () => {
  mailboxPagination.offset += mailboxPagination.limit;
  await refreshMailboxes(false);
};

el("btn-logout").onclick = async () => {
  await api("/web/auth/logout", { method: "POST" });
  location.href = "/web/query";
};

el("btn-change-pass").onclick = async () => {
  const oldPassword = prompt("请输入当前密码");
  if (!oldPassword) return;
  const newPassword = prompt("请输入新密码（至少6位）");
  if (!newPassword) return;
  setStatus("正在修改密码...", "");
  const res = await api("/web/me/change-password", {
    method: "POST",
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
  setResult(res.data);
  if (res.status === 200 && res.data.ok) {
    setStatus("密码修改成功。", "ok");
    return;
  }
  setStatus(`修改失败: ${res.data.error || "操作失败"}`, "error");
};

document.addEventListener("click", async (ev) => {
  const target = ev.target;
  if (!target || !target.dataset) return;
  const action = target.dataset.a;
  const id = target.dataset.id;
  if (!action || !id) return;
  const mailbox = mailboxesCache.find((item) => String(item.id) === String(id));
  if (!mailbox) return;
  if (action === "copy") {
    const text = `${mailbox.address}----${mailbox.access_key}`;
    setResult({ credential: text });
    const copied = await copyText(text);
    if (copied) {
      setStatus("凭据已复制", "ok");
    } else {
      setStatus("浏览器不支持自动复制，请从结果面板手动复制", "error");
    }
    return;
  }
  if (action === "edit-note") {
    const nextNote = prompt("请输入备注", mailbox.note || "");
    if (nextNote === null) return;
    const res = await api(`/web/admin/mailboxes/${id}/note`, { method: "POST", body: JSON.stringify({ note: nextNote }) });
    setResult(res.data);
    setStatus(res.status === 200 && res.data.ok ? "备注已更新" : "备注更新失败", res.status === 200 && res.data.ok ? "ok" : "error");
    await refreshMailboxes();
    return;
  }
  if (action === "edit-tags") {
    openTagEditModal(mailbox);
    return;
  }
  if (action === "reset-key") {
    const res = await api(`/web/admin/mailboxes/${id}/reset-key`, { method: "POST", body: JSON.stringify({}) });
    setResult(res.data);
    setStatus(res.status === 200 && res.data.ok ? "密钥已重置" : "密钥重置失败", res.status === 200 && res.data.ok ? "ok" : "error");
    await refreshMailboxes();
    return;
  }
  if (action === "toggle") {
    const res = await api(`/web/admin/mailboxes/${id}/toggle-active`, { method: "POST", body: JSON.stringify({ active: !mailbox.active }) });
    setResult(res.data);
    setStatus(res.status === 200 && res.data.ok ? "状态已更新" : "状态更新失败", res.status === 200 && res.data.ok ? "ok" : "error");
    await refreshMailboxes();
    return;
  }
  if (action === "view-mails") {
    await openMailboxMails(mailbox);
    return;
  }
  if (action === "delete") {
    if (!confirm(`确定删除邮箱「${mailbox.address}」？\n将标记为「已删除」并从列表隐藏（可按状态筛选「已删除」查看），不会真正清除数据。`)) return;
    const res = await api(`/web/admin/mailboxes/${id}/delete`, { method: "POST", body: JSON.stringify({}) });
    setResult(res.data);
    setStatus(res.status === 200 && res.data.ok ? "邮箱已删除" : `删除失败: ${res.data.error || "操作失败"}`, res.status === 200 && res.data.ok ? "ok" : "error");
    await refreshMailboxes();
    return;
  }
});

let mbMailsList = [];
async function openMailboxMails(mailbox) {
  const res = await api(`/web/admin/mailboxes/${mailbox.id}/emails`, { method: "GET" });
  if (!(res.status === 200 && res.data.ok)) {
    setStatus(`加载邮件失败: ${res.data.error || "操作失败"}`, "error");
    return;
  }
  mbMailsList = res.data.emails || [];
  el("mbMailsTitle").textContent = `邮箱邮件 · ${mailbox.address}`;
  el("mbMailsSub").textContent = `共 ${mbMailsList.length} 封（最多展示最近 ${mbMailsList.length} 封），点击查看正文`;
  const box = el("mbMailsList");
  box.innerHTML = mbMailsList.length
    ? mbMailsList.map((email) => `
        <article class="mail-item" data-open="${Number(email.id || 0)}">
          <div class="mail-top">
            <div>
              <h3 class="mail-subject">${escapeHtml(email.subject || "(无主题)")}</h3>
              <div class="mail-meta"><span class="mail-address">发件：${escapeHtml(email.from || "-")}</span></div>
            </div>
            <div class="mail-meta">
              <span>${escapeHtml(formatBeijingDateTime(email.received_at))}</span>
              <span>${escapeHtml(email.mail_type || "unknown")}</span>
            </div>
          </div>
          <p class="mail-preview">${escapeHtml(email.preview || "（无预览）")}</p>
        </article>`).join("")
    : '<div class="empty-state">该邮箱暂无邮件</div>';
  el("mailboxMailsModal").classList.add("open");
}
function closeMailboxMails() { el("mailboxMailsModal").classList.remove("open"); }
el("mbMailsClose").onclick = closeMailboxMails;
el("mailboxMailsModal").addEventListener("click", (ev) => {
  if (ev.target === el("mailboxMailsModal")) closeMailboxMails();
});
el("mbMailsList").addEventListener("click", async (ev) => {
  const card = ev.target && typeof ev.target.closest === "function" ? ev.target.closest("[data-open]") : null;
  if (!card) return;
  const id = card.getAttribute("data-open") || "";
  if (id) await openMailDetail(id);
});
document.addEventListener("click", async (ev) => {
  const target = ev.target;
  const tagId = target && target.dataset ? target.dataset.tagDelete : "";
  if (!tagId) return;
  const confirmed = confirm("删除该标签后，会从所有邮箱上移除，确定继续吗？");
  if (!confirmed) return;
  const res = await api(`/web/admin/tags/${tagId}/delete`, { method: "POST", body: JSON.stringify({}) });
  setResult(res.data);
  setStatus(res.status === 200 && res.data.ok ? "标签已删除" : `标签删除失败: ${res.data.error || "操作失败"}`, res.status === 200 && res.data.ok ? "ok" : "error");
  await refreshTags();
  await refreshMailboxes(true);
});
el("btn-close-tag-modal").onclick = closeTagEditModal;
el("btn-cancel-tag-modal").onclick = closeTagEditModal;
el("btn-save-tag-modal").onclick = async () => {
  if (!currentTagEditMailboxId) {
    closeTagEditModal();
    return;
  }
  const selected = Array.from(document.querySelectorAll('[data-tag-option]:checked'))
    .map((node) => Number(node.getAttribute('data-tag-option') || '0'))
    .filter((value) => Number.isInteger(value) && value > 0);
  const res = await api(`/web/admin/mailboxes/${currentTagEditMailboxId}/tags`, {
    method: "POST",
    body: JSON.stringify({ tag_ids: selected }),
  });
  setResult(res.data);
  if (!(res.status === 200 && res.data.ok)) {
    setStatus(`标签更新失败: ${res.data.error || "操作失败"}`, "error");
    return;
  }
  closeTagEditModal();
  await refreshTags();
  await refreshMailboxes();
  setStatus("标签已更新", "ok");
};
el("tagEditModal").addEventListener("click", (ev) => {
  if (ev.target === el("tagEditModal")) closeTagEditModal();
});

// ---- 收件箱视图（合并自原 /web/admin/inbox 页面）----
let inboxItems = [];
let inboxPagination = { keyword: "", limit: 20, offset: 0, total: 0 };
let currentDetailEmail = null;
let inboxLoaded = false;

function setInboxStatus(text, kind = "") {
  const node = el("inbox-status");
  if (!node) return;
  node.textContent = text;
  node.classList.remove("ok", "error");
  if (kind) node.classList.add(kind);
}

function formatBeijingDateTime(value) {
  const text = String(value || "").trim();
  if (!text) return "-";
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  const second = String(date.getSeconds()).padStart(2, "0");
  return `${year}-${month}-${day} ${hour}:${minute}:${second}`;
}

function renderInboxList() {
  const container = el("inboxList");
  if (!inboxItems.length) {
    container.innerHTML = '<div class="empty-state">当前页没有邮件</div>';
  } else {
    container.innerHTML = inboxItems.map((email) => `
      <article class="mail-item" data-open="${Number(email.id || 0)}">
        <div class="mail-top">
          <div>
            <h3 class="mail-subject">${escapeHtml(email.subject || "(无主题)")}</h3>
            <div class="mail-meta">
              <span class="mail-address">收件：${escapeHtml(email.to || "-")}</span>
              <span class="mail-address">发件：${escapeHtml(email.from || "-")}</span>
            </div>
          </div>
          <div class="mail-meta">
            <span>${escapeHtml(formatBeijingDateTime(email.received_at))}</span>
            <span>${escapeHtml(email.mail_type || "unknown")}</span>
          </div>
        </div>
        <p class="mail-preview">${escapeHtml(email.preview || "（无预览）")}</p>
      </article>
    `).join("");
  }
  const total = Number(inboxPagination.total || 0);
  const offset = Number(inboxPagination.offset || 0);
  const limit = Number(inboxPagination.limit || 20);
  const page = Math.floor(offset / limit) + 1;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const start = total === 0 ? 0 : offset + 1;
  const end = total === 0 ? 0 : offset + inboxItems.length;
  el("inbox-summary").textContent = total === 0 ? "当前没有邮件" : `共 ${total} 封邮件，当前显示 ${start}-${end}`;
  el("inbox-page-info").textContent = `第 ${page} / ${totalPages} 页`;
  el("inbox-prev-page").disabled = offset <= 0;
  el("inbox-next-page").disabled = offset + limit >= total;
}

async function refreshInbox() {
  const keyword = el("search-inbox").value.trim();
  if (keyword !== inboxPagination.keyword) {
    inboxPagination.offset = 0;
  }
  inboxPagination.keyword = keyword;
  const params = new URLSearchParams({
    limit: String(inboxPagination.limit),
    offset: String(inboxPagination.offset),
  });
  if (keyword) params.set("keyword", keyword);
  const res = await api(`/web/admin/inbox/list?${params.toString()}`);
  if (res.status !== 200 || !res.data.ok) {
    setInboxStatus(`读取收件箱失败: ${res.data.error || "操作失败"}`, "error");
    return;
  }
  inboxItems = res.data.emails || [];
  inboxPagination.limit = Number(res.data.limit || inboxPagination.limit || 20);
  inboxPagination.offset = Number(res.data.offset || 0);
  inboxPagination.total = Number(res.data.total || 0);
  renderInboxList();
  setInboxStatus(`已加载 ${inboxItems.length} 封邮件，本次分页总数 ${inboxPagination.total}`, "ok");
}

function closeModal() {
  el("emailModal").classList.remove("open");
  el("emailBodyFrame").src = "about:blank";
  currentDetailEmail = null;
}

function setModalView(view) {
  const email = currentDetailEmail || {};
  const frame = el("emailBodyFrame");
  const fallback = el("emailBodyFallback");
  const rawButton = el("viewRawButton");
  const renderedButton = el("viewRenderedButton");
  const showRaw = view === "raw";
  rawButton.classList.toggle("active", showRaw);
  renderedButton.classList.toggle("active", !showRaw);
  rawButton.classList.toggle("secondary", !showRaw);
  renderedButton.classList.toggle("secondary", showRaw);
  if (showRaw) {
    frame.style.display = "none";
    fallback.style.display = "block";
    fallback.textContent = email.raw_mail || email.body || email.text || "（无正文）";
    return;
  }
  const html = String(email.html || "").trim();
  if (html) {
    fallback.style.display = "none";
    frame.style.display = "block";
    frame.srcdoc = html;
    return;
  }
  frame.style.display = "none";
  fallback.style.display = "block";
  fallback.textContent = email.text || email.body || email.raw_mail || "（无正文）";
}

async function openMailDetail(mailId) {
  const res = await api(`/web/admin/inbox/${Number(mailId || 0)}`);
  if (!(res.status === 200 && res.data.ok && res.data.email)) {
    setInboxStatus(`加载详情失败: ${res.data.error || "操作失败"}`, "error");
    return;
  }
  const email = res.data.email;
  currentDetailEmail = email;
  el("modalSubject").textContent = email.subject || "(无主题)";
  el("modalFrom").textContent = email.from || "-";
  el("modalTo").textContent = email.to || "-";
  el("modalDate").textContent = formatBeijingDateTime(email.received_at);
  el("modalType").textContent = email.mail_type || "unknown";
  setModalView("rendered");
  el("emailModal").classList.add("open");
}

el("inbox-refresh").onclick = async () => { await refreshInbox(); };
el("search-inbox").oninput = async () => { await refreshInbox(); };
el("inbox-prev-page").onclick = async () => {
  inboxPagination.offset = Math.max(0, inboxPagination.offset - inboxPagination.limit);
  await refreshInbox();
};
el("inbox-next-page").onclick = async () => {
  inboxPagination.offset += inboxPagination.limit;
  await refreshInbox();
};
el("inboxList").addEventListener("click", async (ev) => {
  const target = ev.target;
  const card = target && typeof target.closest === "function" ? target.closest("[data-open]") : null;
  if (!card) return;
  const id = card.getAttribute("data-open") || "";
  if (!id) return;
  await openMailDetail(id);
});
el("closeModalButton").onclick = closeModal;
el("viewRawButton").onclick = () => setModalView("raw");
el("viewRenderedButton").onclick = () => setModalView("rendered");
el("emailModal").addEventListener("click", (ev) => {
  if (ev.target === el("emailModal")) closeModal();
});

// ---- 概览视图 ----
let dashboardLoaded = false;
async function loadDashboard() {
  const [mb, ib] = await Promise.all([
    api("/web/admin/mailboxes?limit=200"),
    api("/web/admin/inbox/list?limit=1"),
  ]);
  if (mb.status === 200 && mb.data.ok) {
    const list = mb.data.mailboxes || [];
    el("stat-mailboxes").textContent = String(mb.data.total != null ? mb.data.total : list.length);
    // ponytail: 启用数统计前 200 个邮箱即可，精确全量统计需后端再加聚合接口
    el("stat-active").textContent = String(list.filter((m) => m.active).length);
  }
  el("stat-tags").textContent = String(tagCache.length);
  if (ib.status === 200 && ib.data.ok) {
    el("stat-inbox").textContent = String(ib.data.total != null ? ib.data.total : 0);
  }
  dashboardLoaded = true;
}
el("btn-refresh-dashboard").onclick = () => { dashboardLoaded = false; loadDashboard(); };

// ---- 视图路由 ----
const VIEWS = ["dashboard", "mailboxes", "create", "tags", "inbox", "cdk"];
function showView(name) {
  if (!VIEWS.includes(name)) name = "dashboard";
  for (const sec of document.querySelectorAll(".view")) {
    sec.classList.toggle("hidden", sec.getAttribute("data-view") !== name);
  }
  for (const item of document.querySelectorAll(".nav-item")) {
    item.classList.toggle("active", item.getAttribute("data-view") === name);
  }
  if (location.hash !== "#" + name) {
    history.replaceState(null, "", "#" + name);
  }
  if (name === "dashboard" && !dashboardLoaded) loadDashboard();
  if (name === "inbox" && !inboxLoaded) { inboxLoaded = true; refreshInbox(); }
  if (name === "cdk") { renderCdkTagOptions(); refreshStats(); refreshStock(); refreshCdks(); }
}
el("nav").addEventListener("click", (ev) => {
  const item = ev.target && typeof ev.target.closest === "function" ? ev.target.closest("[data-view]") : null;
  if (!item) return;
  showView(item.getAttribute("data-view"));
});
window.addEventListener("hashchange", () => {
  showView((location.hash || "#dashboard").slice(1));
});

// ---- 卡密售卖 ----
let cdkPagination = { keyword: "", status: "", limit: 20, offset: 0, total: 0 };
function setCdkStatus(text, kind = "") {
  const node = el("cdk-status-bar");
  if (!node) return;
  node.textContent = text;
  node.classList.remove("ok", "error");
  if (kind) node.classList.add(kind);
}
function renderCdkTagOptions() {
  const select = el("cdk-tag");
  if (select) {
    const current = select.value;
    select.innerHTML = '<option value="0">任意品类</option>';
    for (const tag of tagCache) {
      const option = document.createElement("option");
      option.value = String(tag.id);
      option.textContent = `${tag.name} (${tag.mailbox_count || 0})`;
      select.appendChild(option);
    }
    if (current) select.value = current;
  }
  const filter = el("cdk-tag-filter");
  if (filter) {
    const current = filter.value;
    filter.innerHTML = '<option value="">全部分类</option>';
    for (const tag of tagCache) {
      const option = document.createElement("option");
      option.value = String(tag.id);
      option.textContent = `${tag.name} (${tag.mailbox_count || 0})`;
      filter.appendChild(option);
    }
    if (current) filter.value = current;
  }
}
const LOW_STOCK_THRESHOLD = 5;
function stockChipClass(available) {
  if ((available || 0) <= 0) return "status-chip inactive";   // 售罄
  if (available < LOW_STOCK_THRESHOLD) return "status-chip warn";  // 低库存预警
  return "status-chip";
}
async function refreshStats() {
  const box = el("sales-stats");
  if (!box) return;
  const res = await api("/web/admin/stats");
  if (!(res.status === 200 && res.data.ok)) { box.innerHTML = '<span class="muted">统计加载失败</span>'; return; }
  const s = res.data.stats || {};
  box.innerHTML = [
    `<span class="status-chip active">今日售出 ${s.today_sold || 0}</span>`,
    `<span class="status-chip active">今日兑换 ${s.today_redemptions || 0}</span>`,
    `<span class="status-chip">累计已售 ${s.sold || 0}</span>`,
    `<span class="${stockChipClass(s.available)}">当前可售 ${s.available || 0}</span>`,
    `<span class="status-chip">邮箱总数 ${s.total || 0}</span>`,
  ].join(" ");
}
async function refreshStock() {
  const box = el("stock-summary");
  if (!box) return;
  const res = await api("/web/admin/stock");
  if (!(res.status === 200 && res.data.ok)) { box.innerHTML = '<span class="muted">库存加载失败</span>'; return; }
  const stock = res.data.stock || {};
  const totals = stock.totals || {};
  const parts = [`<span class="status-chip active">总计 可售 ${totals.available || 0} · 预售 ${totals.presale || 0} · 已售 ${totals.sold || 0}</span>`];
  for (const t of (stock.tags || [])) {
    parts.push(`<span class="${stockChipClass(t.available)}">${escapeHtml(t.name)}：可售 ${t.available || 0} · 预售 ${t.presale || 0} · 已售 ${t.sold || 0}</span>`);
  }
  const ut = stock.untagged || {};
  if ((ut.available || 0) || (ut.presale || 0) || (ut.sold || 0)) {
    parts.push(`<span class="status-chip">无标签：可售 ${ut.available || 0} · 预售 ${ut.presale || 0} · 已售 ${ut.sold || 0}</span>`);
  }
  box.innerHTML = parts.join(" ");
}
async function refreshCdks(resetOffset) {
  if (resetOffset) cdkPagination.offset = 0;
  cdkPagination.keyword = el("cdk-search").value.trim();
  cdkPagination.status = el("cdk-status-filter").value;
  cdkPagination.tagId = (el("cdk-tag-filter") && el("cdk-tag-filter").value) || "";
  const params = new URLSearchParams({ limit: String(cdkPagination.limit), offset: String(cdkPagination.offset) });
  if (cdkPagination.keyword) params.set("keyword", cdkPagination.keyword);
  if (cdkPagination.status) params.set("status", cdkPagination.status);
  if (cdkPagination.tagId) params.set("tag_id", cdkPagination.tagId);
  const res = await api(`/web/admin/cdks?${params.toString()}`);
  const list = el("cdk-list");
  if (!(res.status === 200 && res.data.ok)) { list.innerHTML = '<div class="empty-state">加载失败</div>'; return; }
  const items = res.data.cdks || [];
  cdkPagination.total = Number(res.data.total || 0);
  const stateLabel = { active: "可用", used: "已用", expired: "已过期", disabled: "已撤销" };
  if (!items.length) {
    list.innerHTML = '<div class="empty-state">暂无卡密</div>';
  } else {
    list.innerHTML = items.map((c) => `
      <div class="mail-item">
        <div class="mono">${escapeHtml(c.code)}</div>
        <div class="muted" style="font-size:12px">
          品类: ${escapeHtml(c.tag_name || "任意")} · 每码 ${c.quantity} · 已用 ${c.used_count}/${c.max_uses}
          · 状态: ${stateLabel[c.state] || c.state}
          ${c.expires_at ? " · 到期 " + escapeHtml(formatBeijingDateTime(c.expires_at)) : ""}
          ${c.batch_label ? " · 批次 " + escapeHtml(c.batch_label) : ""}
        </div>
        <div style="margin-top:6px; display:flex; gap:8px">
          <button class="ghost" data-cdk-copy="${escapeHtml(c.code)}">复制</button>
          ${c.active ? `<button class="secondary" data-cdk-revoke="${c.id}">撤销</button>` : ""}
        </div>
      </div>
    `).join("");
  }
  const page = Math.floor(cdkPagination.offset / cdkPagination.limit) + 1;
  el("cdk-page-info").textContent = `第 ${page} 页`;
  el("cdk-summary").textContent = `共 ${cdkPagination.total} 条`;
}
el("btn-gen-cdk").onclick = async () => {
  const button = el("btn-gen-cdk");
  button.disabled = true;
  const tagId = Number(el("cdk-tag").value || 0);
  const quantity = Math.max(1, Number(el("cdk-quantity").value || 1));
  const count = Math.max(1, Number(el("cdk-count").value || 1));
  const batch = el("cdk-batch").value.trim();
  const note = el("cdk-note").value.trim();
  let expires = el("cdk-expires").value.trim();
  if (expires) { try { expires = new Date(expires).toISOString(); } catch { expires = ""; } }
  setCdkStatus("生成中...", "");
  const res = await api("/web/admin/cdks", {
    method: "POST",
    body: JSON.stringify({ count, tag_id: tagId, quantity, batch_label: batch, note, expires_at: expires }),
  });
  if (!(res.status === 200 && res.data.ok)) {
    if (res.data.error === "insufficient_presale") {
      setCdkStatus(`预售池库存不足：可移动 ${res.data.available || 0} 个，本次需要 ${res.data.required || 0} 个`, "error");
    } else {
      setCdkStatus(`生成失败: ${res.data.error || "操作失败"}`, "error");
    }
    button.disabled = false;
    return;
  }
  const codes = (res.data.codes || []).map((c) => c.code).filter(Boolean);
  await copyCreatedCredentials(codes, {
    successMessage: `已生成 ${codes.length} 个卡密并复制到剪贴板`,
    fallbackMessage: `已生成 ${codes.length} 个卡密，浏览器不支持自动复制，请从结果面板手动复制`,
    resultPayload: res.data,
  });
  setCdkStatus(`已生成 ${codes.length} 个卡密`, "ok");
  await refreshStats();
  await refreshStock();
  await refreshCdks(true);
  button.disabled = false;
};
el("cdk-list").addEventListener("click", async (ev) => {
  const target = ev.target;
  if (!target || typeof target.getAttribute !== "function") return;
  const copyCode = target.getAttribute("data-cdk-copy");
  if (copyCode) { await copyText(copyCode); setCdkStatus("已复制卡密", "ok"); return; }
  const revokeId = target.getAttribute("data-cdk-revoke");
  if (revokeId) {
    if (!confirm("确定撤销该卡密？撤销后无法兑换。")) return;
    const res = await api(`/web/admin/cdks/${revokeId}/revoke`, { method: "POST", body: JSON.stringify({}) });
    if (res.status === 200 && res.data.ok) { setCdkStatus("已撤销", "ok"); await refreshCdks(); }
    else setCdkStatus(`撤销失败: ${res.data.error || "操作失败"}`, "error");
  }
});
el("cdk-search").oninput = () => refreshCdks(true);
el("cdk-status-filter").onchange = () => refreshCdks(true);
if (el("cdk-tag-filter")) el("cdk-tag-filter").onchange = () => refreshCdks(true);
el("btn-refresh-stock").onclick = () => { refreshStats(); refreshStock(); };
el("btn-replace").onclick = async () => {
  const address = el("replace-address").value.trim();
  if (!address) { el("replace-status").textContent = "请输入邮箱地址"; return; }
  el("replace-status").textContent = "换货中...";
  const res = await api("/web/admin/mailboxes/replace", { method: "POST", body: JSON.stringify({ address }) });
  if (res.status === 200 && res.data.ok) {
    el("replace-status").textContent = "已换货，新邮箱凭据：" + res.data.credential;
    el("replace-address").value = "";
    refreshStats(); refreshStock();
  } else {
    const map = { mailbox_not_found: "邮箱不存在", not_sold: "该邮箱未售出，无需换货", insufficient_stock: "同标签暂无可补发库存", missing_address: "请输入邮箱地址" };
    el("replace-status").textContent = "换货失败：" + (map[res.data.error] || res.data.error || "操作失败");
  }
};
el("cdk-export").onclick = () => {
  const params = new URLSearchParams();
  if (el("cdk-search").value.trim()) params.set("keyword", el("cdk-search").value.trim());
  if (el("cdk-status-filter").value) params.set("status", el("cdk-status-filter").value);
  if (el("cdk-tag-filter") && el("cdk-tag-filter").value) params.set("tag_id", el("cdk-tag-filter").value);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  window.location.href = `/web/admin/cdks/export.txt${suffix}`;
};
el("cdk-prev-page").onclick = () => {
  cdkPagination.offset = Math.max(0, cdkPagination.offset - cdkPagination.limit);
  refreshCdks();
};
el("cdk-next-page").onclick = () => {
  if (cdkPagination.offset + cdkPagination.limit < cdkPagination.total) {
    cdkPagination.offset += cdkPagination.limit;
    refreshCdks();
  }
};

(async () => {
  if (!(await ensureAdmin())) return;
  setResultOpen(false);
  await refreshTags();
  await refreshMailboxes();
  showView((location.hash || "#dashboard").slice(1));
})();
</script>
</body>
</html>"""


    def _dispatch_safely(self, impl) -> None:
        # Any unhandled error becomes a clean 500 JSON instead of a leaked
        # stack trace or a dropped connection.
        try:
            impl()
        except Exception:
            self.app.logger.exception("unhandled error on %s %s", self.command, self.path)
            try:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "internal_error"}
                )
            except Exception:
                pass

    def do_GET(self) -> None:
        self._dispatch_safely(self._do_GET_impl)

    def _do_GET_impl(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True, "now": utcnow_iso()})
            return
        if parsed.path in {"/", "/web", "/web/"}:
            self._send_redirect("/web/query")
            return
        if parsed.path in {"/web/query", "/web/login"}:
            self._send_html(HTTPStatus.OK, self._render_login_page())
            return
        if parsed.path == "/web/auth/admin-status":
            self._send_json(HTTPStatus.OK, {"ok": True, "needs_setup": not self.app.store.has_any_admin()})
            return
        if parsed.path == "/web/admin/login":
            self._send_html(HTTPStatus.OK, self._render_admin_login_page())
            return
        if parsed.path == "/web/user":
            session_user = self._current_session_user()
            if session_user and str(session_user.get("role") or "").lower() == "admin":
                self._send_redirect("/web/admin")
                return
            self._send_html(HTTPStatus.OK, self._render_user_dashboard_page())
            return
        if parsed.path == "/web/admin":
            session_user = self._current_session_user()
            if not session_user or str(session_user.get("role") or "") != "admin":
                self._send_redirect("/web/admin/login")
                return
            self._send_html(HTTPStatus.OK, self._render_admin_page())
            return
        if parsed.path == "/web/admin/inbox":
            # 收件箱已合并进 /web/admin 的收件箱视图；保留旧地址做兼容跳转
            self._send_redirect("/web/admin#inbox")
            return
        if parsed.path == "/web/me":
            session_user = self._require_session_user()
            if not session_user:
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "user": {
                        "id": int(session_user["user_id"]),
                        "username": str(session_user["username"]),
                        "role": str(session_user["role"]),
                    },
                },
            )
            return
        if parsed.path == "/web/me/mailboxes":
            session_user = self._require_session_user()
            if not session_user:
                return
            mailboxes = self.app.store.list_user_purchased_mailboxes(int(session_user["user_id"]))
            self._send_json(HTTPStatus.OK, {"ok": True, "mailboxes": mailboxes})
            return
        if parsed.path == "/web/me/latest":
            session_user = self._require_session_user()
            if not session_user:
                return
            query = parse_qs(parsed.query or "", keep_blank_values=True)
            address = normalize_address((query.get("address") or [""])[0])
            if not address:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing_address"})
                return
            if not self.app.store.user_has_mailbox(int(session_user["user_id"]), address):
                self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "not_owned"})
                return
            record = self.app.store.latest_message(address)
            if not record:
                self._send_json(HTTPStatus.OK, {"ok": True, "email": None})
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "email": {
                        "id": record.message_id,
                        "to": record.address,
                        "from": record.from_address,
                        "subject": record.subject,
                        "text": record.text or record.body,
                        "html": record.html,
                        "body": record.body,
                        "received_at": record.received_at,
                        "created_at": record.received_at,
                        "verification_code": record.verification_code,
                        "mail_type": record.mail_type,
                        "invite_link": record.invite_link,
                        "process_status": record.process_status,
                    },
                },
            )
            return
        if parsed.path == "/web/me/redemptions":
            session_user = self._require_session_user()
            if not session_user:
                return
            redemptions = self.app.store.list_user_redemptions(int(session_user["user_id"]))
            self._send_json(HTTPStatus.OK, {"ok": True, "redemptions": redemptions})
            return
        if parsed.path == "/web/admin/inbox/list":
            session_user = self._require_admin_session_user()
            if not session_user:
                return
            query = parse_qs(parsed.query or "", keep_blank_values=True)
            keyword = str((query.get("keyword") or [""])[0] or "").strip()
            limit = self._parse_int((query.get("limit") or [str(ADMIN_INBOX_PAGE_SIZE)])[0], default=ADMIN_INBOX_PAGE_SIZE, minimum=1, maximum=200)
            offset = self._parse_int((query.get("offset") or ["0"])[0], default=0, minimum=0, maximum=10_000_000)
            records = self.app.store.list_recent_messages(keyword=keyword, limit=limit, offset=offset)
            total = self.app.store.count_all_messages(keyword=keyword)
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "emails": [self._mail_summary_payload(record) for record in records],
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "keyword": keyword,
                },
            )
            return
        if parsed.path.startswith("/web/admin/inbox/"):
            session_user = self._require_admin_session_user()
            if not session_user:
                return
            message_id = self._extract_admin_inbox_message_id(parsed.path)
            record = self.app.store.get_message_by_id(message_id)
            if not record:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "mail_not_found"})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "email": self._mail_detail_payload(record)})
            return
        if parsed.path == "/web/admin/mailboxes":
            session_user = self._require_admin_session_user()
            if not session_user:
                return
            query = parse_qs(parsed.query or "", keep_blank_values=True)
            keyword = str((query.get("keyword") or [""])[0] or "").strip()
            tag_id = self._parse_int((query.get("tag_id") or ["0"])[0], default=0, minimum=0, maximum=10_000_000)
            status = str((query.get("status") or [""])[0] or "").strip()
            limit = self._parse_int((query.get("limit") or [str(ADMIN_MAILBOX_PAGE_SIZE)])[0], default=ADMIN_MAILBOX_PAGE_SIZE, minimum=1, maximum=200)
            offset = self._parse_int((query.get("offset") or ["0"])[0], default=0, minimum=0, maximum=10_000_000)
            mailboxes, total = self.app.store.list_mailbox_credentials(keyword=keyword, tag_id=tag_id, status=status, limit=limit, offset=offset)
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "mailboxes": mailboxes,
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "keyword": keyword,
                    "tag_id": tag_id,
                    "status": status,
                },
            )
            return
        if parsed.path == "/web/admin/tags":
            session_user = self._require_admin_session_user()
            if not session_user:
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "tags": self.app.store.list_mailbox_tags(),
                },
            )
            return
        if parsed.path == "/web/admin/cdks":
            session_user = self._require_admin_session_user()
            if not session_user:
                return
            query = parse_qs(parsed.query or "", keep_blank_values=True)
            keyword = str((query.get("keyword") or [""])[0] or "").strip()
            status = str((query.get("status") or [""])[0] or "").strip()
            tag_id = self._parse_int((query.get("tag_id") or ["0"])[0], default=0, minimum=0, maximum=10_000_000)
            limit = self._parse_int((query.get("limit") or ["50"])[0], default=50, minimum=1, maximum=200)
            offset = self._parse_int((query.get("offset") or ["0"])[0], default=0, minimum=0, maximum=10_000_000)
            items, total = self.app.store.list_cdks(
                keyword=keyword, status=status, tag_id=tag_id, limit=limit, offset=offset
            )
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "cdks": items, "total": total, "limit": limit, "offset": offset},
            )
            return
        if parsed.path == "/web/admin/stock":
            session_user = self._require_admin_session_user()
            if not session_user:
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "stock": self.app.store.stock_summary_by_tag()})
            return
        if parsed.path == "/web/admin/stats":
            session_user = self._require_admin_session_user()
            if not session_user:
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "stats": self.app.store.sales_stats()})
            return
        if parsed.path == "/web/admin/cdks/export.txt":
            session_user = self._require_admin_session_user()
            if not session_user:
                return
            query = parse_qs(parsed.query or "", keep_blank_values=True)
            keyword = str((query.get("keyword") or [""])[0] or "").strip()
            status = str((query.get("status") or [""])[0] or "").strip()
            tag_id = self._parse_int((query.get("tag_id") or ["0"])[0], default=0, minimum=0, maximum=10_000_000)
            items, _total = self.app.store.list_cdks(
                keyword=keyword, status=status, tag_id=tag_id, limit=200, offset=0
            )
            lines = [str(item.get("code") or "") for item in items if item.get("code")]
            content = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
            self._send_bytes(
                HTTPStatus.OK,
                content,
                content_type="text/plain; charset=utf-8",
                filename="cdks-export.txt",
            )
            return
        if parsed.path == "/web/admin/mailboxes/export.csv":
            session_user = self._require_admin_session_user()
            if not session_user:
                return
            query = parse_qs(parsed.query or "", keep_blank_values=True)
            keyword = str((query.get("keyword") or [""])[0] or "").strip()
            rows = self.app.store.list_mailbox_credentials_for_export(keyword=keyword)
            lines = [f"{row.address}----{row.access_key}" for row in rows]
            content = ("\ufeff" + "\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
            self._send_bytes(
                HTTPStatus.OK,
                content,
                content_type="text/csv; charset=utf-8",
                filename="mailboxes-export.csv",
            )
            return
        if parsed.path.startswith("/web/admin/mailboxes/") and parsed.path.endswith("/emails"):
            session_user = self._require_admin_session_user()
            if not session_user:
                return
            mailbox_id = self._extract_admin_mailbox_id(parsed.path)
            credential = self.app.store.get_mailbox_credential_by_id(mailbox_id)
            if not credential:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "mailbox_not_found"})
                return
            records = self.app.store.list_messages(credential.address, limit=PUBLIC_QUERY_PAGE_SIZE, offset=0)
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "mailbox": {
                        "id": credential.mailbox_id,
                        "address": credential.address,
                        "active": credential.active,
                    },
                    "emails": [self._mail_summary_payload(record) for record in records],
                },
            )
            return
        if parsed.path == "/admin/mails":
            if not self._require_admin_auth():
                return
            query = parse_qs(parsed.query)
            address = self._normalize_query_address((query.get("address") or [""])[0])
            if not address:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing_address"})
                return
            limit = self._parse_int((query.get("limit") or ["5"])[0], default=5, minimum=1, maximum=200)
            offset = self._parse_int((query.get("offset") or ["0"])[0], default=0, minimum=0, maximum=10_000_000)
            records = self.app.store.list_messages(address, limit=limit, offset=offset)
            results = []
            for record in records:
                results.append(
                    {
                        "id": record.message_id,
                        "address": record.address,
                        "from": record.from_address,
                        "subject": record.subject,
                        "received_at": record.received_at,
                        "raw": record.raw_mail or self._compose_raw(record.subject, record.text, record.html, record.body),
                        "raw_header_text": record.raw_header_text,
                        "mail_type": record.mail_type,
                        "invite_link": record.invite_link,
                        "process_status": record.process_status,
                        "processed_at": record.processed_at,
                        "process_note": record.process_note,
                    }
                )
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "results": results,
                    "limit": limit,
                    "offset": offset,
                },
            )
            return
        if parsed.path == "/api/invites/next":
            if not self._require_auth(self.app.api_token):
                return
            query = parse_qs(parsed.query)
            address = self._normalize_query_address((query.get("address") or [""])[0])
            record = self.app.store.next_pending_invite(address)
            if not record:
                self._send_json(HTTPStatus.OK, {"ok": True, "invite": None})
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "invite": {
                        "id": record.message_id,
                        "to": record.address,
                        "from": record.from_address,
                        "subject": record.subject,
                        "text": record.text or record.body,
                        "html": record.html,
                        "body": record.body,
                        "received_at": record.received_at,
                        "mail_type": record.mail_type,
                        "invite_link": record.invite_link,
                        "process_status": record.process_status,
                    },
                },
            )
            return
        if parsed.path != "/api/latest":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        if not self._require_auth(self.app.api_token):
            return
        query = parse_qs(parsed.query)
        address = self._normalize_query_address((query.get("address") or [""])[0])
        if not address:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing_address"})
            return
        record = self.app.store.latest_message(address)
        if not record:
            self._send_json(HTTPStatus.OK, {"ok": True, "email": None})
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "email": {
                    "id": record.message_id,
                    "to": record.address,
                    "from": record.from_address,
                    "subject": record.subject,
                    "text": record.text or record.body,
                    "html": record.html,
                    "body": record.body,
                    "received_at": record.received_at,
                    "created_at": record.received_at,
                    "verification_code": record.verification_code,
                    "mail_type": record.mail_type,
                    "invite_link": record.invite_link,
                    "process_status": record.process_status,
                },
            },
        )

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/admin/mails/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        if not self._require_admin_auth():
            return
        raw_id = parsed.path[len("/admin/mails/") :].strip()
        try:
            message_id = int(raw_id)
        except Exception:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_id"})
            return
        deleted = self.app.store.delete_message(message_id)
        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "deleted": deleted,
                "id": message_id,
            },
        )

    def do_POST(self) -> None:
        self._dispatch_safely(self._do_POST_impl)

    def _body_too_large(self) -> bool:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        if length > MAX_REQUEST_BODY_BYTES:
            # Close the connection so the unread oversized body can't desync
            # the next keep-alive request.
            self.close_connection = True
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "payload_too_large"}
            )
            return True
        return False

    def _do_POST_impl(self) -> None:
        if self._body_too_large():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/web/auth/register":
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            username = normalize_username(payload.get("username"))
            password = str(payload.get("password") or "")
            if not username:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing_username"})
                return
            if len(username) > MAX_USERNAME_LENGTH:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "username_too_long"})
                return
            if len(password) < 6:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "password_too_short"})
                return
            if len(password) > MAX_PASSWORD_LENGTH:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "password_too_long"})
                return
            password_hash = build_password_hash(password)
            created, user, reason = self.app.store.create_user(username, password_hash=password_hash, role="user")
            if not created or not user:
                status = HTTPStatus.CONFLICT if reason == "username_exists" else HTTPStatus.BAD_REQUEST
                self._send_json(status, {"ok": False, "error": reason})
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "user": {
                        "id": user.user_id,
                        "username": user.username,
                        "role": user.role,
                        "active": user.active,
                    },
                },
            )
            return
        if parsed.path == "/web/auth/setup":
            # First-run admin setup: only usable while NO admin exists. Once an
            # admin is created this is permanently locked (409). No shipped creds.
            if self.app.store.has_any_admin():
                self._send_json(HTTPStatus.CONFLICT, {"ok": False, "error": "admin_already_exists"})
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            username = normalize_username(payload.get("username")) or DEFAULT_ADMIN_USERNAME
            password = str(payload.get("password") or "")
            if len(username) > MAX_USERNAME_LENGTH:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "username_too_long"})
                return
            if len(password) < 8:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "password_too_short"})
                return
            if len(password) > MAX_PASSWORD_LENGTH:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "password_too_long"})
                return
            self.app.store.ensure_admin_user(username, build_password_hash(password))
            user = self.app.store.get_user_by_username(username)
            if not user:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "setup_failed"})
                return
            token, expires_at = self.app.create_user_session(int(user["id"]))
            body = json.dumps(
                {"ok": True, "user": {"username": str(user["username"]), "role": "admin"}, "dashboard": "/web/admin"},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._set_session_cookie(token, expires_at)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/web/auth/login":
            # Throttle brute force: too many recent failures from one client are
            # blocked before credentials are even checked.
            limiter_key = f"login:{self._client_ip()}"
            retry_after = self.app.login_limiter.retry_after(limiter_key)
            if retry_after > 0:
                self._send_json(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    {"ok": False, "error": "too_many_attempts", "retry_after": retry_after},
                )
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            username = normalize_username(payload.get("username"))
            password = str(payload.get("password") or "")
            user = self.app.store.get_user_by_username(username)
            if not user or not verify_password(password, str(user.get("password_hash") or "")):
                self.app.login_limiter.record(limiter_key)
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "invalid_credentials"})
                return
            if not bool(user.get("active")):
                self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "user_inactive"})
                return
            self.app.login_limiter.reset(limiter_key)
            token, expires_at = self.app.create_user_session(int(user["id"]))
            body = json.dumps(
                {
                    "ok": True,
                    "user": {
                        "id": int(user["id"]),
                        "username": str(user["username"]),
                        "role": str(user["role"]),
                    },
                    "dashboard": "/web/admin" if str(user["role"]).lower() == "admin" else "/web/query",
                    "expires_at": format_beijing_time(expires_at),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._set_session_cookie(token, expires_at)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/web/auth/logout":
            token = self._extract_session_token()
            if token:
                self.app.store.revoke_session(self.app.build_session_token_hash(token))
            body = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._clear_session_cookie()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/web/me/mailboxes/reset-key":
            session_user = self._require_session_user()
            if not session_user:
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            ok, record, reason = self.app.store.reset_user_mailbox_access_key(
                int(session_user["user_id"]), payload.get("address")
            )
            if not ok or not record:
                status_map = {
                    "not_owned": HTTPStatus.FORBIDDEN,
                    "mailbox_not_found": HTTPStatus.NOT_FOUND,
                    "missing_address": HTTPStatus.BAD_REQUEST,
                    "invalid_user": HTTPStatus.BAD_REQUEST,
                }
                self._send_json(status_map.get(reason, HTTPStatus.BAD_REQUEST), {"ok": False, "error": reason})
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "address": record.address,
                    "access_key": record.access_key,
                    "credential": f"{record.address}----{record.access_key}",
                },
            )
            return
        if parsed.path == "/web/query-mails":
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            credential_raw = payload.get("credential") or ""
            address_raw = payload.get("address") or ""
            key_raw = payload.get("key") or payload.get("access_key") or ""
            address, access_key = parse_mailbox_credential(credential_raw)
            if not address:
                address = normalize_address(address_raw)
            if not access_key:
                access_key = str(key_raw or "").strip()
            verified, credential, reason = self.app.store.verify_mailbox_access(address, access_key)
            if not verified or not credential:
                status = HTTPStatus.BAD_REQUEST if reason in {"missing_address", "missing_access_key"} else HTTPStatus.UNAUTHORIZED
                self._send_json(status, {"ok": False, "error": reason})
                return
            records = self.app.store.list_messages(credential.address, limit=PUBLIC_QUERY_PAGE_SIZE, offset=0)
            total = self.app.store.count_messages(credential.address)
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "mailbox": {
                        "id": credential.mailbox_id,
                        "address": credential.address,
                        "active": credential.active,
                    },
                    "emails": [self._mail_summary_payload(record) for record in records],
                    "total": total,
                },
            )
            return
        if parsed.path == "/web/query-mail-detail":
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            address = normalize_address(payload.get("address"))
            access_key = str(payload.get("key") or payload.get("access_key") or "").strip()
            try:
                message_id = int(payload.get("id") or 0)
            except Exception:
                message_id = 0
            verified, credential, reason = self.app.store.verify_mailbox_access(address, access_key)
            if not verified or not credential:
                status = HTTPStatus.BAD_REQUEST if reason in {"missing_address", "missing_access_key"} else HTTPStatus.UNAUTHORIZED
                self._send_json(status, {"ok": False, "error": reason})
                return
            record = self.app.store.get_message_for_address(credential.address, message_id)
            if not record:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "mail_not_found"})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "email": self._mail_detail_payload(record)})
            return
        if parsed.path == "/web/me/change-password":
            session_user = self._require_session_user()
            if not session_user:
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            old_password = str(payload.get("old_password") or "")
            new_password = str(payload.get("new_password") or "")
            if len(new_password) < 6:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "password_too_short"})
                return
            if len(new_password) > MAX_PASSWORD_LENGTH:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "password_too_long"})
                return
            username = normalize_username(session_user.get("username"))
            user = self.app.store.get_user_by_username(username)
            if not user or not verify_password(old_password, str(user.get("password_hash") or "")):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "invalid_old_password"})
                return
            success, reason = self.app.store.reset_user_password(
                int(session_user["user_id"]),
                build_password_hash(new_password),
            )
            if not success:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": reason})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "changed": True})
            return
        if parsed.path == "/web/user/redeem":
            # Login optional: logged-in users get the mailbox bound to their
            # account; anonymous buyers (user_id=0) just receive the credential
            # and keep it in browser localStorage.
            session_user = self._current_session_user()
            user_id = int(session_user["user_id"]) if session_user else 0
            # Throttle CDK guessing per client: too many bad codes are blocked
            # before processing. Valid redemptions never count toward the limit.
            limiter_key = f"redeem:{self._client_ip()}"
            retry_after = self.app.redeem_limiter.retry_after(limiter_key)
            if retry_after > 0:
                self._send_json(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    {"ok": False, "error": "too_many_attempts", "retry_after": retry_after},
                )
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            code = str(payload.get("code") or "").strip()
            if not code:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing_code"})
                return
            ok, data, reason = self.app.store.redeem_cdk(code, user_id)
            if not ok and reason == "cdk_not_found":
                self.app.redeem_limiter.record(limiter_key)
            if not ok:
                status_map = {
                    "cdk_not_found": HTTPStatus.NOT_FOUND,
                    "missing_code": HTTPStatus.BAD_REQUEST,
                    "invalid_user": HTTPStatus.BAD_REQUEST,
                    "insufficient_stock": HTTPStatus.CONFLICT,
                    "cdk_used": HTTPStatus.CONFLICT,
                    "cdk_expired": HTTPStatus.CONFLICT,
                    "cdk_disabled": HTTPStatus.CONFLICT,
                }
                self._send_json(status_map.get(reason, HTTPStatus.BAD_REQUEST), {"ok": False, "error": reason})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, **(data or {})})
            return
        if parsed.path == "/web/admin/mailboxes":
            if not self._require_admin_session_user():
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            address = normalize_address(payload.get("address"))
            note = str(payload.get("note") or "").strip()
            raw_tag_ids = payload.get("tag_ids")
            tag_ids = raw_tag_ids if isinstance(raw_tag_ids, list) else []
            created, mailbox, reason = self.app.store.create_mailbox_credential(address, note=note, tag_ids=tag_ids)
            if not created or not mailbox:
                status = HTTPStatus.CONFLICT if reason == "address_exists" else HTTPStatus.BAD_REQUEST
                self._send_json(status, {"ok": False, "error": reason})
                return
            tags = self.app.store._list_tags_for_mailbox_ids([mailbox.mailbox_id]).get(mailbox.mailbox_id, [])
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "mailbox": {
                        "id": mailbox.mailbox_id,
                        "address": mailbox.address,
                        "active": mailbox.active,
                        "note": mailbox.note,
                        "created_at": mailbox.created_at,
                        "updated_at": mailbox.updated_at,
                        "tags": tags,
                    },
                    "access_key": mailbox.access_key,
                    "credential": f"{mailbox.address}----{mailbox.access_key}",
                },
            )
            return
        if parsed.path == "/web/admin/tags":
            if not self._require_admin_session_user():
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            name = str(payload.get("name") or "").strip()
            created, tag, reason = self.app.store.create_mailbox_tag(name)
            if not created or not tag:
                status = HTTPStatus.CONFLICT if reason == "tag_exists" else HTTPStatus.BAD_REQUEST
                self._send_json(status, {"ok": False, "error": reason})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "tag": tag})
            return
        if parsed.path == "/web/admin/cdks":
            session_user = self._require_admin_session_user()
            if not session_user:
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            count = self._parse_int(payload.get("count"), default=1, minimum=1, maximum=1000)
            tag_id = self._parse_int(payload.get("tag_id"), default=0, minimum=0, maximum=10_000_000)
            quantity = self._parse_int(payload.get("quantity"), default=1, minimum=1, maximum=1000)
            max_uses = self._parse_int(payload.get("max_uses"), default=1, minimum=1, maximum=1_000_000)
            batch_label = str(payload.get("batch_label") or "").strip()
            note = str(payload.get("note") or "").strip()
            expires_at = str(payload.get("expires_at") or "").strip()
            result = self.app.store.generate_cdk_codes(
                count,
                tag_id=tag_id,
                quantity=quantity,
                max_uses=max_uses,
                batch_label=batch_label,
                note=note,
                expires_at=expires_at,
                created_by=int(session_user["user_id"]),
            )
            if not result.get("ok"):
                self._send_json(HTTPStatus.CONFLICT, {
                    "ok": False,
                    "error": result.get("error", "insufficient_presale"),
                    "available": result.get("available", 0),
                    "required": result.get("required", 0),
                })
                return
            codes = result["codes"]
            self._send_json(HTTPStatus.OK, {"ok": True, "codes": codes, "created": len(codes)})
            return
        if parsed.path.startswith("/web/admin/cdks/") and parsed.path.endswith("/revoke"):
            session_user = self._require_admin_session_user()
            if not session_user:
                return
            middle = parsed.path[len("/web/admin/cdks/") : -len("/revoke")]
            cdk_id = self._parse_int(middle, default=0, minimum=0, maximum=10_000_000)
            if cdk_id <= 0:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_cdk_id"})
                return
            ok, reason = self.app.store.set_cdk_active(cdk_id, False)
            if not ok:
                status = HTTPStatus.NOT_FOUND if reason == "cdk_not_found" else HTTPStatus.BAD_REQUEST
                self._send_json(status, {"ok": False, "error": reason})
                return
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if parsed.path == "/web/admin/mailboxes/replace":
            if not self._require_admin_session_user():
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            ok, record, reason = self.app.store.replace_sold_mailbox(payload.get("address"))
            if not ok or not record:
                status_map = {
                    "mailbox_not_found": HTTPStatus.NOT_FOUND,
                    "not_sold": HTTPStatus.CONFLICT,
                    "insufficient_stock": HTTPStatus.CONFLICT,
                    "missing_address": HTTPStatus.BAD_REQUEST,
                }
                self._send_json(status_map.get(reason, HTTPStatus.BAD_REQUEST), {"ok": False, "error": reason})
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "address": record.address,
                    "access_key": record.access_key,
                    "credential": f"{record.address}----{record.access_key}",
                },
            )
            return
        if parsed.path == "/web/admin/mailboxes/import-bulk":
            if not self._require_admin_session_user():
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            content = str(payload.get("content") or "")
            note = str(payload.get("note") or "").strip()
            raw_tag_ids = payload.get("tag_ids")
            tag_ids = raw_tag_ids if isinstance(raw_tag_ids, list) else []
            if not content.strip():
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing_content"})
                return
            summary = self.app.store.bulk_create_mailbox_credentials(content, note=note, tag_ids=tag_ids)
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "summary": summary,
                },
            )
            return
        if parsed.path == "/web/admin/mailboxes/import-csv":
            if not self._require_admin_session_user():
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            content = str(payload.get("content") or "")
            note = str(payload.get("note") or "").strip()
            if not content.strip():
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing_content"})
                return
            summary = self.app.store.import_mailbox_credentials_csv(content, note=note)
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "summary": summary,
                },
            )
            return
        if parsed.path.startswith("/web/admin/mailboxes/") and parsed.path.endswith("/note"):
            if not self._require_admin_session_user():
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            mailbox_id = self._extract_admin_mailbox_id(parsed.path)
            note = str(payload.get("note") or "").strip()
            success, mailbox, reason = self.app.store.update_mailbox_note(mailbox_id, note)
            if not success or not mailbox:
                status = HTTPStatus.NOT_FOUND if reason == "mailbox_not_found" else HTTPStatus.BAD_REQUEST
                self._send_json(status, {"ok": False, "error": reason})
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "mailbox": {
                        "id": mailbox.mailbox_id,
                        "address": mailbox.address,
                        "active": mailbox.active,
                        "note": mailbox.note,
                        "created_at": mailbox.created_at,
                        "updated_at": mailbox.updated_at,
                    },
                },
            )
            return
        if parsed.path.startswith("/web/admin/mailboxes/") and parsed.path.endswith("/tags"):
            if not self._require_admin_session_user():
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            mailbox_id = self._extract_admin_mailbox_id(parsed.path)
            raw_tag_ids = payload.get("tag_ids")
            if not isinstance(raw_tag_ids, list):
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_tag_ids"})
                return
            success, mailbox, reason = self.app.store.set_mailbox_tags(mailbox_id, raw_tag_ids)
            if not success or not mailbox:
                status = HTTPStatus.NOT_FOUND if reason in {"mailbox_not_found", "tag_not_found"} else HTTPStatus.BAD_REQUEST
                self._send_json(status, {"ok": False, "error": reason})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "mailbox": mailbox})
            return
        if parsed.path.startswith("/web/admin/mailboxes/") and parsed.path.endswith("/reset-key"):
            if not self._require_admin_session_user():
                return
            mailbox_id = self._extract_admin_mailbox_id(parsed.path)
            success, mailbox, reason = self.app.store.reset_mailbox_access_key(mailbox_id)
            if not success or not mailbox:
                status = HTTPStatus.NOT_FOUND if reason == "mailbox_not_found" else HTTPStatus.BAD_REQUEST
                self._send_json(status, {"ok": False, "error": reason})
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "mailbox": {
                        "id": mailbox.mailbox_id,
                        "address": mailbox.address,
                        "active": mailbox.active,
                        "note": mailbox.note,
                        "created_at": mailbox.created_at,
                        "updated_at": mailbox.updated_at,
                    },
                    "access_key": mailbox.access_key,
                    "credential": f"{mailbox.address}----{mailbox.access_key}",
                },
            )
            return
        if parsed.path.startswith("/web/admin/mailboxes/") and parsed.path.endswith("/toggle-active"):
            if not self._require_admin_session_user():
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            mailbox_id = self._extract_admin_mailbox_id(parsed.path)
            active = bool(payload.get("active"))
            success, mailbox, reason = self.app.store.set_mailbox_active(mailbox_id, active)
            if not success or not mailbox:
                status = HTTPStatus.NOT_FOUND if reason == "mailbox_not_found" else HTTPStatus.BAD_REQUEST
                self._send_json(status, {"ok": False, "error": reason})
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "mailbox": {
                        "id": mailbox.mailbox_id,
                        "address": mailbox.address,
                        "active": mailbox.active,
                        "note": mailbox.note,
                        "created_at": mailbox.created_at,
                        "updated_at": mailbox.updated_at,
                    },
                },
            )
            return
        if parsed.path.startswith("/web/admin/mailboxes/") and parsed.path.endswith("/delete"):
            if not self._require_admin_session_user():
                return
            mailbox_id = self._extract_admin_mailbox_id(parsed.path)
            success, reason = self.app.store.delete_mailbox_credential(mailbox_id)
            if not success:
                status = HTTPStatus.NOT_FOUND if reason == "mailbox_not_found" else HTTPStatus.BAD_REQUEST
                self._send_json(status, {"ok": False, "error": reason})
                return
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if parsed.path.startswith("/web/admin/tags/") and parsed.path.endswith("/delete"):
            if not self._require_admin_session_user():
                return
            parts = [part for part in parsed.path.split("/") if part]
            tag_id = 0
            if len(parts) >= 4:
                try:
                    tag_id = int(parts[3])
                except Exception:
                    tag_id = 0
            deleted, reason = self.app.store.delete_mailbox_tag(tag_id)
            if not deleted:
                status = HTTPStatus.NOT_FOUND if reason == "tag_not_found" else HTTPStatus.BAD_REQUEST
                self._send_json(status, {"ok": False, "error": reason})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "deleted": True, "id": tag_id})
            return
        if parsed.path.startswith("/web/admin/users/") and parsed.path.endswith("/assign-mailbox"):
            if not self._require_admin_session_user():
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            user_id = self._extract_admin_user_id(parsed.path)
            address = normalize_address(payload.get("address"))
            success, reason = self.app.store.assign_mailbox(user_id, address)
            if not success:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": reason})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "user_id": user_id, "address": address})
            return
        if parsed.path.startswith("/web/admin/users/") and parsed.path.endswith("/unassign-mailbox"):
            if not self._require_admin_session_user():
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            user_id = self._extract_admin_user_id(parsed.path)
            address = normalize_address(payload.get("address"))
            success, reason = self.app.store.unassign_mailbox(user_id, address)
            if not success:
                status = HTTPStatus.NOT_FOUND if reason == "not_found" else HTTPStatus.BAD_REQUEST
                self._send_json(status, {"ok": False, "error": reason})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "user_id": user_id, "address": address})
            return
        if parsed.path.startswith("/web/admin/users/") and parsed.path.endswith("/reset-password"):
            if not self._require_admin_session_user():
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            user_id = self._extract_admin_user_id(parsed.path)
            password = str(payload.get("password") or "")
            if len(password) < 6:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "password_too_short"})
                return
            if len(password) > MAX_PASSWORD_LENGTH:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "password_too_long"})
                return
            success, reason = self.app.store.reset_user_password(user_id, build_password_hash(password))
            if not success:
                status = HTTPStatus.NOT_FOUND if reason == "user_not_found" else HTTPStatus.BAD_REQUEST
                self._send_json(status, {"ok": False, "error": reason})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "user_id": user_id})
            return
        if parsed.path == "/admin/new_address":
            if not self._require_admin_auth():
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            domain = self._resolve_new_address_domain(payload)
            if not domain:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing_domain"})
                return
            local_part = self._build_cf_style_local_part()
            address = f"{local_part}@{domain}"
            token = secrets.token_urlsafe(24)
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "email": address,
                    "address": address,
                    "token": token,
                    "jwt": token,
                    "name": local_part,
                    "domain": domain,
                    "created_at": format_beijing_time(utcnow_iso()),
                },
            )
            return
        if parsed.path == "/api/mailboxes/import":
            # 新增邮箱到预售池（presale）。Body: {content, note?, tag_ids?}
            if not self._require_auth(self.app.api_token):
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            content = str(payload.get("content") or "")
            note = str(payload.get("note") or "").strip()
            raw_tag_ids = payload.get("tag_ids")
            tag_ids = raw_tag_ids if isinstance(raw_tag_ids, list) else []
            if not content.strip():
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing_content"})
                return
            summary = self.app.store.bulk_create_mailbox_credentials(content, note=note, tag_ids=tag_ids)
            self._send_json(HTTPStatus.OK, {"ok": True, "summary": summary})
            return
        if parsed.path == "/api/mailboxes/tags":
            # 给邮箱打标签（整体覆盖）。Body: {address|mailbox_id, tag_ids:[...]}
            if not self._require_auth(self.app.api_token):
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            raw_tag_ids = payload.get("tag_ids")
            if not isinstance(raw_tag_ids, list):
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_tag_ids"})
                return
            mailbox_id = self._parse_int(payload.get("mailbox_id"), default=0, minimum=0, maximum=10_000_000)
            if not mailbox_id:
                mailbox_id = self.app.store.get_mailbox_id_by_address(payload.get("address"))
            if not mailbox_id:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "mailbox_not_found"})
                return
            success, mailbox, reason = self.app.store.set_mailbox_tags(mailbox_id, raw_tag_ids)
            if not success or not mailbox:
                status = HTTPStatus.NOT_FOUND if reason in {"mailbox_not_found", "tag_not_found"} else HTTPStatus.BAD_REQUEST
                self._send_json(status, {"ok": False, "error": reason})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "mailbox": mailbox})
            return
        if parsed.path == "/api/mailboxes/delete":
            # 软删除邮箱（status=deleted, active=0）。Body: {address|mailbox_id}
            if not self._require_auth(self.app.api_token):
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            mailbox_id = self._parse_int(payload.get("mailbox_id"), default=0, minimum=0, maximum=10_000_000)
            if not mailbox_id:
                mailbox_id = self.app.store.get_mailbox_id_by_address(payload.get("address"))
            # Idempotent: a missing mailbox is treated as already-gone success.
            if not mailbox_id:
                self._send_json(HTTPStatus.OK, {"ok": True, "mailbox_id": 0, "deleted": False})
                return
            success, reason = self.app.store.delete_mailbox_credential(mailbox_id)
            if not success and reason != "mailbox_not_found":
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": reason})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "mailbox_id": mailbox_id, "deleted": success})
            return
        if parsed.path == "/api/invites/mark":
            if not self._require_auth(self.app.api_token):
                return
            try:
                payload = self._read_json_body()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json:{exc}"})
                return
            try:
                message_id = int(payload.get("id") or 0)
            except Exception:
                message_id = 0
            status = str(payload.get("status") or "").strip().lower()
            note = str(payload.get("note") or "").strip()
            updated, record, reason = self.app.store.mark_invite(message_id, status, note=note)
            if not updated:
                status_code = HTTPStatus.BAD_REQUEST
                if reason == "not_found":
                    status_code = HTTPStatus.NOT_FOUND
                self._send_json(status_code, {"ok": False, "error": reason})
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "id": record.message_id if record else message_id,
                    "status": record.process_status if record else status,
                    "processed_at": record.processed_at if record else format_beijing_time(utcnow_iso()),
                    "note": record.process_note if record else note,
                },
            )
            return
        if parsed.path != "/inbound/email":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        if not self._require_auth(self.app.inbound_token):
            return
        try:
            payload = self._read_inbound_payload()
        except Exception as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_inbound_payload:{exc}"})
            return
        try:
            record = self.app.store.save_message(payload)
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:
            self.app.logger.exception("save inbound email failed")
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": f"store_failed:{exc}"})
            return
        self.app.logger.info(
            "stored inbound email: id=%s address=%s subject=%s mail_type=%s code=%s invite_link=%s process_status=%s",
            record.message_id,
            record.address,
            record.subject[:120],
            record.mail_type or "-",
            record.verification_code or "-",
            record.invite_link or "-",
            record.process_status or "-",
        )
        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "address": record.address,
                "verification_code": record.verification_code,
                "received_at": record.received_at,
            },
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cloudflare Email Worker inbound bridge for gpt-register-oss")
    parser.add_argument("--host", default=os.environ.get("MAIL_BRIDGE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MAIL_BRIDGE_PORT", "8880")))
    parser.add_argument("--db", default=os.environ.get("MAIL_BRIDGE_DB", str(DEFAULT_DB_PATH)))
    parser.add_argument("--log-dir", default=os.environ.get("MAIL_BRIDGE_LOG_DIR", str(DEFAULT_LOG_DIR)))
    parser.add_argument("--config", default=os.environ.get("MAIL_BRIDGE_CONFIG", str(DEFAULT_CONFIG_PATH)))
    parser.add_argument("--api-token", default=os.environ.get("MAIL_BRIDGE_API_TOKEN", ""))
    parser.add_argument("--inbound-token", default=os.environ.get("MAIL_BRIDGE_INBOUND_TOKEN", ""))
    parser.add_argument("--hash-password", default="", help="Generate PBKDF2 hash for a password and exit")
    return parser


def make_server(args: argparse.Namespace) -> ThreadingHTTPServer:
    config_path = Path(args.config).resolve()
    config = load_json_file(config_path)
    mail_conf = config.get("mail") if isinstance(config.get("mail"), dict) else {}
    auth_conf = config.get("auth") if isinstance(config.get("auth"), dict) else {}
    admin_conf = auth_conf.get("admin") if isinstance(auth_conf.get("admin"), dict) else {}
    default_domain = str(mail_conf.get("domain") or "").strip()
    api_token = resolve_shared_token(config_path, args.api_token)
    inbound_token = resolve_shared_token(config_path, args.inbound_token or api_token)
    admin_username = normalize_username(admin_conf.get("username") or DEFAULT_ADMIN_USERNAME)
    admin_password_hash = str(admin_conf.get("password_hash") or "").strip()
    session_secret = str(auth_conf.get("session_secret") or "").strip()
    logger = setup_logger(Path(args.log_dir).resolve())
    store = MailBridgeStore(Path(args.db).resolve(), logger=logger)
    app = MailBridgeApplication(
        store=store,
        logger=logger,
        api_token=api_token,
        inbound_token=inbound_token,
        default_domain=default_domain,
        admin_username=admin_username,
        admin_password_hash=admin_password_hash,
        session_secret=session_secret,
    )
    app.bootstrap_admin_user()
    app.warn_on_weak_config()
    server = ThreadingHTTPServer((args.host, int(args.port)), MailBridgeHandler)
    server.app = app  # type: ignore[attr-defined]
    bound_host, bound_port = server.server_address[:2]
    logger.info("mail bridge listening on http://%s:%s", bound_host, bound_port)
    logger.info("mail bridge db: %s", Path(args.db).resolve())
    return server


def main() -> int:
    args = build_parser().parse_args()
    if str(args.hash_password or "").strip():
        print(build_password_hash(str(args.hash_password)))
        return 0
    server = make_server(args)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.app.store.close()  # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
