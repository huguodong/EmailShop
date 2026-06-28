# gpt邮件服务 Guidelines

## Scope
- This file applies to `gpt邮件服务/` and everything under it.
- Parent repo rules still apply unless this file adds tighter rules for the mail bridge service.

## Service Purpose
- `mail_bridge_server.py` is a mail ingestion and query service backed by SQLite.
- The service accepts forwarded emails, stores the original content, extracts verification codes when present, and classifies invite emails.
- This service does not accept workspace invitations itself. Upstream callers fetch invite links and perform the acceptance flow outside this service.

## API Contract
- Keep these existing endpoints stable unless the task explicitly requires a breaking change:
  - `POST /inbound/email`
  - `GET /api/latest?address=<email>`
  - `GET /admin/mails?address=<email>&limit=<n>&offset=<n>`
  - `GET /api/invites/next[?address=<email>]`
  - `POST /api/invites/mark`
- `GET /api/invites/next` is single-item and oldest-first:
  - If `address` is provided, return the oldest `team_invite` row for that address where `process_status = pending`
  - If `address` is omitted, return the oldest `team_invite` row across all addresses where `process_status = pending`
  - Do not return multiple invite rows from this endpoint
- `POST /api/invites/mark` is an explicit upstream callback:
  - Upstream marks successful handling with `{ "id": <message_id>, "status": "accepted" }`
  - Failed upstream handling should leave the row unchanged so it remains `pending`

## Invite API Examples
- Fetch the next pending invite globally when the caller does not know which mailbox was invited:

```http
GET /api/invites/next
Authorization: Bearer <mail-api-token>
```

- Fetch the next pending invite for one mailbox:

```http
GET /api/invites/next?address=daizenan0+003@gmail.com
Authorization: Bearer <mail-api-token>
```

- Example success response:

```json
{
  "ok": true,
  "invite": {
    "id": 12,
    "to": "daizenan0+003@gmail.com",
    "from": "team@openai.com",
    "subject": "Dennis Hill invited you to ChatGPT Business",
    "text": "Dennis Hill 已邀请你在工作空间 egg 中使用 ChatGPT Business 参与协作。",
    "html": "<html>...</html>",
    "body": "Dennis Hill 已邀请你在工作空间 egg 中使用 ChatGPT Business 参与协作。",
    "received_at": "2026-04-24T15:00:00Z",
    "mail_type": "team_invite",
    "invite_link": "https://chatgpt.com/invite/workspace/abc123",
    "process_status": "pending"
  }
}
```

- Example empty response when no pending invite exists:

```json
{
  "ok": true,
  "invite": null
}
```

- Mark an invite as accepted after upstream handling succeeds:

```http
POST /api/invites/mark
Authorization: Bearer <mail-api-token>
Content-Type: application/json
```

```json
{
  "id": 12,
  "status": "accepted",
  "note": "joined upstream"
}
```

- Example mark response:

```json
{
  "ok": true,
  "id": 12,
  "status": "accepted",
  "processed_at": "2026-04-24T15:01:00Z",
  "note": "joined upstream"
}
```

## Storage Rules
- SQLite schema changes must be backward-compatible with existing `mail_bridge.sqlite3` files.
- Prefer additive migrations on startup such as `ALTER TABLE ... ADD COLUMN` checks over destructive rebuilds.
- Do not remove or rename existing columns without an explicit migration task.
- Current mail classification semantics should remain stable:
  - `verification_code`
  - `team_invite`
  - `unknown`

## Invite Mail Handling
- Treat invite detection as heuristic and template-based, not provider-generic.
- Prefer extracting invite links from HTML anchor tags first, then fall back to plain-text/body URLs.
- Preserve the raw email content even when classification or extraction fails.
- If an email is recognized as `team_invite` but no link is extracted:
  - Keep `mail_type = team_invite`
  - Keep `process_status = pending`
  - Do not silently downgrade it to `unknown`

## Implementation Preferences
- Keep this service self-contained and lightweight.
- Prefer standard library solutions before adding new dependencies.
- Preserve current auth behavior unless the task explicitly asks for auth changes.
- Be careful with email address normalization:
  - Stored addresses should remain lowercase
  - Query parameters may contain `+` aliases and must continue to work

## Testing
- Any change to mail ingestion, auth, invite detection, invite ordering, or callback behavior must update `tests/test_mail_bridge_server.py`.
- Prefer deterministic tests with temp SQLite files and local in-process HTTP servers.
- At minimum, keep these behaviors covered:
  - verification-code ingestion and lookup
  - invite classification and link extraction
  - oldest pending invite selection
  - accepted invite not being returned again
