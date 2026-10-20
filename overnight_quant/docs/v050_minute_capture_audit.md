# v0.5 真实分钟专项取证准备

## 范围与来源依据

仅新增 audit 捕获工具，不是 Provider、分钟桥接 profile、来源资格或正式快照。固定节点 `59.36.5.11:7709`，五股 `000001,000333,600000,600519,601318`，mootdx 0.11.7 / tdxpy 0.2.7；源版本保持 `mootdx_0.11.7_tdx_std_bars_1m_v2026-07-31`，捕获合同独立版本化。

| 依据层次 | 本轮结论 |
| --- | --- |
| 提供方官方定义 | [通达信 get_market_data](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1ctuhthaq5qmg/mindoc-1h10g60jt68sc.html)说明该API的量/额单位，但不是当前TCP bars；不得移用其“万元”或其他字段语义。未取得当前端点的区间/累计、标签和修订最终性定义，均UNPROVEN |
| 实际客户端实现 | [mootdx v0.11.7](https://github.com/mootdx/mootdx/blob/v0.11.7/mootdx/quotes.py)的bars调用get_security_bars；本地tdxpy 0.2.7解码vol/amount及HH:MM。实现声明不是上游语义证明，不能由数值比例或趋势推单位 |
| 已有实测一致性 | 直接引用已批准的三日单标签归因与100倍成交量换算，不再扫描或重算。它不证明其余11行及分钟成交额，逐笔没有原生成交额，禁止price×volume回填 |

公开材料原件、版本固定源码、读取时间及SHA：仓库外 `local-archive/recovery/v050-minute-capture-tool-20261020/source-basis.json`；先前缺口结论引用既有索引，不重复复制或调查。

## 工具实际保存

- `run_minute_capture_audit.py` 默认/validate-only零子进程、零来源网络；真实入口须显式`--network`、外部SHA锚定冻结计划及单日PM授权。没有获批日期，不具备执行授权。
- 每波一个可终止进程，固定五股顺序调用bars；保存每次所有原生DataFrame字段、非目标/其他日期行、请求参数/hash、客户端调用边界、解析前响应体base64/hash、解析入口接收时间、读取完成时间和原生来源版本。body边界为**tdxpy收到的解压后协议响应体**，不是声称测得线上数据包发出/到达时间。
- 原生标签不改名、不补秒；候选窗口仅按原生end标签14:39–14:50列视图，对应的实际`[14:38,14:50)`语义仍待证明，不生成interval转换或正式records。所有未知字段/缺失值原样保留，不补零，不推算成交额。
- 每行分别保存观察hash、变化次数、连续相同次数；沿用至少3次相同的观测稳定规则。只标记observed_stable，最终性始终UNPROVEN/is_final=false，不把最后观察当最终，不注册真实profile，不输出研究排名。
- stdout逐响应刷新，超时杀树后保留已经收到的回包和失败流；同日intent、每波原件、evidence、外部SHA锚点、seal-receipt均原子不可覆盖。日期目录也是永久单次启动凭据，不自动删除/重置失败锁。只写ignored `overnight_quant/data/cache/minute_capture_audit/`；test-only写隔离测试目录。
- 完整性verifier通过可以对应失败采集，不能升级来源资格。封存完成与source available_at分开；迟封存单独返回失败，不回填较早available_at。离线双重放发生在封存之后，不倒写成采集前可用。

## 无日期冻结候选计划

| 阶段 | 请求、依赖与最坏预算（待审核，不是执行许可） |
| --- | --- |
| 前置 | 人工批准日期/代码SHA/计划hash；当日日历、session、环境与时钟预检必须先核验，不占用到点后的工作预算。计划文件自身trade_date=null/execution_authorized=false |
| 四波采集 | 14:49:55、14:50:05、14:50:30、14:50:55；每波5次bars，共20次业务请求，最多4次固定节点建连。start=0/offset=800、每股仅一页；缺窗口或需要额外页即拒绝，不自动扩页 |
| 并发与节点 | 全局1worker、同节点1个在途调用，前一调用完整返回后才下一次；不另加TCP间隔，不把东财500ms规则当作mootdx已批准规则。关闭bestip/heartbeat/auto_retry，无fallback |
| 2000ms整波 | 1500ms进程启动、导入、连接、排队、全部五次调用及读取/正常退出；500ms在同一2000ms内预留终止清理。每个单请求上限2000ms，但同时服从更小的整波剩余预算，不能按5×2000ms相加。到点前未准备好即减少剩余预算，耗尽零派发 |
| 发布/检查 | 每波最多2000ms保存全回包及检查窗口，另有实际耗时回执。最短两波间隔10秒，worker2秒+发布检查2秒，余6秒；不承诺原生响应一定在预算内完成，超时即失败 |
| 最后一波后 | 最晚14:50:57进程退出；至14:51:05的8秒中，发布检查2秒、最终确认2秒、证据与外部锚点封存3秒、调度/回执余量1秒。各阶段计时并拒绝超预算；真实冷启动/网络/磁盘最坏表现仍未被本轮证明 |
| 逐笔 | **0次**。本轮问题首先是分钟全行丢失及缺端点语义；逐笔无原生成交额，重复请求不能解决它。以后若有具体可回答的缺口，需另冻分页/总数，不在失败后补采 |

原14:51:05才发起必要末次观察与同刻完成截止冲突。本候选仅前移末次观察，不改feature cutoff14:50、collection deadline14:51:05、decision14:51:10、execution边界14:52或旧规则。超2000ms的清理/OS异常仍保全证据并判失败，不称按时成功；父进程封存迟到不能取得正式准入。

## 完整命令

仅准备冻结计划（本轮允许且零联网）；目标路径必须不存在：

```powershell
$py = 'D:\A-stock\.venv\Scripts\python.exe'
Set-Location 'D:\Projects\A-stock-tail\worktrees\active\A-stock-v043-eastmoney-t2'
& $py overnight_quant/scripts/run_minute_capture_audit.py --validate-only --plan-output overnight_quant/data/cache/minute_capture_audit/candidate-plan-v1.json
Get-FileHash overnight_quant/data/cache/minute_capture_audit/candidate-plan-v1.json -Algorithm SHA256
```

下面只供**下一次另行批准后**人工使用，本轮不填入具体日期或执行：

```powershell
& $py overnight_quant/scripts/run_minute_capture_audit.py --network --date '<PM批准YYYY-MM-DD>' --plan overnight_quant/data/cache/minute_capture_audit/candidate-plan-v1.json --expected-plan-sha256 '<计划文件SHA256>' --authorization '<已审核单日授权JSON路径>' --authorization-sha256 '<授权文件SHA256>'
& $py overnight_quant/scripts/run_minute_capture_audit.py --verify 'overnight_quant/data/cache/minute_capture_audit/<批准日期>/evidence.json' --expected-file-sha256 '<采集原件外部SHA256>' --replay-output '<新的ignored审计目录>/replay-a.json'
& $py overnight_quant/scripts/run_minute_capture_audit.py --verify 'overnight_quant/data/cache/minute_capture_audit/<批准日期>/evidence.json' --expected-file-sha256 '<同一原件外部SHA256>' --replay-output '<新的ignored审计目录>/replay-b.json'
```

授权JSON合同：schema=`minute_capture_manual_authorization_v1`、status=`PM_APPROVED_SINGLE_AUDIT`、trade_date、code_sha、plan_hash、single_use=true；calendar/session/preflight/environment分别为`{path,sha256}`。工具复用现有双合同verifier、环境及PRE_LAUNCH_GO检查，要求固定代码与干净工作区。实际人工预检逻辑仍须审阅；SHA一致只证明文件身份，不能替代逻辑审核，不新增外层启动脚本。合成harness不能充当这些真实输入。

下一轮最多证明该日原生响应、窗口覆盖、实际变化/稳定性与时间预算表现；不能凭单轮采集、hash或连续相同证明上游最终性、取得资格、激活Provider或宣称真实策略就绪。资金流冻结及市场/行业/公告/风险阻断不变。正式qualification=0/3、data_ready=false、hard_gate_authorized=false，records/ranking/candidates/tickets/orders=[]。
