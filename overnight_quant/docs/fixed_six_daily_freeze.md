# 固定六请求日线冻结入口 v1

只支持腾讯完成日历1次和固定五股qfq各1次。默认零网络；不调用旧全来源验证、
报价/session、新闻、公告、分钟或资金流。未接入生产Registry、旧策略或资格台账。
`source_use_basis=UNKNOWN`仍为独立阻断；PM批准不等于上游使用权，亦不据缺书面回复推断禁止使用。
11月3日仍只是待批草案，本次没有生成授权、任务或采样安排。

## 身份与预算

- 执行/验证合同`fixed_six_daily_freeze_v1`，许可`fixed_six_daily_authorization_v1`，
  请求清单和预算由`overnight_quant.data.daily_freeze.PLAN/BUDGET/IDENTITY`固定。
  冻结输入复用`completed_daily_frozen_batch_v1`，不改变观察公式合同。
- 股票固定000001、000333、600000、600519、601318；串行，禁止重试、fallback及HTTP重定向。
  专用urllib opener禁用环境代理和重定向，3xx作为实际响应封存后拒绝，不跟随Location。
- 单请求绝对10秒：最多9秒启动/连接/读取/原始字节封存，剩余最多1秒共享进程树/管道清理。
  这是新HTTP合同内部分配，不借用TCP的1500/500ms。启动、依赖导入和写入均不移出预算。
- 整批90秒从入口执行开始计时：协调worker最晚85秒停止工作，最多2秒共享回收，
  剩余到90秒用于执行回执、独立验证、观察文件及最终外部锚点；不为步骤重新给90秒。
  每次派发前须覆盖所有未执行请求各10秒及10秒收尾余量，否则零新增派发；不依赖实测可能更快放行。
- 复用`run_probe_worker_process(... bounded_cleanup_ms=..., hard_deadline_monotonic=...)`，
  正常退出、stdout/stderr、进程树及管道均须确认。清理不明写持久阻断并保留active.lock；
  不自动删除或解除。正常确认后只释放本控制器拥有的活动锁。
- 单调时钟执行截止；+08:00时间仅审计，日期须为实际运行日，不新增分钟同步/NTP/session门槛。
  网络和文件系统可能失败或迟返，截止检查不把迟返判成功；离线速度不保证实网可行性。

## 许可与输出

真实执行前必须校验许可外部SHA、PM来源、日期、固定代码SHA、干净Git、用途依据、固定五股、
执行合同/预算、single_use、batch_id和独立输出绝对路径。created_at≤实际启动≤expires_at，
到期日须为R；每个请求子进程再次检查。只有预检字段而无PM许可不能派发。
batch_id先确定，原子独占目录/活动锁及intent在网络前持久化；重复/并发不得再请求。
运行许可没有自动生成器；本PR不含任何有效联网许可。

每项请求独占`request-N.json`；响应先写`raw-N.json`，保存原始base64、SHA、字节数、参数、
来源版本、HTTP状态/头、最终URL、真实请求起止及单调阶段时间。
写完后另写`raw-written-N.json`作为完成观察，不把写入开始时间当完成时间。
无响应的网络异常不造raw，父进程诊断/stdout/stderr仍保存。任一解析/覆盖/封存失败立即停后项。

六项通过后写并重读`source.json`，再取真实frozen_at，验证内存清单后原子发布`manifest.json`。
`capture-complete.json`记录清单写完后时间，`execution.json`记录真实进程回收结论。
失败保留既有原件/部分材料；不覆盖成功文件或补旧批次。若全局截止或清理失败，
已有文件不作为成功执行依据，必须使用最终外部锚点重放；不能只读取生产者success标志。

父控制器异常回执按派发边界区分：尚未进入协调worker调用为`REQUEST_REJECTED`、
`requests_dispatched=0`及`KNOWN_ZERO_BEFORE_DISPATCH`；一旦尝试派发则为`CONTROLLER_FAILED`、
`requests_dispatched=null`及`UNKNOWN_AFTER_DISPATCH`，不推断实际HTTP请求数或worker是否已启动。
保存原始error、failure_stage及已返回的process诊断；未取得清理回执时cleanup_complete=null。
在原90秒剩余预算内仅尝试一次不可覆盖的`controller-failure.json`；返回值只在写完后标记
failure_receipt_persisted=true。写失败或预算耗尽明确报告，文件本身不自证已落盘；
后续guard/锁异常不能掩盖首个错误，未确认清理仍保留活动锁，不增加预算或解除阻断。
此项为异常报告修复，不改变请求计划/许可/源件/观察合同身份，不改写旧失败材料或请求计数。

最终锚点包含全部材料文件SHA、源文件/清单/intent/执行回执SHA和全部发布及重读后的单调完成时点。
独立验证核对完整文件集合、原始字节、六请求计划/批次/版本、父Provider与HTTP时间顺序、
单调绝对截止、源件重放、60日完整覆盖及最终执行状态；重新调用既有R20观察逻辑。
合法失败材料可完整性通过，但执行仍失败，排序为空。缺件或篡改不能获得完整性成功。

## 实际仓库命令

在固定工作树根目录执行；默认无需任何输入，零worker/零网络：

```powershell
& 'D:\A-stock\.venv\Scripts\python.exe' -m overnight_quant.scripts.run_daily_freeze
```

未来真实执行命令形态（**没有当前许可，不可据此执行**；参数必须来自另批文件）：

```powershell
& 'D:\A-stock\.venv\Scripts\python.exe' -m overnight_quant.scripts.run_daily_freeze `
  --network --date <批准的R> --codes 000001,000333,600000,600519,601318 `
  --approved-sha <审核合并SHA> --authorization <单日许可.json> `
  --authorization-sha256 <许可文件SHA> --output <全新ignored-cache绝对路径>
```

许可必需字段：schema_version/status/single_use/run_date/approved_sha/contract_hash/output/codes/budget/
batch_id/created_at/expires_at/pm_source/source_use_basis。精确值来自版本合同，不接受UNKNOWN用途依据，
不把此字段检查宣称为法律授权验证。本轮不制造模板批准记录。

完成stdout包含anchors；由操作者原样保存到**批次目录之外**并独立计算该文件SHA。
验证入口零网络，不调用生产者计算结果：

```powershell
& 'D:\A-stock\.venv\Scripts\python.exe' -m overnight_quant.scripts.run_daily_freeze `
  --verify <批次目录> --anchors <外部锚点.json> --anchors-sha256 <锚点文件SHA>
```

两次验证stdout可逐字节比较；不向不可变批次目录写入replay，原件SHA须前后不变。
`--test-fixture <本地合成响应.json> --test-batch-id <标识>`只允许与network互斥，
配合显式日期/codes/SHA/output运行真实子进程；使用隔离state且所有结果标记synthetic_test_only，
不能授予生产资格。没有默认fixture、demo或自动fallback。

## 停止边界

`observation_input_ready`仅观察输入，风险恒NOT_ASSESSED；D为有效日历中R前最新完成日，
每股60日，20交易日回溯需要21个收盘。不得退到更旧共同D或用R日日线。
不评风险/收益/PIT有效性，不模拟成交。qualification=0/3，data_ready=false，
hard_gate_authorized=false，正式records/candidates/tickets/orders始终为空。
