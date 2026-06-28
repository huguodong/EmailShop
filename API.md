# gpt邮件服务 API 对接文档

本文档面向需要接入 **gpt邮件服务** 的其他系统，说明如何通过 HTTP API 获取：

- 指定邮箱的最新邮件
- 邮件中的验证码
- 待处理邀请链接
- 某个邮箱的邮件列表

> 说明
>
> - 所有示例响应时间默认使用 **北京时间**，格式通常为 `2026-05-25T20:34:56+08:00`
> - 本服务负责 **收信、存储、提取验证码、识别邀请邮件**
> - 本服务 **不负责** 自动接受工作区邀请；邀请处理由上游系统完成

---

## 1. 基础信息

### 1.1 Base URL

按你的部署地址替换：

```text
http://127.0.0.1:8880
```

例如：

```text
http://your-host:8880
```

### 1.2 鉴权方式

开放查询接口使用 Bearer Token：

```http
Authorization: Bearer <mail-api-token>
```

### 1.3 通用返回格式

成功一般返回：

```json
{
  "ok": true
}
```

失败一般返回：

```json
{
  "ok": false,
  "error": "error_code"
}
```

常见错误码：

- `unauthorized`：鉴权失败
- `missing_address`：缺少邮箱地址参数
- `invalid_json:...`：请求体不是合法 JSON
- `not_found`：资源不存在
- `not_invite`：目标邮件不是邀请邮件
- `invalid_status`：邀请标记状态非法

---

## 2. 获取指定邮箱最新邮件

用于其他系统轮询某个邮箱的最新邮件，并从中读取验证码或邀请链接。

### 接口

```http
GET /api/latest?address=<email>
Authorization: Bearer <mail-api-token>
```

### Query 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `address` | 是 | 目标邮箱地址 |

### 示例请求

```bash
curl "http://127.0.0.1:8880/api/latest?address=alice+001@example.com" ^
  -H "Authorization: Bearer YOUR_MAIL_TOKEN"
```

### 成功响应

```json
{
  "ok": true,
  "email": {
    "id": 123,
    "to": "alice+001@example.com",
    "from": "noreply@openai.com",
    "subject": "Your ChatGPT code is 654321",
    "text": "Use 654321 to continue.",
    "html": "<html>...</html>",
    "body": "Use 654321 to continue.",
    "received_at": "2026-05-25T20:34:56+08:00",
    "created_at": "2026-05-25T20:34:56+08:00",
    "verification_code": "654321",
    "mail_type": "verification_code",
    "invite_link": "",
    "process_status": "pending"
  }
}
```

### 无邮件时响应

```json
{
  "ok": true,
  "email": null
}
```

### 字段说明

| 字段 | 说明 |
|---|---|
| `id` | 邮件主键 ID |
| `to` | 收件邮箱 |
| `from` | 发件人 |
| `subject` | 邮件主题 |
| `text` | 文本正文 |
| `html` | HTML 正文 |
| `body` | 原始正文摘要/正文 |
| `received_at` | 收件时间，北京时间 |
| `created_at` | 当前实现与 `received_at` 一致 |
| `verification_code` | 识别出的验证码，没有则为空字符串 |
| `mail_type` | 邮件类型：`verification_code` / `team_invite` / `unknown` |
| `invite_link` | 提取出的邀请链接，没有则为空字符串 |
| `process_status` | 处理状态，邀请邮件通常为 `pending` 或 `accepted` |

### 获取验证码的推荐读取方式

业务系统通常只需要判断：

1. `ok == true`
2. `email != null`
3. `email.verification_code` 非空

例如：

```json
{
  "verification_code": "654321"
}
```

---

## 3. 获取待处理邀请邮件

用于自动化系统拉取下一封待处理邀请邮件。  
该接口是 **单条返回、按最旧优先**。

### 接口

```http
GET /api/invites/next
Authorization: Bearer <mail-api-token>
```

或指定邮箱：

```http
GET /api/invites/next?address=<email>
Authorization: Bearer <mail-api-token>
```

### Query 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `address` | 否 | 指定邮箱时，仅返回该邮箱最早的一封待处理邀请邮件 |

### 规则

- 如果带 `address`，返回该邮箱 `process_status = pending` 的最旧邀请邮件
- 如果不带 `address`，返回全局最旧的一封待处理邀请邮件
- 一次只返回一条

### 示例请求

```bash
curl "http://127.0.0.1:8880/api/invites/next" ^
  -H "Authorization: Bearer YOUR_MAIL_TOKEN"
```

