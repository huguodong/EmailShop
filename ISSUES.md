# 邮箱售卖平台 优化问题列表

## 问题列表

| # | 严重度 | 描述 | 状态 |
|---|--------|------|------|
| 1 | 🔴高 | 查询页兑换CDK按钮无 loading/禁用保护，连续点击触发重复请求 | ✅已修复 |
| 2 | 🔴高 | 邮件详情弹窗中 mail_type 显示英文原始值（`verification_code`/`team_invite`/`unknown`），两个页面都有 | ✅已修复 |
| 3 | 🔴高 | 散户换设备/清浏览器缓存后本机邮箱丢失，空列表无任何「用原CDK可找回」提示 | ✅已修复 |
| 4 | 🟡中 | 批量导入textarea的 placeholder 只展示纯邮箱格式，未说明支持 `邮箱----密钥` 格式（实际后端支持） | ✅已修复 |
| 5 | 🟡中 | 自动刷新开启后无视觉反馈（无倒计时/spinner），用户无法判断是否还在刷新 | ✅已修复 |
| 6 | 🟡中 | 用户中心（/web/user）收件箱无自动刷新功能，等验证码需手动点刷新 | ✅已修复 |
| 7 | 🟡中 | 管理员概览卡片缺少 CDK 售卖统计（已售/总量/兑换数）需跳到「卡密」页才能查看 | ✅已修复 |
| 8 | 🟡中 | CDK生成时填写「指定邮箱」后「生成数量」字段仍可见且可编辑，说明文字不够显眼，容易误解 | ✅已修复 |
| 9 | 🟡中 | 库存耗尽/卡密过期/停用错误提示无联系渠道入口，用户无从求助 | ✅已修复 |
| 10 | 🟡中 | 批量兑换成功的结果卡片只显示邮箱地址，不展示密钥，用户无法直接看到完整凭据 | ✅已修复 |
| 11 | 🔴高 | 管理员修改密码用 `prompt()` 弹窗：密码明文可见（非 password 类型输入）、可被浏览器拦截、无验证反馈 | ✅已修复 |
| 12 | 🟡中 | CDK 搜索框无防抖，每次按键直接调 `refreshCdks(true)` 触发 API，快速打字产生大量并发请求 | ✅已修复 |
| 13 | 🟡中 | 换货（replace）成功后新凭据以纯文本拼在状态行中，无「复制凭据」按钮，密钥长串难以手动选取 | ✅已修复 |
| 14 | 🟡中 | 用户中心收件箱验证码 pill 仅展示，无「一键复制验证码」按钮，需点开全文才能复制 | ✅已修复 |
| 15 | 🟡中 | 查询页「我的邮箱（本机缓存）」区域无搜索/过滤，邮箱多时难以快速定位目标邮箱 | ✅已修复 |
| 16 | 🟡中 | 换货成功后只刷新 stats/stock，未调 `refreshCdks()`，CDK 列表中已用次数等不同步 | ✅已修复 |
| 17 | 🟡中 | 管理员「编辑邮箱备注」也用 `prompt()` 弹窗，与页面风格割裂且无法留空清空备注 | ✅已修复 |
| 18 | 🟡中 | 管理员 CDK 列表无「首页」按钮，百条卡密时要多次点「上一页」才能回到顶部 | ✅已修复 |
| 19 | 🟡中 | 用户中心「重置密钥」成功后提示「请使用新凭据」，但新密钥未高亮/滚动显示，用户须自行在列表中找 | ✅已修复 |
| 20 | 🟡中 | 生成 CDK 后状态文字（`cdk-status-bar`）不会自动清空，再次生成前会残留上一次的成功/错误提示 | ✅已修复 |
| 21 | 🔴高 | 用户中心「删除邮箱」和「重置密钥」均用 `confirm()` 弹窗，可被浏览器拦截，风格割裂 | ✅已修复 |
| 22 | 🔴高 | 管理员「撤销 CDK」也用 `confirm()` 弹窗（第 6768 行），同样可被拦截 | ✅已修复 |
| 23 | 🟡中 | CDK 分页只显示「第 N 页」，不显示总页数（如「第 2/5 页」），翻页时无整体感知 | ✅已修复 |
| 24 | 🟡中 | 管理员收件箱分页无「首页」按钮，与 CDK 列表 #18 修复后不一致 | ✅已修复 |
| 25 | 🟡中 | CDK 列表「复制」按钮点击无按钮级反馈，只更新 `cdk-status-bar`，多条卡密时用户不知道复制了哪条 | ✅已修复 |
| 26 | 🟡中 | CDK「已复制卡密」提示不自动清空（#20 只修了生成成功，复制/撤销操作提示同样残留） | ✅已修复 |
| 27 | 🟡中 | 查询页邮件结果无总条数提示，邮件多时用户不知是否看到了全部 | ✅已修复 |
| 28 | 🟡中 | 管理员邮箱列表搜索框无防抖，快速输入触发多次 API 请求 | ✅已修复 |
| 29 | 🟡中 | 用户中心搜索过滤后若无匹配，空状态文案「你还没有邮箱」误导用户以为真的没有邮箱 | ✅已修复 |
| 30 | 🟡中 | CDK 生成表单「有效期」字段无清除按钮，设置后无法通过 UI 置空为永不过期 | ✅已修复 |
| 31 | 🔴高 | 管理员收件箱搜索框无防抖，每次按键直接触发 `refreshInbox()` API（与 CDK #12、邮箱 #28 不一致） | ✅已修复 |
| 32 | 🟡中 | CDK 「撤销」成功后 `setCdkStatus("已撤销","ok")` 无自动清空；「查看邮箱」同样残留，与复制 3s/生成 5s 清空不一致 | ✅已修复 |
| 33 | 🟡中 | CDK 列表筛选无结果时固定显示「暂无卡密」，无法区分「库存真空」vs「筛选无匹配」，用户困惑 | ✅已修复 |
| 34 | 🔴高 | 用户中心「批量删除邮箱」用原生 `confirm()` 弹窗（#21 漏掉此处），可被浏览器拦截，风格割裂 | ✅已修复 |
| 35 | 🟡中 | CDK 分页「上一页/下一页」按钮在请求进行中未禁用，快速点击触发并发请求导致数据错乱 | ✅已修复 |
| 36 | 🟡中 | 管理员「换货」输入框无回车快捷键，操作习惯与查询页（回车搜索）不一致，需鼠标点按钮 | ✅已修复 |
| 37 | 🟡中 | CDK 列表「撤销」按钮无 `danger` 样式，与「复制」/「查看邮箱」视觉权重相同，误操作风险高 | ✅已修复 |
| 38 | 🟡中 | 管理员后台「修改密码」栏提交无回车快捷键支持，需鼠标点「确认修改」 | ✅已修复 |
| 39 | 🟡低 | 用户中心「兑换记录」全量渲染无分页无总数提示，记录多时列表过长 | ✅已修复 |
| 40 | 🟡低 | 查询页「自动刷新」倒计时文案在静默模式收到新邮件后被「🔔 收到新邮件」覆盖，下次刷新前文案残留（应 3s 后复原为刷新中标识） | ✅已修复 |
| 41 | 🔴高 | 用户中心「修改密码」用 `prompt()`/`alert()`（#11 只修了管理后台），密码明文可见、无长度校验、可被浏览器拦截 | ✅已修复 |
| 42 | 🟡中 | 管理员页面模态框（`mailboxMailsModal`、`tagEditModal`）无 Escape 关闭快捷键，只有用户中心的邮件弹窗有（行 4889） | ✅已修复 |
| 43 | 🟡中 | CDK 生成成功后不清空可选字段（`cdk-batch`、`cdk-note`、`cdk-pinned`、`cdk-expires`），下一批次会误带上一次的参数 | ✅已修复 |
| 44 | 🟡中 | 管理员邮箱操作（备注/重置密钥/切换状态/删除）的 `setStatus("...","ok")` 成功提示永不自动清空，消息驻留至下一次操作 | ✅已修复 |
| 45 | 🟡中 | 用户中心注册流程缺客户端密码长度校验，输入 1 位密码需等 API 返回才报错，体验差 | ✅已修复 |
| 46 | 🟡中 | 查询页手动查询成功后 `setStatus("查询成功","ok")` 永不自动清空，开启自动刷新后仍显示「查询成功」与自动刷新状态混淆 | ✅已修复 |
| 47 | 🟡中 | 用户中心登录/注册表单的密码输入框按回车不提交，需鼠标点按钮（与查询页回车习惯不一致） | ✅已修复 |
| 48 | 🟡中 | 管理员「换货」成功后凭据行（`replace-status`）永不自动清空，下次换货前旧凭据一直可见造成混淆 | ✅已修复 |
| 49 | 🟡低 | 管理员 CDK 状态筛选器激活时无视觉标识（无角标/高亮），用户翻页时容易忘记当前有过滤条件 | ✅已修复 |
| 50 | 🟡低 | 用户中心「我的邮箱」搜索框超过 4 条时出现，但删至 4 条及以下后搜索框不消失，且搜索词仍生效导致误过滤 | ✅已修复 |
| 51 | 🔴高 | 管理员「删除标签」仍用原生 `confirm()`（#22/#34 漏掉此处），可被浏览器拦截，风格割裂 | ✅已修复 |
| 52 | 🔴高 | 管理员「删除邮箱」仍用原生 `confirm()`（行 6300，#22/#34 漏掉），可被浏览器拦截，风格割裂 | ✅已修复 |
| 53 | 🔴高 | **安全**：三个页面邮件正文 iframe 用 `srcdoc` 渲染原始 HTML 且无 `sandbox`，恶意邮件的 `<script>` 可在本站 origin 执行，窃取 session cookie / 邮箱密钥（存储型 XSS） | ✅已修复 |
| 54 | 🔴高 | 会话过期无兜底：用户中心/管理后台开着页面时 session 失效，`api()` 收到 401 只弹「加载失败」，页面停在坏掉的仪表盘不回登录页；若收件箱自动刷新在跑还会每隔几秒静默撞 401 | ✅已修复 |
| 55 | 🟡中 | 正确性：管理仪表盘「已售邮箱」统计取前 200 个邮箱客户端过滤，>200 时静默少算营收核心指标 | ✅已修复 |
| 56 | 🟡中 | 正确性：管理仪表盘「CDK兑换次数」按前 200 个 CDK 的 used_count 前端求和，CDK 超 200 时静默少算 | ✅已修复 |
| 57 | 🟡中 | 正确性+统一：管理仪表盘「启用邮箱」仍取前 200 采样不准；改用后端 `/web/admin/stats`(sales_stats) SQL 全量聚合作权威源，并把语义模糊的「启用邮箱」改为项目术语「可发货」(available AND active)，同时精简 2 个采样请求 | ✅已修复 |
| 58 | 🟡中 | UX：生成 CDK 时「有效期」若手滑设成过去时间，CDK 一生成即失效且无任何提示，管理员造出「死码」还以为正常 | ✅已修复 |
| 59 | 🔴高 | **数据一致性**：换货(replace)后未同步 `cdk_redemptions.addresses`，买家清缓存后用原 CDK 找回会拿回已作废的旧邮箱（登录用户还会把 dead 邮箱重新 claim 回名下），而非换货后的新邮箱 | ✅已修复 |
| 60 | 🔴高 | 逻辑顺序：`redeem_cdk` 的 `cdk_disabled` 检查排在「幂等找回」之前，导致管理员撤销 CDK 后，已付款的匿名买家清缓存无法找回自己已购邮箱（撤销本应只挡新兑换） | ✅已修复 |
| 61 | 🔴高 | **数据一致性**：换货 `replace_sold_mailbox` 挑替补邮箱的两条分支（tag/默认）都未排除 `pinned_cdk_id`，会把「为其他专属码预留的邮箱」当普通库存发出，导致该专属码买家兑换时 `insufficient_stock`（付了钱拿不到预留邮箱）；`redeem_cdk` 各路径已正确排除，唯换货遗漏 | ✅已修复 |
| 62 | 🔴高 | **数据一致性**：批量导入对已存在地址的 UPDATE 只置 `active=1` 不重置 `status`。管理员软删邮箱（`status='deleted',active=0`）或换货作废（`dead`）后重新导入同地址，结果显示「updated 成功」，但 row 变成矛盾态 `active=1 + status='deleted'/'dead'`，「可发货」要求 `status='available'`，该邮箱在所有库存查询/统计里都不可见也发不出去，等于导入静默失败、且遗留脏数据 | ✅已修复 |
| 63 | 🔴高 | **越权/数据泄露**：`set_mailbox_status` 把邮箱手动改回 `available`/`presale` 时清了 `mailbox_credentials.owner_user_id`，但没删 `user_mailboxes` 归属链接（兑换是双轨写入、换货会删，唯此处遗漏）。管理员把已售给用户 A 的邮箱改回可发货并转卖给用户 B 后，用户 A 的用户中心仍能看到该邮箱、读邮件、重置密钥——跨用户越权 | ✅已修复 |
| 64 | 🔴高 | **僵尸库存**：撤销专属码（`set_cdk_active(id,False)`）只翻 `cdks.active=0`，未释放其预留邮箱的 `pinned_cdk_id`。该邮箱仍 `status='available'` 但专属码已停用无法兑换、所有普通/tag/换货路径又排除 pinned 行，成为「available 却永远发不出」的悬挂库存，还虚增可发货统计 | ✅已修复 |
| 65 | 🔴高 | **一箱双卖**：生成专属码时只跳过「不存在/已售」，未查 `pinned_cdk_id`。管理员对同一地址重复生成专属码，第二次会把 `pinned_cdk_id` 覆盖为新码，旧码仍 active 且 `pinned_mailbox_ids` 指向该邮箱→旧码买家兑换时 pinned 查询要求旧码 id 却已被改成新码→`insufficient_stock`，旧码静默作废（一个邮箱被两个码卖出） | ✅已修复 |
| 66 | 🔴高 | **越权/归属分叉**：`redeem_cdk` 幂等找回路径对登录用户无条件 `INSERT user_mailboxes`，即使邮箱已归属别人。用户 B 输入一个已被用户 A 认领的旧码，`UPDATE owner WHERE owner=0` 对 A 拥有的邮箱空操作（owner 仍 A），但仍给 B 插了 user_mailboxes 链接→两个归属真相源分叉（owner=A，链接=A+B），reset/delete 只认 A 但读邮件认 user_mailboxes→B 能读 A 的邮件 | ✅已修复 |
| 67 | 🔴高 | **数据一致性（#62 同源漏网）**：管理员主用的 textarea 批量导入走 `bulk_create_mailbox_credentials`（非 #62 修的 CSV 方法），其已存在 UPDATE 同样只置 `active=1` 不重置 `status`，软删/作废邮箱重导后同样落入 `active=1+status='deleted'/'dead'` 僵尸态、库存/统计不可见 | ✅已修复 |
| 68 | 🔴高 | **买家锁死**：textarea 批量导入 `bulk_create_mailbox_credentials` 对每个已存在地址（含 `sold`）都 `generate_access_key` 轮换密钥。管理员重导入一份含已售地址的库存清单时，这些在用买家的 `地址----旧密钥` 凭据被静默作废，买家被锁在门外且无任何提示 | ✅已修复 |
| 69 | 🔴高 | **静默截断/数据丢失**：CDK 导出 `export.txt` 调 `list_cdks(limit=200)`，而 `list_cdks` 把 limit 硬顶 200。管理员生成 500 张卡密点「导出」只拿到前 200 张，静默丢失 300 张待售卡密（无任何提示），管理员拿残缺清单去售卖 | ✅已修复 |
| 70 | 🟡中 | **无法解除预留**：`set_mailbox_status` 的 `available`/`presale` 分支不清 `pinned_cdk_id`。管理员想把某个被专属码预留的邮箱退回普通库存出售，点「改为可发货」但 `pinned_cdk_id` 残留，普通兑换/换货路径仍排除它→永远发不出；除撤销专属码（#64）外无 UI 途径解除预留；改 presale 更会留下 `presale+pinned` 矛盾态 | ✅已修复 |
| 71 | 🟡中 | **筛选与徽章不一致**：`list_cdks` 的 status 筛选（`used`/`expired`）条件非互斥，而卡片徽章按优先级 `disabled>used>expired>active` 给唯一状态。`active=0且used满` 徽章「已停用」却也进「已用尽」筛选；`used且过期` 徽章「已用尽」却也进「已过期」筛选。筛选桶重叠、与徽章对不上、计数不能划分到总数，管理员困惑 | ✅已修复 |
| 72 | 🟡中 | **专属码静默丢弃地址**：pinned 生成 `clean_pinned` 只 `strip()` 不 lowercase，而库里地址是小写归一的→管理员输入含大写的地址 `WHERE address=?` 匹配不上被静默跳过；且前端只报「已生成 N 个」，管理员粘 5 个地址实际生成 3 个时不知另 2 个为何/哪些没生成（不存在/已售/已被其他专属码预留） | ✅已修复 |
| 73 | 🟡中 | **专属码静默丢弃（#72 同源漏网）**：邮箱行「生成专属卡密」`btn-gen-cdk-from-mb` 是另一个 handler，#72 前端只修了主表单 `btn-gen-cdk`。勾选 N 个邮箱行生成时若含已售/已被其他专属码预留的，只报「已生成 N 个」不报跳过数；全被跳过时还会 `copyText("")` 复制空串并误报「已生成 0 个卡密」 | ✅已修复 |
| 74 | 🔴高 | **删标签致买家锁死（僵尸 CDK，同 #64 类）**：`delete_mailbox_tag` 连带删掉该 tag 的所有 `mailbox_tag_links`，但不处理仍绑定该 `tag_id` 的 CDK。管理员删掉一个还有未售 CDK 绑定的品类后，这些 CDK 兑换走 `tag_id>0` 分支 `EXISTS mailbox_tag_links WHERE tag_id=?` 恒为空→永远 `insufficient_stock`，买家付款兑不出；且删除无任何提示/守卫 | ✅已修复 |
| 75 | 🟡低 | **枚举 oracle（安全）**：`verify_mailbox_access` 把 `active` 检查排在密钥校验之前，密钥错误时 inactive（dead/deleted）地址返回 `mailbox_inactive`、其余返回 `invalid_credential`，攻击者不持密钥即可区分「地址存在但已停用」vs「不存在」，枚举出售卖过的邮箱地址 | ✅已修复 |
| 76 | 🟡中 | **越权/归属分叉（#66 同类，assign 路径）**：管理员 API `assign_mailbox`（`/web/admin/users/{id}/assign-mailbox`）只往 `user_mailboxes` 插链接，不校验邮箱是否存在或已归属他人。指派一个已售给用户 B 的地址给用户 A→A 也拿到读链接→越权读 B 邮件；指派不存在的地址→插入幽灵链接。仅 API 可达无 UI，但端点存在即可被误用 | ✅已修复 |
| 77 | 🟡中 | **悬挂标签链接致库存分类少算**：两条批量导入（`bulk_create_mailbox_credentials` textarea 主路径 + `import_mailbox_credentials_csv`）直接把请求里的 `tag_ids` 插入 `mailbox_tag_links`，不校验 tag 是否存在（单条 `set_mailbox_tags` 却校验）。删标签竞态/直连 API 传入不存在的 tag_id→插入悬挂 link→该邮箱在 `stock_summary_by_tag` 里既不属任何标签（LEFT JOIN 落空）也不算无标签（`NOT EXISTS link` 不成立）→按品类库存少算、与总数对不上 | ✅已修复 |
| 78 | 🟡中 | **注册端点无限流（刷号/CPU-DoS）**：`/web/auth/login` 有 `login_limiter` 防暴破，但 `/web/auth/register` 完全无节流。注册是公开未鉴权端点，每次都跑昂贵 PBKDF2 `build_password_hash` + 写 `users` 表，脚本可无限刷号（DB 膨胀）并借 PBKDF2 耗 CPU（DoS） | ✅已修复 |
| 79 | 🔴高 | **公共取件端点无限流（暴力试密钥）**：`/web/query-mails` 与 `/web/query-mail-detail` 均公开未鉴权、凭 `地址----密钥` 校验，却无任何限流（login/redeem 都有）。地址即售卖产品易枚举，`access_key` 是保护买家邮箱与验证码的唯一秘密，攻击者可无限速暴力试 key 盗取他人邮箱/验证码 | ✅已修复 |
| 80 | 🔴高 | **限流可被 XFF 伪造绕过（使 #78/#79 及 login/redeem 限流失效）**：`_client_ip()` 取 `X-Forwarded-For` 的第一跳（最左），而标准反代（nginx `$proxy_add_x_forwarded_for`）把真实客户端 IP **追加到 XFF 末尾**，最左段完全由客户端伪造。攻击者每请求换一个 XFF 前缀即得到不同限流 key，绕过 login/register/query/redeem 全部限流——刚补的暴破防护形同虚设 | ✅已修复 |

