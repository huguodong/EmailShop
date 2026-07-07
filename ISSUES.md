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
| 42 | 🟡中 | 管理员页面模态框（`mailboxMailsModal`、`tagEditModal`）无 Escape 关闭快捷键，只有用户中心的邮件弹窗有（行 4889） | 待修复 |
| 43 | 🟡中 | CDK 生成成功后不清空可选字段（`cdk-batch`、`cdk-note`、`cdk-pinned`、`cdk-expires`），下一批次会误带上一次的参数 | 待修复 |
| 44 | 🟡中 | 管理员邮箱操作（备注/重置密钥/切换状态/删除）的 `setStatus("...","ok")` 成功提示永不自动清空，消息驻留至下一次操作 | 待修复 |
| 45 | 🟡中 | 用户中心注册流程缺客户端密码长度校验，输入 1 位密码需等 API 返回才报错，体验差 | 待修复 |
| 46 | 🟡中 | 查询页手动查询成功后 `setStatus("查询成功","ok")` 永不自动清空，开启自动刷新后仍显示「查询成功」与自动刷新状态混淆 | 待修复 |
| 47 | 🟡中 | 用户中心登录/注册表单的密码输入框按回车不提交，需鼠标点按钮（与查询页回车习惯不一致） | 待修复 |
| 48 | 🟡中 | 管理员「换货」成功后凭据行（`replace-status`）永不自动清空，下次换货前旧凭据一直可见造成混淆 | 待修复 |
| 49 | 🟡低 | 管理员 CDK 状态筛选器激活时无视觉标识（无角标/高亮），用户翻页时容易忘记当前有过滤条件 | 待修复 |
| 50 | 🟡低 | 用户中心「我的邮箱」搜索框超过 4 条时出现，但删至 4 条及以下后搜索框不消失，且搜索词仍生效导致误过滤 | 待修复 |

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

---
_自动扫描生成，每轮修复一个问题_
