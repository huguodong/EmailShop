# GOAL — 邮箱售卖平台持续优化（goal 模式）

## 北极星
把这个单文件邮箱售卖平台打磨成「**能放心收钱、卖家好运营、买家好上手**」的生产级服务。
在**不破坏现有功能**的前提下，逐项加固、补功能、提体验，**每一步都用回归测试自证**。

## 铁律（GUARDRAILS — 永不违反）
1. **测试为护栏**：每次改动后必须跑 `python -m unittest tests.test_mail_bridge_server`（在项目根目录）。
   - 全绿才算这一步完成，红了就**回退本步改动**，不带着失败往前走。
   - 钱 / 库存 / 卡密 / 兑换 任意路径的改动，**必须先加对应测试**再改实现。
2. **绝不碰生产数据**：不修改 `config.json`、`mail_bridge.sqlite3`(及 -wal/-shm)；测试只用临时 DB（现有 setUp 已是临时目录）。不改任何真实密钥。
3. **单文件、无新依赖**：业务代码仍在 `mail_bridge_server.py`，纯标准库；不 pip 安装任何东西。
4. **ponytail 风格**：最小 diff，优先复用现有函数/CSS/JS helper；有意简化加 `ponytail:` 注释并写明上限。
5. **每完成一个 backlog 项**：在本文件末尾「进度日志」追加一行（做了什么 + 测试结果 + 新增测试数）。

## 关键事实
- 全局单锁 `self._lock` 非可重入：原子操作内联 SQL，**禁止**在锁内调用其它加锁方法（会死锁）。
- 「可发货」权威定义：`status='available' AND active=1`。
- 收信是 webhook（`POST /inbound/email`，需 Bearer token），不是 IMAP。
- 鉴权：Web=session cookie；`/api/*`=Bearer。匿名兑换 user_id=0（不绑账号）。
- 测试 harness：`tests/test_mail_bridge_server.py`，用 `make_server` 在线程内起服务（端口 0），helper 齐全（`_web_login`/`_request`/`_admin_cookie`/`_gen_cdk`/`_redeem`/`_stock` 等）。

## BACKLOG（按价值排序，从上往下做）

### A. 安全加固
- [x] A1 登录限流：同一来源连续失败 N 次后短暂限速（内存计数+时间窗，ponytail 简单实现）。
- [x] A2 兑换限流：匿名兑换按 IP 限速，防卡密爆破（虽码长，但加一层）。
- [x] A3 弱配置启动告警：`session_secret` 仍是 `CHANGE_ME...` 占位符时，启动日志 WARN。
- [x] A4 输入校验审计：兑换/注册/导入的边界（超长、空、非法字符）返回明确 4xx 不 500。
- [x] A5 错误处理：`do_POST`/`do_GET` 未捕获异常统一 500 JSON，不泄露堆栈。

### B. 卖家运营
- [x] B1 销售统计接口+面板：今日售出 / 累计售出 / 当前可售（按标签），admin 仪表盘展示。
- [x] B2 低库存预警：某标签可售 < 阈值时，库存概览 chip 变红（前端阈值 5，售罄灰/低库存红）。
- [x] B3 买家自助重置密钥：登录买家对**自己**的邮箱重置 access_key（校验 owner_user_id）。
- [x] B4 换货/标记失效：admin 把某已售坏邮箱标记 dead，并从同标签补发一个给原买家（含账号归属迁移）。
- [x] B5 CDK 列表搜索/筛选增强（关键词=码/批次/备注，状态，新增标签筛选下拉，导出同步筛选）。

### C. 买家体验
- [x] C1 验证码邮件自动刷新：查件页轮询最新邮件（5 秒可开关、tab 隐藏暂停、出错自停），收到新邮件提示。
- [x] C2 移动端适配复查：买家页（公共查件/兑换 + 用户中心）已用 `@media`/flex-wrap 响应式；管理后台为桌面工具，保持现状。
- [x] C3 文案/错误提示打磨：登录/注册/兑换/换货/重置全部失败路径映射中文；补 429/413 友好文案。

### D. 测试 + 运维
- [x] D1 扩充回归测试：每项落地都补测试，回归套件从 32 → 60（+28），覆盖安全/库存/兑换/换货/统计/限流/异常等。
- [x] D2 Docker 健康检查：compose + Dockerfile 加 `healthcheck`（stdlib 打 `/health`，slim 无 curl）。
- [x] D3 容器非 root 运行：Dockerfile 加 uid 10001 appuser + chown /data /app + USER。
- [x] D4 备份脚本：`backup.py`，stdlib sqlite3 在线备份 API（WAL 安全、运行中可备），含 config，时间戳目录，保留最近 N 份。

> 完成一项就勾选 [x]。顺序可在同一字母段内微调，但 A 段（安全）整体优先。

## 工作协议（每个 backlog 项）
1. 在 `tests/test_mail_bridge_server.py` 加该项的测试（先红）。
2. 在 `mail_bridge_server.py` 实现，最小 diff。
3. 跑全量测试；绿→勾选 + 写进度日志；红→修或回退。
4. 取下一项，循环。