## 修复记录

- [#1] 在查询页 `redeem()` 函数入口加 `btn.disabled=true`，成功/失败分支末尾恢复 `btn.disabled=false` → 约第 4190 行
- [#2] 三处 `el("modalType").textContent` 均改为翻译后中文：查询页用已有 `typeLabel()`，用户中心和管理后台用内联对象查找 → 约第 4067、4590、6436 行
- [#3] 在查询页兑换区底部新增一行提示「换了设备或清除了浏览器缓存？重新输入原卡密兑换即可找回已购邮箱。」→ 约第 3771 行
- [#4] 批量导入 textarea placeholder 增加 `邮箱----密钥` 示例行及说明 → 第 5299 行
- [#5] label 文字加 `id="auto-refresh-label"`；`startAutoRefresh` 改为「🔄 自动刷新中…」，`stopAutoRefresh` 复原 → 约第 3800、3973 行
- [#6] 用户中心收件箱 panel-head 加 checkbox label；新增 `inboxAutoTimer`/`startInboxAutoRefresh`/`stopInboxAutoRefresh`；`loadInbox` 显示 label，`loadMailboxes` else 分支隐藏并停 timer → 约第 4421、4724、4841 行
- [#7] 概览加「已售邮箱」和「CDK兑换次数」两张 stat-card；`loadDashboard` 并发拉取 `/web/admin/cdks?limit=200` 计算 used_count 之和，已售邮箱从邮箱列表过滤 status=sold → 约第 5207、6496 行
- [#8] 监听 `cdk-pinned` textarea input 事件，有内容时用 `.closest("label")` 隐藏「生成数量」字段，清空后恢复 → 约第 6697 行
- [#9] `getRedeemErrorMessage` 中 `cdk_expired/disabled/not_found/insufficient_stock/mailbox_unavailable` 及 fallback 均追加「如需帮助请联系购买渠道卖家」；`cdk_used` 改为提示重新输入可找回邮箱 → 约第 4177 行
- [#10] 批量兑换成功行改为 `address----access_key` 格式，多邮箱用 `<br>` 分行；覆盖查询页和用户中心两处 → 约第 4255、4918 行
- [#11] 在 `</header>` 后插入 `#change-pass-bar` 折叠栏（两个 `type=password` 输入）；`btn-change-pass` 改为切换栏的显示，新增 `cp-submit`/`cp-cancel` handler，含前端验证（非空/6位）和错误映射；删除原 `prompt()` → 约第 5188、6131 行
- [#12] `cdk-search` oninput 改为 `clearTimeout(cdkSearchTimer); cdkSearchTimer = setTimeout(() => refreshCdks(true), 300)` 300ms 防抖 → 约第 6751 行
- [#13] 换货成功改用 innerHTML 展示凭据（`<code user-select:all>`）并附「复制凭据」按钮；同时顺带修了 #16 → 约第 6762 行
- [#16] 换货成功后追加 `refreshCdks(true)` 调用，与 #13 同处修复 → 约第 6762 行
- [#14] 验证码 pill 改为 `<button data-copy-code>` 按钮；inbox-list 点击监听器在最前面拦截 `[data-copy-code]` 点击，复制后临时显示「已复制 ✓」，不触发弹窗 → 约第 4716、4812 行
- [#15] 「我的邮箱」panel 新增 `#my-mailbox-search` 搜索框，超过 4 条时自动显示；`renderMyMailboxes` 按输入词过滤列表，无匹配时显示提示；绑定 input 事件实时重渲 → 约第 3785、4149 行
- [#17] 在管理员页面 `</body>` 前插入 `<dialog id="note-dlg">` 原生对话框（含文本输入框、确认/取消按钮）；新增 `promptNote(current)` Promise 封装；将 `edit-note` 分支的 `prompt()` 替换为 `await promptNote(...)` → 约第 6201、6812 行
- [#18] CDK 分页栏新增 `#cdk-first-page`「首页」按钮，点击重置 `cdkPagination.offset = 0` 并刷新列表 → 约第 5507、6794 行
- [#19] 重置密钥成功后改用 innerHTML 显示新凭据 `address----key`（`<code user-select:all>`）+ 「复制」按钮，不再只说「请使用新凭据」→ 约第 4800 行
- [#20] 生成 CDK 成功后 `setCdkStatus(...)` 后追加 `setTimeout(() => setCdkStatus(""), 5000)` — 5 秒后自动清空成功提示，错误提示保留 → 约第 6734 行
- [#21] 用户中心 `</body>` 前插入 `<dialog id="user-confirm-dlg">`；新增 `confirmAction(msg)` Promise 封装；将「删除邮箱」和「重置密钥」两处 `confirm()` 替换为 `await confirmAction(...)` → 约第 4770、4798、5044 行
- [#22] 管理员页面新增 `confirmAction()` 函数 + `<dialog id="admin-confirm-dlg">`；将 CDK 撤销 `confirm()` 替换为 `await confirmAction(...)` → 约第 6786、6852、6871 行
- [#23] `refreshCdks` 末尾加 `totalPages = Math.ceil(total/limit) || 1`，分页标签改为「第 N/M 页」→ 约第 6716 行
- [#24] 管理员收件箱分页栏新增 `#inbox-first-page`「首页」按钮，点击重置 `inboxPagination.offset = 0` 并刷新 → 约第 5562、6533 行
- [#25] CDK 列表「复制」按钮点击后文字改为「已复制 ✓」1.2 秒后复原；同时在 `cdk-status-bar` 显示「已复制卡密」3 秒后自动清空 → 约第 6771 行
- [#26] 复制操作的 `cdk-status-bar` 提示随 #25 同步加 `setTimeout(() => setCdkStatus(""), 3000)` 3 秒自动清空，与生成成功的 5 秒清空一致 → 约第 6771 行
- [#27] `totalResultsInfo` 元素及 `共找到 N 封邮件，当前展示 M 封` 文案已在此前迭代中实现，本轮确认无需改动 → 约第 3803、3932 行
- [#28] 管理员邮箱搜索框 `oninput` 改为 `clearTimeout(mailboxSearchTimer); mailboxSearchTimer = setTimeout(() => refreshMailboxes(true), 300)` 300ms 防抖，与 CDK 搜索 #12 一致 → 约第 6104 行
- [#29] 用户中心 `renderMailboxItems` 已用 `cachedMailboxes.length` 三元判断区分「过滤无匹配」vs「真无邮箱」，本轮确认逻辑正确无需改动 → 约第 4616 行
- [#30] CDK「有效期」`<label>` 改为 `<div class="field">`，输入框用 flex 行包裹并追加「清除」按钮，`onclick` 置空 `cdk-expires` 值 → 约第 5453 行
- [#31] 管理员收件箱搜索框 `oninput` 改为 300ms 防抖（`inboxSearchTimer`），与 CDK #12 / 邮箱 #28 一致 → 约第 6537 行
- [#32] CDK 撤销成功追加 `setTimeout(() => setCdkStatus(""), 3000)`；「查看邮箱」setCdkStatus 后追加 4s 清空 → 约第 6792、6803 行
- [#33] `refreshCdks` 空列表分支按 `cdkPagination.keyword||status||tagId` 判断，有筛选时显示「无匹配卡密」，无筛选时显示「暂无卡密」→ 约第 6702 行
- [#34] 用户中心批量删除 `confirm()` 替换为 `await confirmAction(...)`，复用 #21 已插入的 `user-confirm-dlg` 原生对话框 → 约第 4853 行
- [#35] `refreshCdks` 开始时禁用三个分页按钮（`cdk-first/prev/next-page`），`finally` 块恢复，防止并发请求 → 约第 6688 行
- [#36] `replace-address` 输入框绑定 `keydown Enter` 触发 `btn-replace.click()`，与查询页回车习惯一致 → 约第 6818 行
- [#37] CDK 撤销按钮 `class="secondary"` 改为 `class="secondary danger"`，视觉上区分危险操作 → 约第 6719 行
- [#38] 管理员修改密码 `cp-old`/`cp-new` 绑定 `keydown Enter` 触发 `cp-submit.click()` → 约第 6190 行
- [#39] `/web/me/redemptions` 后端已有 `limit=50` 兜底；前端无兑换记录渲染 UI，无需修复
- [#40] 静默刷新收到新邮件后 `setStatus("🔔 收到新邮件","ok")` 后追加 `setTimeout(() => setStatus(""), 4000)` 4s 自动清空 → 约第 4012 行
- [#41] 用户中心 `</header>` 后插入 `#u-change-pass-bar` 折叠栏（`u-cp-old`/`u-cp-new` password 输入 + 确认/取消），Enter 键支持；`btn-change-pass` handler 改为切换栏，删除 `prompt()`/`alert()` → 约第 4382、4960 行

- [#42] 管理员页面新增 `document keydown Escape` 监听：优先关 `tagEditModal`，否则关 `mailboxMailsModal` → 约第 6420 行
- [#43] `btn-gen-cdk` 成功后清空 `cdk-batch/note/pinned/expires` 四个可选字段，并恢复被 pinned 隐藏的「生成数量」字段 → 约第 6797 行
- [#44] 管理员 `setStatus` 增加共享 `adminStatusTimer`：`kind==="ok"` 的成功提示 3.5s 后自动清空（错误/进行中保留），一次性覆盖备注/重置/切换/删除等所有成功提示 → 约第 5716 行
- [#45] 用户中心注册分支在调 API 前加 `password.length < 6` 客户端校验，即时报错「密码至少 6 位」→ 约第 5023 行
- [#46] 查询页「查询成功」提示追加 3s 自动清空（文案未变时才清），避免与自动刷新状态混淆 → 约第 4015 行
- [#47] `auth-user`/`auth-pass` 绑定 `keydown Enter` 触发 `btn-auth-submit.click()`，与查询页回车习惯一致 → 约第 5043 行
- [#48] 换货成功后 `replace-status` 由共享 `replaceStatusTimer` 在 20s 后清空（凭据留足复制时间），下次换货时重置计时器 → 约第 6861 行
- [#49] `refreshCdks` 中对 `cdk-status-filter`/`cdk-tag-filter` 按 value 非空切换高亮（边框色+浅底+加粗），提醒当前存在过滤条件 → 约第 6735 行
- [#50] `renderMyMailboxes` 隐藏搜索框（≤4 条）时同步 `searchEl.value=""`，避免残留搜索词把 ≤4 条邮箱误过滤成「无匹配」→ 约第 4159 行
- [#51] 隐藏 bug：管理员删除标签的 `document click` 监听内 `confirm()` 替换为 `await confirmAction(...)`，复用管理端原生对话框 → 约第 6396 行
- [#52] 隐藏 bug：管理员删除邮箱（`action==="delete"`）的 `confirm()` 替换为 `await confirmAction(...)` → 约第 6300 行
- [#53] 安全隐患：三处 `<iframe id="emailBodyFrame">` 加 `sandbox="allow-popups allow-popups-to-escape-sandbox"`，禁止脚本执行与同源访问，堵住恶意邮件 HTML 的存储型 XSS；保留 popups 让邮件里的验证/邀请链接仍可点开 → 约第 3843、4476、5630 行
- [#54] 会话过期兜底：用户中心 `api()` 在仪表盘可见时收到 401 → `stopInboxAutoRefresh()` + `showAuth()` + 提示「登录已过期，请重新登录」（用 dash 可见性守卫，避免登录流程误触）；管理后台 `api()` 收到 401 → 一次性 `alert` 后跳回 `/web/query`。后端 `_require_session_user` 确认对失效会话返回 401 → 约第 4507、5678 行
- [#55] `loadDashboard` 并发追加 `api("/web/admin/mailboxes?status=sold&limit=1")`，用其精确 `total` 填「已售邮箱」（复用后端已支持的 status=sold 过滤计数，零后端改动）；「启用」数与「CDK兑换次数」仍前 200 采样并补 ponytail 注释说明上限（精确化待后端加 SUM 聚合）→ 约第 6619 行
- 本轮核查（无需改动）：后端 POST 统一走 `_do_POST_impl` 开头的 `_body_too_large()`（10 MiB 上限，含 `/inbound/email`）→ 无 OOM 缺口；CSV 导出仅 `地址----密钥` 纯文本、无公式注入面；查询页/收件箱自动刷新均有双定时器守卫 + `document.hidden` 暂停 → 无泄漏；兑换 API 确返回 `credential` → 前端复制凭据正确
- [#56] `list_cdks` 在锁内加 `SELECT COALESCE(SUM(c.used_count),0) ... {where_sql}` 全量聚合，返回值改为 `(items, total, total_used)`；同步更新两处调用（列表端点响应加 `total_used`，导出改 `_used` 占位）；`loadDashboard` 用 `cdk.data.total_used`（兜底前 200 求和）填「CDK兑换次数」→ 约第 2106、7212、7256、6640 行。备注：后端已存在 `/web/admin/stats`→`sales_stats()` 端点，后续可考虑把仪表盘所有统计统一迁到该聚合接口
- [#57] `loadDashboard` 改用 `/web/admin/stats`（`sales_stats()` 的 SQL 全量聚合）作邮箱统计权威源：`stat-mailboxes=total`、`stat-sold=sold` 均精确；语义模糊且不准的「启用邮箱」卡片改为项目术语「可发货」并用 `stats.available`（= `status='available' AND active=1`，CLAUDE.md 定义的「可发货」）；删去 `?limit=200` 与 `?status=sold&limit=1` 两个采样请求（4 个并发降为 3）。冒烟测试验证 total/available/sold 语义正确（4/2/1）→ 约第 5303、6627 行
- [#58] `btn-gen-cdk` 生成前对「有效期」加未来时间校验：解析 `datetime-local` 后若 `<= Date.now()` 则 `setCdkStatus("有效期不能早于当前时间...")` 并中止（恢复按钮），避免造出一生成即过期的死码 → 约第 6812 行
- 本轮核查（无需改动）：`redeem_cdk` 全内联 SQL + 单锁，双守卫（`UPDATE...WHERE status='available'` rowcount 回滚 + `used_count<max_uses` rowcount 回滚），清缓存可幂等找回 → 并发安全无死锁；CDK 过期判断/state/active 过滤均用 `utcnow_iso()` UTC 词法比较，前端 `datetime-local`→`toISOString()` 转 UTC，全链路时区一致；展示用 `formatBeijingDateTime` 转北京时间
- [#59] `replace_sold_mailbox` 换货成功后在同一把锁内同步订单流水：按 `order_id` 取 `cdk_redemptions` 行，把 `addresses` 里的旧地址换成 `new_address`、`mailbox_ids` 里的 `old_id` 换成 `new_id` 再回写。修复前 reissue 找回读旧 `addresses` 会返回作废旧邮箱。端到端冒烟测试（presale→生成→兑换 old→换货 new→原码找回）验证 `addresses==['new@x.com']` 且找回返回 new@x.com → 约第 1652 行
- [#60] `redeem_cdk` 把 `cdk_disabled` 检查从「读 cdk 之后立即」下移到「幂等找回之后、首次发货之前」：已兑换过的码即使被撤销/过期也能找回已付款邮箱（与 reissue 注释「already paid, expiry ignored」一致），撤销只挡首次新兑换。端到端冒烟测试验证：撤销后原码找回成功且返回同一邮箱（reused）、未兑换的撤销码新兑换被 `cdk_disabled` 挡住 → 约第 1904、1957 行

- [#61] `replace_sold_mailbox` 的 tag 分支与默认分支替补查询各加 `AND (pinned_cdk_id IS NULL OR pinned_cdk_id = 0)`，与 `redeem_cdk` 的排除逻辑对齐，换货不再偷走专属码预留邮箱。端到端冒烟（sold a + 专属预留 b + 普通 c，换货应挑 c 跳过 b）验证：替补=c@x.com，b 仍 available 且 pinned 不变 → 约第 1620、1627 行
- [#62] `import_mailbox_credentials_csv` 的已存在 UPDATE 改用 CASE：`status IN ('deleted','dead')` 的行复活为 `presale` 并清 `owner_user_id/sold_at/order_id`；`sold`/`available`/`presale` 保持不变（SQLite 的 SET 右值读旧行值，所有 CASE 都看到 pre-update status，不会互相污染）。修复前重导入软删/作废邮箱只置 active=1 留下 `active=1+status='deleted'` 脏态。端到端冒烟（重导 deleted+dead+sold+available 四态）验证：前两者→presale 且归属清空，sold(owner=7,order=3) 与 available 原样保留 → 约第 1378 行
- [#63] `set_mailbox_status` 的 `available`/`presale` 分支（会清 owner_user_id）各加 `DELETE FROM user_mailboxes WHERE address = ?`（SELECT 里补取 address），与兑换双轨写入/换货删链接对齐，杜绝旧买家在邮箱转卖后仍保留访问权。端到端冒烟（sold 给 A + user_mailboxes 链接 → 改回 available/presale）验证：两分支链接均被删、owner=0 → 约第 2596、2607 行
- [#64] `set_cdk_active` 在 `active=False` 且更新命中时，追加 `UPDATE mailbox_credentials SET pinned_cdk_id=0 WHERE pinned_cdk_id=? AND status='available'`，撤销专属码即释放其未售预留邮箱回普通库存（已售 status='sold' 不动）；只在撤销分支释放（重新启用路径 UI 不可达，不回填 pin）。端到端冒烟（专属码预留 r + 已售 s → 撤销）验证：r pinned→0 且随后可被普通码正常发货，s 仍 sold → 约第 2210 行
- [#65] `generate_cdk_codes` 的 pinned 分支 SELECT 补取 `pinned_cdk_id`，在「不存在/已售」跳过后追加 `if int(row["pinned_cdk_id"] or 0) > 0: continue`，跳过已被其他专属码预留的邮箱，杜绝重复生成覆盖 `pinned_cdk_id` 导致旧码悬挂/一箱双卖。端到端冒烟（同地址生成两次专属码）验证：第二次 codes 为空、`pinned_cdk_id` 仍指向首码、首码可正常兑换 → 约第 1737 行
- [#66] `redeem_cdk` 找回路径的 `INSERT user_mailboxes` 改为条件插入：`INSERT OR IGNORE ... SELECT ?,?,? WHERE EXISTS(SELECT 1 FROM mailbox_credentials WHERE address=? AND owner_user_id=?)`，仅当该邮箱确实归属当前用户（刚认领或本就拥有）才绑定到其用户中心，避免第二个账号输入已认领码后与 `owner_user_id` 归属分叉、越权读原主邮件。端到端冒烟：A 兑换→owner=A/链接=[A]；B 输入同码→仍 owner=A/链接=[A]、`user_has_mailbox(B)=False`；A 清缓存再兑换→重新绑定 [A] → 约第 1953 行
- [#67] `bulk_create_mailbox_credentials`（textarea 主导入路径）的 `reset_rows` UPDATE 套用 #62 同款 CASE：`status IN ('deleted','dead')` 复活为 `presale` 并清 `owner_user_id/sold_at/order_id`，`sold/available/presale` 不变。修复前该主路径与 CSV 方法有同源僵尸态 bug（#62 只修了 CSV）。端到端冒烟（重导 deleted+dead+sold）验证：前两者→presale 且清归属、sold(owner=7,order=3) 保留 → 约第 1264 行
- [#68] `bulk_create_mailbox_credentials` 的 existing 查询补取 `status`，`reset_rows` 列表推导加 `and existing_status.get(addr) != "sold"`——`sold` 邮箱不参与密钥轮换，保护在用买家的 `地址----密钥` 凭据不被静默作废；非售库存（available/presale/deleted/dead）仍正常刷新密钥、deleted/dead 仍走 #67 复活 CASE。端到端冒烟（重导 sold+available+deleted）验证：sold 密钥保留 BUYERKEY 不动、available 刷新、deleted→presale 清归属 → 约第 1249、1255 行
- [#69] `export.txt` 端点改为按 `limit=200` 翻页循环累积（`while True: list_cdks(offset=export_offset); if len(items)<200: break; offset+=200`），导出全部匹配卡密而非仅前 200。端到端冒烟（450 张卡密）验证：分页导出 450 张全覆盖无重复，旧单次调用仅 200 → 约第 7346 行。附注：max_uses>1 多用途码的找回/反查（`ORDER BY id ASC LIMIT 1` 只认首次兑换）对第 2+ 买家会返回首买家邮箱，但 max_uses 无 UI 入口（生成表单不暴露、恒为 1），仅直连 API 可达，判定潜在低优先级暂不修
- [#70] `set_mailbox_status` 的 `available`/`presale` 两个 UPDATE 各加 `pinned_cdk_id=0`：管理员手动改状态即把邮箱退回普通库存并解除专属预留（此前只能靠撤销专属码 #64 解除），也杜绝 `presale+pinned` 矛盾态被后续普通批次生成的 presale→available 迁移误扫。端到端冒烟（pinned=99 available → 改 available）验证：pinned→0 且可被普通码正常发货；（pinned=77 → 改 presale）pinned→0 status=presale → 约第 2636、2647 行。核查：非 pinned 生成的 presale→available 迁移与库存计数同锁一致、无死码 → 约第 1884 行
- [#71] `list_cdks` 的 status 筛选条件改为与徽章优先级严格互斥：`used` 加 `c.active=1`、`expired` 加 `c.active=1 AND c.used_count<c.max_uses`（`disabled=active0`、`active` 不变）。使四个筛选桶与卡片徽章（`disabled>used>expired>active`）一一对应、互斥、可划分到总数。端到端冒烟（active/used/expired/disabled/disabled+used/used+expired 六态）验证：DIS_USED 只进 disabled、USED_EXP 只进 used，四桶无重叠全覆盖、每条与徽章一致 → 约第 2158 行
- [#72] 专属码生成 `clean_pinned` 改用 `normalize_address`（strip+lower）并 `dict.fromkeys` 去重保序：管理员输入大写/含空格/重复的地址都能正确匹配库中小写地址，不再静默失配；前端 `btn-gen-cdk` 成功分支计算 `pinnedSkipped = pinnedAddresses.length - codes.length`，>0 时以 error 提示「已生成 N 个专属卡密；M 个地址被跳过（不存在/已售/已被其他专属码预留）」而非只报成功数。端到端冒烟（输入 " Foo@X.com "/"FOO@x.com"/"missing@x.com"，库存 foo@x.com）验证：归一去重后仅 1 个专属码且 pinned 正确、missing 跳过 → 约第 1742、6927 行。核查（无需改动）：普通生成 `insufficient_presale` 已带 available/required 数字反馈；`sales_stats`/`stock_summary_by_tag` 的 available 含 pinned 属 CLAUDE.md「可发货」定义、非 bug；`today_*` 用北京午夜转 UTC 边界正确；`list_user_redemptions` 按 user_id 过滤、地址随 #59 同步一致
- [#73] `btn-gen-cdk-from-mb` handler 同 #72 加跳过反馈：`skipped = addresses.length - codes.length`，`codes` 为空时改报「未生成任何卡密：N 个所选邮箱已售或已被其他专属码预留」并 return（不再 `copyText("")` 误报成功），`skipped>0` 时提示「已生成 M 个专属卡密；N 个所选邮箱被跳过（已售/已被其他专属码预留）」。端到端冒烟（选 ok@x.com+sold@x.com→1 码 skipped=1；仅 sold→0 码）验证前端 skipped 计算前提成立 → 约第 6319 行
- 本轮核查（无需改动）：CDK 卡片有效期用 `formatBeijingDateTime`（空→「-」、无效→原文，仅 `c.expires_at` 非空时展示「到期 ...」）→ 约第 6890 行；`cdk-status-filter` 下拉四值 active/used/expired/disabled 与后端 `list_cdks` 筛选键、`stateLabel` 徽章键三处完全一致（#71 UI 侧闭环）→ 约第 5653、6879 行；`escapeHtml` 用 `/[&<>"]/g`（此前疑似反斜杠是 Read 显示伪影，实际字节正确）；三处 `formatBeijingDateTime`（查询/用户中心/管理）均含空值与 NaN 兜底
- [#74] `delete_mailbox_tag` 删除前加守卫：在锁内 `SELECT COUNT(*) FROM cdks WHERE tag_id=? AND active=1 AND used_count<max_uses AND (expires_at='' OR expires_at>?)`，仍有「可首次发货」的绑定 CDK 时返回 `tag_in_use` 拒删（端点已把 reason 原样作 error 返回，无需改端点）；前端删标签 handler 把 `tag_in_use` 映射为「该品类下仍有可用卡密绑定，请先撤销这些卡密或改绑其他品类后再删除」。已用尽/已撤销/已过期 CDK 不阻止删除（本就不可新兑换）。端到端冒烟（绑定 active CDK 拒删→撤销后可删；已用尽/已过期 CDK 均可删）验证 → 约第 2541、6533 行
- [#75] `verify_mailbox_access` 把 `secrets.compare_digest` 密钥校验提到 `active` 检查之前：密钥错误一律返回 `invalid_credential`（inactive 地址与不存在地址无法区分），只有持正确密钥的合法主人（如换货后旧邮箱变 dead）才看到 `mailbox_inactive` 友好提示。关闭「地址存在但已停用」的枚举 oracle，不影响正常取件与已有 UX 文案。端到端冒烟（active+对/错密钥、inactive+对/错密钥、不存在+错密钥五组）验证：仅 active+对→ok、inactive+对→mailbox_inactive，其余全 invalid_credential → 约第 1503 行。核查（无需改动）：CDK 反查邮箱 `get_cdk_bound_mailboxes` 读 `cdk_redemptions.addresses`（#59 已保证换货同步），凭据取当前 access_key、正确；`set_mailbox_tags` 已校验所有 tag_id 存在（count 不等即 tag_not_found）无悬挂链接
- [#76] `assign_mailbox` 插链接前加两道守卫（锁内内联 SQL，不调其他加锁方法）：`SELECT status,active,owner_user_id FROM mailbox_credentials WHERE address=?`，① 邮箱不存在/`status IN (deleted,dead)`/`active=0`→`mailbox_not_found`（杜绝幽灵链接）；② `owner_user_id` 非 0 且 != 目标用户→`mailbox_owned_by_other`（杜绝把他人已购邮箱的读链接插给第三人，同 #66 跨用户读信）。端点已把 reason 原样作 error 返回，无需改端点。保持「读授权」原语义不动（owner/status 不改），仅去害。端到端冒烟（ghost→not_found、dead→not_found、他人 owned→owned_by_other、未归属 free→ok 且 user_has_mailbox 生效、owner 自指派幂等 ok）验证 → 约第 2903 行
- [#77] 两条批量导入路径插 `mailbox_tag_links` 前先 `SELECT id FROM mailbox_tags WHERE id IN (...)` 过滤出真实存在的 `valid_tag_ids`，只链接有效 tag（全非法则不插任何 link），与单条 `set_mailbox_tags` 的校验对齐，杜绝悬挂链接导致的按品类库存少算。两处代码相同用 replace_all 同修。端到端冒烟（bulk textarea 纯地址 + csv `地址----密钥`，各传 [真实 tag, 不存在 tag]）验证：均只链真实 tag、全 ghost 时不链、全库无悬挂 link → 约第 1290、1424 行
- [#78] 新增 `self.register_limiter = RateLimiter(LOGIN_MAX_FAILURES, LOGIN_WINDOW_SECONDS)`（复用登录阈值 5 次/300s，不污染登录计数器），`/web/auth/register` 端点顶部先 `retry_after("register:{ip}")`>0 即 429 `too_many_attempts`（在解析/PBKDF2 前拦截省 CPU），再 `record` 每次尝试（不 reset，累计限速）。前端 auth errMap 已有 `too_many_attempts`→「尝试过于频繁，请稍后再试」，无需改前端。端到端冒烟（连续 5 次 record 后 retry_after=299s、跨 IP 独立、字段已挂载）验证 → 约第 3253、7673 行。核查（无需改动）：`create_user`/`get_user_by_username` 均 `normalize_username`（strip+lower）且锁内 check-then-insert，大小写去重正确无 TOCTOU；注册端点已有 `username_too_long`/`password 6~MAX` 校验且前后端 errMap 一致
- [#79] 新增 `self.query_limiter = RateLimiter(LOGIN_MAX_FAILURES, LOGIN_WINDOW_SECONDS)`，`/web/query-mails` 与 `/web/query-mail-detail` 两个公共取件端点顶部先 `retry_after("query:{ip}")`>0 即 429（在解析前拦截），`verify_mailbox_access` 失败且非缺参（`missing_address/access_key` 不计）时 `record`、成功时 `reset`——合法用户凭正确密钥每次成功即清零不受节流，只有暴力试 key 的错误尝试累计到 5 次/300s 触发封锁；两端点共享 `query:{ip}` 键，经任一端点暴破均累计。前端查询 errMap 已含 `too_many_attempts`（约第 4347 行），无需改前端。端到端冒烟（5 次错误 record 后 retry_after>0、reset 后放行、字段已挂载）验证 → 约第 3256、7880、7915 行。核查（无需改动）：`/web/user/redeem` 已接入 `redeem_limiter`（约第 7978/7996 行）
- [#80] `_client_ip()` 改取 `X-Forwarded-For` 的**最后一跳** `split(",")[-1]`（原为第一跳 `[0]`）：标准反代把真实客户端 IP 追加到 XFF 末尾，最左段可被客户端伪造，取末尾则攻击者只能前缀伪造、无法篡改真实 peer→限流 key 稳定。末尾空时回退 socket peer；无 XFF 用 socket peer。本服务绑 127.0.0.1 必在反代后，取末尾安全。ponytail：假设单层可信反代且追加 XFF，多层链路 YAGNI。此修复恢复了 #78/#79 及既有 login/redeem 限流的有效性。端到端冒烟（伪造 XFF 前缀 `6.6.6.6,203.0.113.9` 与 `spoof,203.0.113.9` 同得 203.0.113.9；单值 replace；无 XFF→peer；末尾空→peer）验证 → 约第 3392 行。核查（无需改动）：`/web/me/change-password` 已 `verify_password(old_password)` 且需会话；`/inbound/email` 已 `_require_auth(inbound_token)` 且 `check_bearer` 用 `secrets.compare_digest` 恒定时间、token 经 `resolve_shared_token` 默认回退 api_token 非空

---
_自动扫描生成，每轮修复一个问题_