```bash
curl "http://127.0.0.1:8880/api/invites/next?address=alice+001@example.com" ^
  -H "Authorization: Bearer YOUR_MAIL_TOKEN"
```

### 成功响应

```json
{
  "ok": true,
  "invite": {
    "id": 12,
    "to": "alice+001@example.com",
    "from": "team@openai.com",
    "subject": "Dennis Hill invited you to ChatGPT Business",
    "text": "Dennis Hill invited you to join workspace egg.",
    "html": "<html>...</html>",
    "body": "Dennis Hill invited you to join workspace egg.",
    "received_at": "2026-05-25T20:35:00+08:00",
    "mail_type": "team_invite",
    "invite_link": "https://chatgpt.com/invite/workspace/abc123",
    "process_status": "pending"
  }
}
```

### 没有待处理邀请时响应

```json
{
  "ok": true,
  "invite": null
}
```

### 推荐读取字段

自动化系统通常重点读取：

- `invite.id`
- `invite.to`
- `invite.invite_link`
- `invite.received_at`

---

## 4. 标记邀请已处理

上游系统成功处理邀请链接后，应显式回调本接口，将邀请标记为 `accepted`。

### 接口

```http
POST /api/invites/mark
Authorization: Bearer <mail-api-token>
Content-Type: application/json
```

### 请求体

```json
{
  "id": 12,
  "status": "accepted",
  "note": "joined upstream"
}
```

### 字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 邀请邮件 ID |
| `status` | 是 | 当前仅支持 `accepted` |
| `note` | 否 | 处理备注 |

### 示例请求

```bash
curl "http://127.0.0.1:8880/api/invites/mark" ^
  -H "Authorization: Bearer YOUR_MAIL_TOKEN" ^
  -H "Content-Type: application/json" ^
  -d "{\"id\":12,\"status\":\"accepted\",\"note\":\"joined upstream\"}"
```

### 成功响应

```json
{
  "ok": true,
  "id": 12,
  "status": "accepted",
  "processed_at": "2026-05-25T20:36:00+08:00",
  "note": "joined upstream"
}
```

### 说明

- 如果上游处理失败，**不要调用 accepted**
- 未成功处理的邀请保持 `pending`，便于后续继续拉取

---

## 5. 获取某个邮箱的邮件列表

用于管理系统或排查工具查看某个邮箱最近多封邮件。

### 接口

```http
GET /admin/mails?address=<email>&limit=<n>&offset=<n>
Authorization: Bearer <mail-api-token>
```

> 说明：这是管理向接口，但同样使用 `mail-api-token` 鉴权。

### Query 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `address` | 是 | 目标邮箱地址 |
| `limit` | 否 | 返回条数，默认 `5`，最大 `200` |
| `offset` | 否 | 偏移量，默认 `0` |

### 示例请求

```bash
curl "http://127.0.0.1:8880/admin/mails?address=alice+001@example.com&limit=10&offset=0" ^
  -H "Authorization: Bearer YOUR_MAIL_TOKEN"
```

### 成功响应

```json
{
  "ok": true,
  "results": [
    {
      "id": 123,
      "address": "alice+001@example.com",
      "from": "noreply@openai.com",
      "subject": "Your ChatGPT code is 654321",
      "received_at": "2026-05-25T20:34:56+08:00",
      "raw": "Subject: Your ChatGPT code is 654321\n\nUse 654321 to continue.",
      "raw_header_text": "From: noreply@openai.com\nTo: alice+001@example.com\nSubject: Your ChatGPT code is 654321",
      "mail_type": "verification_code",
      "invite_link": "",
      "process_status": "pending",
      "processed_at": "",
      "process_note": ""
    }
  ],
  "limit": 10,
  "offset": 0
}
```

### 适用场景

- 查看某个邮箱最近多封邮件
- 调试验证码提取问题
- 调试邀请链接提取问题
- 查看原始邮件文本

---

## 6. 上游收信写入接口

本接口通常供邮件转发器、Worker、Webhook 或上游邮件桥接程序调用。  
普通业务系统如果只是“查验证码/查邀请”，通常**不需要**调用它。

### 接口

```http
POST /inbound/email
Authorization: Bearer <mail-inbound-token>
Content-Type: application/json
```

也支持：

```http
Content-Type: message/rfc822
```

### JSON 模式示例

```json
{
  "to": "alice+001@example.com",
  "from": "noreply@openai.com",
  "subject": "Your ChatGPT code is 654321",
  "text": "Use 654321 to continue.",
  "html": "<html>...</html>",
  "body": "Use 654321 to continue.",
  "received_at": "2026-05-25T12:34:56Z"
}
```

