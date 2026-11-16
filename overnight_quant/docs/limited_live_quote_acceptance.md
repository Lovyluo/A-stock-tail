# 有限真实自选报价：验收待批单（未批准）

观察合同 `manual_quote_limited_live_v3`；许可格式 `manual_quote_limited_permit_v1`。本工作包只做离线验收，不签发真实许可、不访问行情。它不是策略、正式来源资格或可交易性验证。

## 用户待填写

| 项 | 待批准内容 |
| --- | --- |
| 版本 | 固定完整 Git SHA：________；唯一执行工作树绝对路径：________ |
| 时间 | 北京时间同一日：________；明确起止时间（含起、不含止）：________ |
| 范围 | 唯一许可编号（8–80 位小写字母、数字、下划线或连字符）：________；规范市场＋代码集合（1–5 个）：________ |
| 用途和限额 | 仅本人内部观察，不公开传播；最多两次人工点击，同一集合，间隔至少 10 秒。用户独立确认用途依据为 UNKNOWN：________ |
| 证据 | 真实批准原文、可追溯 HTTPS 引用及原文 UTF-8 SHA-256：________。本地核对绑定，不联网核验引用；验收操作者须对照原批准，独立记录许可文件字节 SHA-256。 |

许可 JSON 需要 `schema`、`contract`、`status`、`permit_id`、`approved_sha`、`worktree`（工作树绝对路径）、`date`（YYYY-MM-DD）、`window_start`/`window_end`（ISO 8601，明确 +08:00）、`selection`（排序的规范身份数组）、`approval_reference`（`locator`、`verbatim_quote`、`quote_sha256`）。固定合同项：`purpose=PERSONAL_INTERNAL_OBSERVATION`、`source_use_basis=UNKNOWN`、`known_use_constraint=NO_KNOWN_PROHIBITION`、`max_requests=2`、`manual_clicks_only=true`、`user_approved=true`、`acknowledges_unknown_basis=true`、`public_distribution=false`、`access_control_bypass=false`。仅获得新批准才接受 `status=USER_APPROVED`；示例 `{"schema":"manual_quote_limited_permit_v1","status":"EXAMPLE"}` 永不放行。不得改旧许可证复用；用户许可不是上游授权，明确禁止或需要绕过访问控制时停止。

## 获批后才运行

在批准的固定工作树根目录，确认 `overnight_quant/` 代码无本地修改；许可文件放在代码目录之外，使用既有 Python 环境和独立审批锚点。占额与在途状态与同一工作树的旧真实观察共用，不复制或改名控制目录，不以另一工作树重新使用同一许可。

```powershell
$env:OBSERVATION_QUOTE_NETWORK_ENABLED = '0'
$env:OBSERVATION_LIMITED_QUOTE_ENABLED = '1'
$env:OBSERVATION_LIMITED_QUOTE_PERMIT_FILE = '<真实许可绝对路径>'
$env:OBSERVATION_LIMITED_QUOTE_PERMIT_SHA256 = '<独立记录的许可SHA-256>'
D:\A-stock\.venv\Scripts\python.exe -m streamlit run overnight_quant/ui/dashboard.py --server.address 127.0.0.1 --server.port 8518 --server.headless true --browser.gatherUsageStats false --server.fileWatcherType none
```

1. 在 `http://127.0.0.1:8518` 选择「历史/离线观察 → 有限真实观察 → 真实历史材料」。核对时间、许可集合和剩余额度。确认选择后点一次「刷新一次报价」；普通浏览、条件、跟踪、比较不发请求。
2. 第一份必须确认成功、完整且 60 秒内；至少间隔 10 秒，再以同一集合人工点第二次。价格不变也是有效结果。失败、状态未知、陈旧、身份错误、清理阻断、过期或额度不足，立刻停止。不补请求、重试或跨日。
3. 在另一 PowerShell 用下方命令持久关闭该许可，随后停止本专用页面，在启动终端将新开关置 `0`。先比对独立记录的许可锚点；把两份确认文件原始字节的 SHA-256 单独记录，再离线逐批重放。关闭不影响历史材料。

```powershell
D:\A-stock\.venv\Scripts\python.exe -m overnight_quant.data.quote_observation_permit close --permit-file '<真实许可绝对路径>' --permit-sha256 '<许可锚点>'
D:\A-stock\.venv\Scripts\python.exe -m overnight_quant.data.quote_observation_permit verify --batch '<批次ID>' --permit-sha256 '<许可锚点>' --confirmation-sha256 '<该批确认锚点>'
```

材料位于 `overnight_quant/data/cache/offline_observation/offline_price_observation_v1/historical/manual_quote_limited_live_v3/`；许可与占额留在共享控制目录。持久关闭后不能重开同一许可。每次一条腾讯 quote、HTTP 5 秒、执行 10 秒、最终确认另计。请求后状态不明不能退还额度；部分原件可留存但不可作为成功重放。旧 v1 历史和 v2 test-only 合同不变。正式记录、研究推荐、交易输出均为空。
