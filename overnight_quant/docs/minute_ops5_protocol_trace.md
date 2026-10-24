# MINUTE-OPS-5：有界协议取证草案

## 范围与身份

仅固定 `59.36.5.11:7709`、`000001`、`frequency="1m"/start=0/offset=800`；一次bars及原客户端三次必要握手，最多4组交互，无扫描、重试、fallback、逐笔或其他股票。

- 新计划/worker/evidence/verifier/授权分别为 `mootdx_protocol_diagnostic_*_v1`，执行合同 `single_bars_protocol_diagnostic_v1`。
- 与原四波20请求计划、授权、证据及来源资格隔离。日期和目标时点未指定；只生成 `DRAFT_NOT_AUTHORIZED`，不能生成PM批准。
- 本轮CLI仅冻结、审核和独立重放，**不提供 `--network` 或真实执行模式**。内部worker仅由隔离测试调度验证；今后真实操作仍须单独批准并审核实际运维入口/前置，不能临场直接调用内部worker。
- 授权绑定校验函数仅核对独立身份、日期/窗口、代码/计划及外部SHA；它不是前置逻辑验收或采集许可。旧四波授权、DRAFT及空值均不通过。

## 不改变接收行为

取证默认关闭，仅新诊断worker显式安装。原 `NativeClient`、依赖包、读取长度、分帧/解压/解析、返回值和异常均不改；所有hook在finally恢复，继承的方法恢复为继承关系。每组记录send的原参数/字节/返回长度，以及recv的请求长度、返回长度、单调起止、EOF/异常和实际分段字节。

调用前/返回后记录同一call index的生命周期，遇到阻塞或被终止时保留已派发信息，不将缺少返回解释为EOF。每组最多32个recv调用，不是32次重试；取证不增加任何send/recv。超限的停止是显式失败，不宣称等价成功。

响应头按实际第一recv分段保留：不足16字节即头不完整，声明长度/体归属标UNKNOWN，不为取证拼成依赖实际未消费的成功帧头。只有已捕获完整16字节头时，才按固定安装实现读取zip/unzip长度；前三个uint32不猜测命令意义。

原始接收体通过recv记录引用，标注“按头声明压缩/未压缩”，不额外解压；解析器输入单独保存字节/长度/SHA。组号只证明本地调用顺序，**不证明线上命令匹配或响应归属**。原样短体仍交原解析器并拒绝，不补字节、计数或价格。

## 冻结预算与上限

| 项目 | 上限/含义 |
| --- | --- |
| 交互/recv | 4组，每组32个实际recv调用 |
| send原始材料 | 每组4096字节；超限不派发该send |
| 接收材料 | 每组65535字节体＋16字节头；超限保存已接收前缀及不完整标识后失败 |
| bars解析器输入 | 65535字节；不完整材料不能冒充完整响应 |
| 总子进程输出 | stdout/stderr合计1MiB；worker预留32KiB失败终态，父进程可选管道保留上限防止依赖输出绕过，超限截留前缀并明确output_complete=false |
| 本地准备 | T-5启动，3500ms工作＋共享500ms失败清理，T-1前结束，零DNS/连接/握手 |
| 联网工作/回收 | 一次GO；连接、三握手、bars、复制/哈希/编码/输出及正常退出仍须T+1500ms；共享清理至T+2000ms，不重置截止 |

复用既有Windows Job、单调截止和有界管道回收；总驻留独立计时，最多7秒，不声称全部成本仍2秒。失败诊断可能只保留已到达前缀，不能据此升级执行成功；清理未确认时禁止后续执行，不删除或解除既有阻断。

## 零联网命令

使用当前已安装Python；输出文件必须不存在，计划内容hash与文件SHA分开记录：

```powershell
$py = 'D:\A-stock\.venv\Scripts\python.exe'
& $py overnight_quant/scripts/run_minute_protocol_diagnostic.py --validate-only `
  --plan-output '<新的仓库外目录>\protocol-plan-v1.json' `
  --authorization-draft-output '<新的仓库外目录>\authorization-draft-v1.json'
& $py overnight_quant/scripts/run_minute_protocol_diagnostic.py `
  --verify '<已捕获的独立诊断原件>' --expected-file-sha256 '<原件外部SHA>' `
  --replay-output '<新的不可覆盖replay文件>'
```

默认/validate-only零网络、零worker。重放只输出完整性与诊断结果，不表示PM批准或来源资格；合法短体失败可以完整性通过，但CLI仍退出2，不把重放一致升级为执行成功。缺失回执/部分输出不能形成完整诊断成功。

## 验收与限制

Phase114使用实际安装解析器和内存socket逐项比较OPS-4九类fixture：完整体、分段体/头、短体、声明长后EOF、正常压缩、错误解压长度声明、未知头前缀及越读。比较发送字节、recv调用序列、结果或原始异常；还覆盖材料/输出超限、写出失败、hook恢复、真实子进程READY/GO、超时/洪泛回收、最大材料、重签篡改和旧计划隔离。

固定有限启用/关闭对照和进程阶段耗时进入仓库外增量索引；合成材料不能证明真实TCP预算、上游语义或最终性。能区分声明长度、实际接收长度、解析器输入长度及本地异常阶段；不能仅凭这些记录认定服务器故障、源数据正确、线上包发出时刻或未识别头字段意义。

本机最终有限对照30对（九类＋最大材料各3对），额外耗时中位数0.141ms、最大1.405ms；最大留存原始材料计数131124字节、单次IO事件输出177132字节。这是显式字节计数，不是进程RSS或任意原生分配的峰值。最终真实子进程最大材料：准备819.37ms、GO后工作134.84ms（含父进程输出冻结/SHA0.38ms）、清理0.05ms、驻留5125.01ms，stdout总192335字节；洪泛准确截留在1MiB并失败。成功路径的流解码/哈希在工作截止前完成，失败部分材料处理计入剩余清理预算，不从计时中移除。专项73项、新增22项及最终预算回归6项通过，存在覆盖重叠不相加；完整原始结果/增量SHA见仓库外minute-ops5-20261024恢复索引。

保持ROOT_CAUSE_UNCONFIRMED、CAPTURE_BUDGET_FEASIBILITY_UNPROVEN，qualification=0/3、data_ready=false、hard_gate_authorized=false，正式records、研究排序及candidates/tickets/orders全空。历史失败、intent、原资格裁决不重判；不合并、采集、校时、安排任务或发布。
