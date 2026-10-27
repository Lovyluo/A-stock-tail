# MINUTE-OPS-6：有限差分协议诊断

## 结论与边界

10/26客户端socket边界收到的bars响应，头声明体长与实收均2字节；原始体与解析器输入均`20 03`。原解析器把前缀按小端记录数读为800，随后无法读出记录所需日期字段。响应头不是“声明800条”，短体形成原因仍UNKNOWN，不认定服务器故障。失败路径缺少成功冻结标记使独立成功时序核验false，不改写为请求超时或放宽成功判定。

只读核对原件及已发布事实文件：四个头均16字节、32个十六进制字符，原bars头`b1cb74000c01086401002d0502000200`完整，没有摘要漏字。核对记录在仓库外`local-archive/recovery/minute-ops6-20261026/header-readonly-verification.json`，原件/失败intent不改写，不重做整批历史hash核验。

新模式仅显式选择`--differential`；旧单次协议诊断及四波800条计划默认不变。固定59.36.5.11:7709、frequency=1m/start=0，仅证券/请求条数按下表变化。count=1只诊断协议，不替代正式800条请求，不用于窗口、特征、选股或来源资格。

## 冻结待审计划

下列时刻是待审核的独立目标时点，**无获批日期，无当前联网授权**。四项是预先指定的独立实验，不是失败重试/fallback；独立进程、连接、三次必要握手，不跨项复用。

| 项 | 股票/市场 | count | 本地准备 | GO目标T | 最晚清理 | 按原预算封存上界 |
| --- | --- | --- | --- | --- | --- | --- |
| A | 000001/深圳 | 1 | 14:47:50 | 14:47:55 | T+2000ms | 14:48:00 |
| B | 000001/深圳 | 800 | 14:48:50 | 14:48:55 | T+2000ms | 14:49:00 |
| C | 600000/上海 | 1 | 14:49:50 | 14:49:55 | T+2000ms | 14:50:00 |
| D | 600000/上海 | 800 | 14:50:50 | 14:50:55 | T+2000ms | 14:51:00 |

- 最多4次bars、12次握手、0逐笔；无其他股票、节点、count、扫描、重试或fallback。
- 每项T-5纯本地准备，3500ms工作＋共享500ms失败清理，T-1前完成READY；准备禁止DNS/连接/握手，单次GO后连接/握手/读取/取证输出/正常退出严格早于T+1500ms，清理严格早于min(T+2000ms,完成+500ms)。最长驻留7秒另记，封存仍3000ms且14:51:05截止。
- 60秒项间距使前项封存上界T+5与下项准备T+55之间留50秒，容纳既有两次Git查询各5秒、10500ms环境查询及本地前置/文件校验；文件系统开销不保证最坏值，仍须在T-5前完成，否则停止，绝不重置绝对截止。相邻交易状态变化是差分解释的局限，不作因果证明。
- 同一单日矩阵有一个原子single-use intent，各项另有独立single-use intent/批次、原件/回执/完成见证和外部SHA。旧单次、旧四波、矩阵各项共享清理保护；旧或损坏未完成材料保全，不删除/改名/绕过。
- 只有成功，或**完整封存且确认清理的原解析器拒绝**可以进入下一独立实验；失败仍FAILED，不把成功冻结标记补true。前置新鲜度在继续决定和下一项派发/GO再次验证；超时、缺件、未确认清理/封存、错身份、前置失效均停止剩余项。

## 版本、入口与授权

矩阵plan/authorization/result v1，各项独立plan/authorization/evidence/verifier/preflight v1；执行身份严格为`protocol_differential_A_v1`至`protocol_differential_D_v1`。没有任意参数或生产注册入口。

```powershell
$py = 'D:\A-stock\.venv\Scripts\python.exe'
$repo = 'D:\Projects\A-stock-tail\worktrees\active\A-stock-v043-eastmoney-t2'
$cli = Join-Path $repo 'overnight_quant/scripts/run_minute_protocol_diagnostic.py'
$pre = Join-Path $repo 'overnight_quant/scripts/run_minute_capture_preflight.py'
$ops = '<新的、不可覆盖、隔离audit目录>'
# 本轮唯一允许的实际模式：零网络、零worker；产生矩阵/四项计划与五份DRAFT。
& $py $cli --differential --validate-only --plan-output (Join-Path $ops 'matrix-plan-v1.json') `
  --authorization-draft-output (Join-Path $ops 'matrix-authorization-draft-v1.json')