### 成功响应

```json
{
  "ok": true,
  "address": "alice+001@example.com",
  "verification_code": "654321",
  "received_at": "2026-05-25T20:34:56+08:00"
}
```

### 说明

- 服务会在入库时自动尝试：
  - 提取验证码
  - 识别邀请邮件
  - 提取邀请链接
- 当前支持的邮件类型：
  - `verification_code`
  - `team_invite`
  - `unknown`

---

## 7. 健康检查

### 接口

```http
GET /health
```

### 响应示例

```json
{
  "ok": true,
  "now": "2026-05-25T12:00:00Z"
}
```

> 说明：`/health` 的 `now` 主要用于服务存活检查，不建议作为业务时间字段依赖。

---

## 8. 邮箱售卖平台（CDK 卡密兑换）

> 把系统当作邮箱售卖平台使用：管理员导入邮箱作为库存（可按标签分品类），生成 CDK 卡密在外部售卖；
> 买家登录后用卡密兑换，自动从“未售卖”库存里发货并归属到买家账号，买家可在「用户中心」`/web/user` 查看已购邮箱与邮件。
> 以下接口均基于**会话 Cookie**鉴权（`mail_bridge_session`），不是 Bearer Token。

### 8.1 库存模型（双号池）

`mailbox_credentials.status` 取值：

- `presale` **预售池**：新导入 / 新建邮箱默认进此池，**不可被兑换**。
- `available` **可兑换池**：只有此状态能被 CDK 兑换。
- `sold` 已售：兑换后写入归属（`owner_user_id`、`sold_at`、`order_id`）。
- `deleted` 已删除：软删除，默认不出现在邮箱列表，可按状态筛选查看；其标签、归属、邮件均保留。

流转规则：

1. 导入 / 新建 → `presale`（所有创建入口默认）。
2. **创建 CDK 时**按品类（tag）校验预售库存是否够：够则把 `count × quantity × max_uses` 个邮箱从 `presale` 移到 `available` 并生成卡密；不够则拒绝创建（不生成任何卡密）。
3. 买家兑换：`available` → `sold`。
4. 删除：任意状态 → `deleted`（软删除，不物理清除）。

### 8.2 管理员接口（需管理员会话）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/web/admin/cdks` | 批量生成卡密。Body：`{count, tag_id, quantity, max_uses, batch_label, note, expires_at}`；`tag_id=0` 表示任意品类；**会先校验预售池库存**，够则生成并把库存从预售池移入可兑换池，返回 `codes`；不够返回 409（见下）。 |
| GET | `/web/admin/cdks` | 卡密列表，Query：`keyword,status(active/used/expired/disabled),tag_id,limit,offset`。 |
| POST | `/web/admin/cdks/{id}/revoke` | 撤销（停用）某卡密。 |
| GET | `/web/admin/cdks/export.txt` | 按当前筛选导出卡密（纯文本，每行一个）。 |
| GET | `/web/admin/stock` | 库存概览，按标签及总计统计 `available`/`presale`/`sold`。 |
| GET | `/web/admin/mailboxes` | 邮箱列表，Query：`keyword,tag_id,status,limit,offset`。`status` 取 `presale/available/sold/deleted`；**留空时默认隐藏 `deleted`**。 |
| POST | `/web/admin/mailboxes`、`/import-bulk`、`/import-csv` | 创建 / 批量导入邮箱，新建的默认进 **预售池**（`presale`）。 |
| POST | `/web/admin/mailboxes/{id}/delete` | **软删除**邮箱：置 `status=deleted, active=0`，不物理清除。返回 `{ok:true}`。 |

生成卡密示例：

```json
POST /web/admin/cdks
{ "count": 100, "tag_id": 1, "quantity": 1, "batch_label": "闲鱼-0627" }
```

预售库存不足时（不会生成任何卡密）：

```json
HTTP 409
{ "ok": false, "error": "insufficient_presale", "available": 30, "required": 100 }
```

### 8.3 买家接口（需用户会话）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/web/auth/register` | 注册买家账号 `{username, password}`。 |
| POST | `/web/auth/login` | 登录（非管理员自动进入 `/web/user`）。 |
| POST | `/web/user/redeem` | 兑换卡密 `{code}`，成功返回发放的 `mailboxes`（含 `address`、`access_key`、`credential`）。**登录可选**：带会话则归属到账号；匿名（散户）也可直接兑换，仅返回凭据由前端本地保存。 |
| GET | `/web/me/mailboxes` | 我的已购邮箱（含密钥与标签）。 |
| GET | `/web/me/latest?address=` | 读取自己已购邮箱的最新邮件；非本人邮箱返回 403。 |
| GET | `/web/me/redemptions` | 我的兑换历史。 |

