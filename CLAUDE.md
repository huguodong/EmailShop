# 邮箱售卖平台 (mail_bridge_server)

## 是什么

单文件 Python 邮箱服务,已改造为 **CDK 卡密兑换的邮箱售卖平台**。
管理员导入邮箱作为库存 → 生成 CDK 卡密(外部售卖)→ 买家登录后用 CDK 兑换,
自动发放未售卖邮箱 → 买家在用户中心管理已购邮箱、查看邮件。

## 技术栈(关键:无第三方依赖)

- **纯 Python 标准库**:`http.server`(ThreadingHTTPServer)+ `sqlite3`(WAL 模式)。无 pip 依赖,无框架。
- 全部代码在一个文件 `mail_bridge_server.py`(~6300 行),HTML/CSS/JS 以三引号字符串内嵌。
- 收信靠 **webhook**(`POST /inbound/email` 落 `messages` 表),不是 IMAP/POP。
- 鉴权:Web 用 session cookie(`mail_bridge_session`);`/api/*` 用 Bearer token。

## 文件结构

```
gpt邮件服务/
├── mail_bridge_server.py        # 整个应用(唯一源码)
├── config.json                  # 密钥/管理员口令哈希(含真实凭据,只读勿改)
├── mail_bridge.sqlite3          # SQLite 库(+ -wal/-shm)
├── API.md                       # 接口文档(第 8 节为 CDK 售卖平台)
├── Dockerfile / docker-compose.yml / .dockerignore
├── data/                        # Docker 数据卷(config + db + logs 持久化)
├── start_mail_bridge_8880.sh/.bat   # 本地启动
└── stop_mail_bridge_8880.sh/.bat    # 本地停止(改库前先停服,有 WAL)
```

## 启动方式

- **Docker(推荐)**:`docker compose up -d --build`,数据在 `./data/`,首次自动迁移。
- **本地**:`bash start_mail_bridge_8880.sh`(默认 127.0.0.1:8880)。
- **配置全走环境变量**:`MAIL_BRIDGE_HOST/PORT/DB/CONFIG/LOG_DIR/API_TOKEN/INBOUND_TOKEN`。
- 生成口令哈希:`python mail_bridge_server.py --hash-password <pwd>`。

## 数据模型

- `mailbox_credentials` — 邮箱库存(地址 + 访问密钥);含 `status`(available/sold)、
  `owner_user_id`、`sold_at`、`order_id`。**可发货 = `status='available' AND active=1`**。
- `cdks` — 卡密(绑定 `tag_id` 品类、每码发 `quantity` 个、`max_uses` 默认 1、`expires_at`、批次)。
- `cdk_redemptions` — 兑换/订单流水。
- `users`/`sessions`/`user_mailboxes` — 用户账号、会话、归属(兑换时双轨写入归属)。
- `mailbox_tags`/`mailbox_tag_links` — 标签分品类。
- `messages` — 收到的邮件。
- 迁移机制:`_init_db` 用 `PRAGMA table_info` + 条件 `ALTER TABLE` 加列,新表 `CREATE TABLE IF NOT EXISTS`,**幂等**。

## ⚠️ 核心约束(改代码必读)

- **单一非可重入全局锁** `self._lock` 串行所有写操作。原子操作(如 `redeem_cdk`)必须
  **内联 SQL**,**禁止**调用本身会再次加锁的方法(`assign_mailbox`/`reset_mailbox_access_key` 等)→ 否则死锁。
- 兑换防重复售卖/重复消费靠双守卫:`UPDATE ... WHERE status='available'` + `WHERE used_count < max_uses`。全有或全无发货。
- `config.json` 含真实密钥,**只读不改**;`session_secret` 上线前务必改掉占位符。
- 改库前先停服(WAL 文件)。

## 关键接口

- 管理员:`POST /web/admin/cdks`(生成)、`GET /web/admin/cdks`、`/web/admin/cdks/{id}/revoke`、
  `/web/admin/stock`(库存)、`/web/admin/cdks/export.txt`、`/web/admin/mailboxes/import-bulk`(批量导入,支持 `tag_ids`)。
- 买家:`POST /web/user/redeem`(兑换)、`GET /web/me/mailboxes`、`/web/me/latest?address=`、`/web/me/redemptions`。
- 公共取件:`POST /web/query-mails`(凭 `地址----密钥`)。
- 页面:`/web/admin`(后台)、`/web/user`(买家门户,未登录显示登录/注册)。

## 哲学

简单实用胜过复杂优雅。最小新增、最大复用、无新依赖。
deliberate 简化用 `ponytail:` 注释标注上限与升级路径。

---
_最后更新: 2026-06-28_