```

内容hash与原始文件SHA分别记录，所有草案date/target/批准字段为空或DRAFT，不能编辑一个status就声称许可。本轮实际文件及双hash在仓库外增量索引；不生成批准日期或有效联网授权。

未来另获单日PM批准并固定审核SHA后，依次为四项使用仓库内前置入口（只读环境、当日真实双合同、同一组获准新三样本、独立verify），不新增外层启动器。单项格式如下，`$key`只能取A/B/C/D，目标取冻结表，真实输入不得用旧日或合成内容：

```powershell
# 本轮不运行。以下全部变量由未来具体日期的真实授权及材料决定。
& $py $pre --prepare --execution-contract "protocol_differential_${key}_v1" `
  --date $approvedDate --target-at $frozenTarget --approved-code-sha $approvedCode `
  --plan $leafPlan --expected-plan-sha256 $leafPlanSha --output-dir $newPreflightDir `
  --calendar $calendar --calendar-sha256 $calendarSha --session $session --session-sha256 $sessionSha `
  --before-environment $before --before-environment-sha256 $beforeSha --clock-raw $clock --clock-raw-sha256 $clockSha
& $py $pre --verify $leafPreflight --expected-file-sha256 $leafPreflightSha `
  --date $approvedDate --target-at $frozenTarget --approved-code-sha $approvedCode `
  --execution-contract "protocol_differential_${key}_v1"
& $py $cli --differential --network --date $approvedDate `
  --plan $matrixPlan --expected-plan-sha256 $matrixFileSha `
  --authorization $separatePMMatrixAuthorization --authorization-sha256 $PMMatrixAuthorizationSha
```

矩阵批准文件必须schema=`protocol_differential_authorization_v1`、status=`PM_APPROVED_SINGLE_DIFFERENTIAL_MATRIX`、日期/代码/矩阵双hash/single_use/明确PM来源/共同matrix_authorization_id，且只含顺序A–D的四项计划/批准文件外部SHA。四份批准各用`PM_APPROVED_SINGLE_DIFFERENTIAL_EXPERIMENT`，绑定对应计划双hash、真实preflight、同一配置、同日期/代码/PM来源/矩阵ID及唯一目标时点。旧授权不可迁移使用；前置通过不产生批准。

实际intent固定在ignored `minute_protocol_differential/<日期>/`，各项材料固定在`minute_protocol_differential/<A-D>/<日期>/`。新worker还须验证父矩阵授权/intent及所有启动绑定；不能直接调用内部worker跳过前置。

各项原件/封存回执/完成见证分别SHA锚定，矩阵结果引用全部锚点，独立重放重算每项结果及继续/停止决定。合法失败可完整性通过，整体诊断仍失败；不把四项材料完整称为四项请求成功。

```powershell
# 有原件后才运行两次，两个输出文件必须不同且不存在。
& $py $cli --differential --verify $matrixResult --expected-file-sha256 $matrixResultSha --replay-output $newReplayA
& $py $cli --differential --verify $matrixResult --expected-file-sha256 $matrixResultSha --replay-output $newReplayB
```

## 预先规定判读

| 观测 | 允许的结论 | 不允许的结论 |
| --- | --- | --- |
| 两证券小count成功、大count失败 | 支持请求规模相关假设 | 具体限制已确认；批准降低正式count |
| 仅一证券成功 | 支持证券/市场相关假设 | 市场映射或服务器根因已确定 |
| 四项同样两字节短体 | 无法区分节点/服务/协议兼容，停止该节点原样试采，交产品决策 | 原样继续采、重试或自动换源/升级依赖 |
| 任一/四项成功 | 仅本次指定诊断响应 | 五股预算、分钟语义、最终性或正式资格 |

离线fixture仅test-only，不产生真实来源裁决。ROOT_CAUSE_UNCONFIRMED、CAPTURE_BUDGET_FEASIBILITY_UNPROVEN、qualification0/3、data_ready=false、hard_gate_authorized=false，正式records、研究/交易输出为空。本轮不联网、不校时、不建任务、不合并、不发布。