兑换示例与返回：

```json
POST /web/user/redeem
{ "code": "CDK-WFPWT-L3JPJ-YD95Y-WE5P9" }
```

```json
{
  "ok": true,
  "redemption_id": 1,
  "quantity": 1,
  "mailboxes": [
    { "id": 1, "address": "a@x.com", "access_key": "SFGzP0W5lNqG", "credential": "a@x.com----SFGzP0W5lNqG" }
  ]
}
```

兑换失败的 `error` 取值：`cdk_not_found`(404)、`cdk_used`/`cdk_expired`/`cdk_disabled`/`insufficient_stock`(409)。

> 买家拿到 `邮箱----密钥` 后，也可继续用公共取件页 `/web/query` 或 `POST /web/query-mails` 查看邮件，行为不变。

> **公共落地页 `/web/query`（根 `/` 自动 302 跳转至此）** 现为「兑换 CDK + 邮件查询」二合一页：顶部 Tab 切换，默认停在兑换 Tab。散户免登录输入卡密兑换，发放的邮箱凭据存浏览器 `localStorage`（key `mb_mailboxes`，明文，仅本机可见），「我的邮箱」列表一键查件。

---

## 9. 常见对接方案

### 方案 A：注册机 / OTP 轮询器获取验证码

只接：

- `GET /api/latest?address=<email>`

读取：

- `email.verification_code`

建议逻辑：

1. 周期性轮询指定邮箱
2. `ok == true`
3. `email != null`
4. `verification_code` 非空时即取走使用

### 方案 B：邀请自动化系统处理邀请链接

只接：

- `GET /api/invites/next[?address=<email>]`
- `POST /api/invites/mark`

建议逻辑：

1. 轮询 `GET /api/invites/next`
2. 拿到 `invite_link` 后由上游完成加入动作
3. 成功后回调 `POST /api/invites/mark`

### 方案 C：管理后台 / 排障工具查看邮件历史

只接：

- `GET /admin/mails?address=<email>&limit=<n>&offset=<n>`

---

## 9. 对接注意事项

### 9.1 邮箱地址大小写

服务内部按小写存储邮箱地址，调用时建议统一传小写。

### 9.2 `+` 别名邮箱

像下面这样的邮箱是支持的：

```text
alice+001@example.com
```

调用时建议：

- 在 QueryString 中正常传递
- 如果调用方框架会把 `+` 当空格，务必 URL 编码为 `%2B`

例如：

```text
/api/latest?address=alice%2B001@example.com
```

### 9.3 时间字段

邮件相关对外时间字段默认按 **北京时间** 返回，例如：

```text
2026-05-25T20:34:56+08:00
```

### 9.4 未知邮件类型

如果服务没有识别出验证码或邀请邮件，则：

```json
{
  "mail_type": "unknown"
}
```

---

## 10. 快速示例

### 获取验证码

```bash
curl "http://127.0.0.1:8880/api/latest?address=alice%2B001@example.com" ^
  -H "Authorization: Bearer YOUR_MAIL_TOKEN"
```

### 获取待处理邀请

```bash
curl "http://127.0.0.1:8880/api/invites/next?address=alice%2B001@example.com" ^
  -H "Authorization: Bearer YOUR_MAIL_TOKEN"
```

### 标记邀请已处理

```bash
curl "http://127.0.0.1:8880/api/invites/mark" ^
  -H "Authorization: Bearer YOUR_MAIL_TOKEN" ^
  -H "Content-Type: application/json" ^
  -d "{\"id\":12,\"status\":\"accepted\",\"note\":\"joined upstream\"}"
```

### 查看某个邮箱最近邮件

```bash
curl "http://127.0.0.1:8880/admin/mails?address=alice%2B001@example.com&limit=10&offset=0" ^
  -H "Authorization: Bearer YOUR_MAIL_TOKEN"
```

---

## 11. 当前开放接口清单

- `GET /health`
- `POST /inbound/email`
- `GET /api/latest?address=<email>`
- `GET /admin/mails?address=<email>&limit=<n>&offset=<n>`
- `GET /api/invites/next[?address=<email>]`
- `POST /api/invites/mark`

如果后续你需要 Swagger / OpenAPI 版本，我可以在此基础上继续补一份：

- `openapi.yaml`
- 或 Postman / Apifox 可导入格式