## 进度日志
- 2026-06-28 C1+C2+C3：买家体验。① 公共查件页加「自动刷新」开关（5 秒轮询、`document.hidden` 暂停、出错自停、新邮件顶部 id 变化时提示「收到新邮件」）。② 复查买家页响应式（已有 @media/flex-wrap，达标）。③ 文案打磨：新增 `authErrorText` 把登录/注册错误码映射中文，兑换错误图加 429/413 友好文案。新增 1 marker 测试（auto-refresh 控件存在）。全量 60 通过。**A–D 全部 backlog 完成。**
- 2026-06-28 D2+D3+D4：运维加固。Dockerfile 加 HEALTHCHECK（stdlib urllib 打 /health）+ 非 root（uid 10001 appuser，chown /data /app）；docker-compose 加 healthcheck。新增 `backup.py`（stdlib sqlite3 在线备份 API，WAL 安全、运行中可备份，含 config，时间戳目录，--keep 保留最近 N 份）。已对线上库实测：备份 integrity_check=ok、十张表齐全、config 已复制（非破坏性）。Docker 改动无法在本机验证（无 docker），但 /health 已被测试覆盖返 200。
- 2026-06-28 B5：CDK 列表筛选增强。后端 `list_cdks` 已支持 keyword(码/批次/备注)+status+tag_id；前端补「全部分类」标签筛选下拉（`renderCdkTagOptions` 同时填充生成与筛选两个 select），`refreshCdks`/导出 TXT 带上 tag_id，加 onchange。新增 2 测试（按标签筛选 total/项正确、按批次关键词筛选）。修复测试 harness 偶发 ResourceWarning（HTTPError 显式 close）。全量 60 通过（3×稳定）。
- 2026-06-28 B4：换货/标记失效。新增 `store.replace_sold_mailbox(address)`（单锁原子：校验 status='sold'→标记旧为 dead/active=0/owner清0→同标签选可售补发→转移 owner_user_id/order_id 与 user_mailboxes 链接→全有或全无）+ `POST /web/admin/mailboxes/replace`（admin，reason→404/409/400）。admin 卡密视图加「换货/标记失效」面板。新增 3 测试（账号买家换货成功+旧失效+归属迁移+库存正确、未售→409 not_sold、无库存→409 insufficient_stock）。全量 58 通过。
- 2026-06-28 B3：买家自助重置密钥。新增 `store.reset_user_mailbox_access_key(user_id, address)`（单锁、authoritative on owner_user_id，非本人→not_owned）+ `POST /web/me/mailboxes/reset-key`（登录必需，reason→403/404/400）。`/web/user` 仪表盘每个邮箱加「重置密钥」按钮（confirm 后调用并刷新）。新增 3 测试（本人重置成功且旧密钥失效新密钥可用、非本人 403、未登录 401）。全量 55 通过。
- 2026-06-28 B1+B2：销售统计 + 低库存预警。新增 `store.sales_stats()`（累计可售/已售/总数 + 今日售出 + 今日兑换，按北京日界，UTC ISO 微秒边界做词法比较）+ `GET /web/admin/stats`。admin 卡密视图加「统计条」(今日售出/今日兑换/累计已售/当前可售/总数)，进入视图与刷新/生成时刷新。库存 chip 三态：售罄灰、可售<5 红(`.status-chip.warn`)、充足常态。新增 1 测试（统计数值正确）。全量 52 通过。
- 2026-06-28 A4+A5：① 全局错误护栏——`do_GET`/`do_POST` 改为薄壳调 `_dispatch_safely`，未捕获异常统一记日志并返 500 JSON `internal_error`，不泄露堆栈/不断连。② 请求体上限 10MiB（`_body_too_large` 在 POST 单一入口拦截，413 `payload_too_large` 并关连接防 keep-alive 错位）；注册用户名≤64、密码≤256（防 pbkdf2 DoS），change-password/reset 同加上限。新增 5 测试（GET/POST 异常→500、超大 body→413、超长密码/用户名→400）。全量 51 通过。A 安全段全部完成。
- 2026-06-28 A3：弱配置启动告警。`MailBridgeApplication.warn_on_weak_config()`——session_secret 为空或以 `CHANGE_ME` 开头时 WARN，`make_server` 启动时调用（线上配置仍是占位符，会按预期告警，提醒改强随机串）。新增 1 测试（弱密钥告警/强密钥不告警）。全量 46 通过。
- 2026-06-28 A2：兑换限流。复用 `RateLimiter`（同 IP 20 次无效码/5 分钟→429），仅 `cdk_not_found` 计数，成功/库存不足不计，合法买家连续兑换不受影响。新增 2 测试（连刷无效码触发 429、连兑 8 个有效码全 200）。全量 45 通过。
- 2026-06-28 A1：登录限流。`RateLimiter`（内存滑窗，per-process）挂到 app；`/web/auth/login` 在校验前先查节流（同 IP 5 次失败/5 分钟→429 `too_many_attempts`，失败计数、成功重置，支持 X-Forwarded-For）。新增 2 测试（连续失败触发 429+锁定期内正确密码也拒、成功登录重置计数器）。全量 43 通过。
- 2026-06-28：建立回归护栏。新增 9 个 CDK 平台测试（匿名兑换/库存增减/重复消费/库存不足/撤销/账号绑定/匿名不绑账号/匿名取件/并发单库存唯一胜出/公共页标记），修掉 1 个过期断言（admin inbox 已并入 SPA）。全量 41 测试通过。
