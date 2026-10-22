# 分钟候选审计的版本化前置入口

## 本轮边界与修复

代码起点为PR #39合并SHA `6b7cd3d9e325ce1fe2816e42186962128e20a69f`。10月21日原NO_GO及外部脚本原件永久保留，不改判；本轮零行情/真实NTP、零任务，不恢复原授权。新入口审核合并及CI满足后，只有当时有效的单日批准与全部前置仍成立才可独立新批次执行；旧NO_GO本身不要求机械跨日等待，也不提供合并批准。

唯一生成入口：`overnight_quant/scripts/run_minute_capture_preflight.py`。不依赖报告目录下的Python脚本；默认/`--validate-only`零子进程零联网。显式`--inspect-environment`及`--prepare`只启动仓库环境检查，查询本机状态，不获取日历/报价或测NTP，更不启动捕获。

- `minute_capture_preflight_v1` / `minute_capture_preflight_verifier_v1`。
- `minute_capture_environment_v1`保留既有环境字段校验；在原脚本中使用显式可选minute模式，旧行业调用行为不变。
- `minute_capture_manual_authorization_v2`是**另外由人工批准的输入**；前置工具不生成它。捕获消费者只接纳该版，旧v1授权/外部行业预检不能授权新的分钟采集。历史分钟原件verifier未变。
- 冻结计划原字节、内容hash、四波20bars/0逐笔、节点、串行、整波2000ms和共享500ms清理均不变。

## 检查与证据

日期不写死，必须等于北京时间当天；批准SHA必须与真实Git HEAD一致且工作区/暂存区干净。计划原文件外部SHA与全部冻结字段同时核对，客户端版本须匹配计划。环境匹配实际分钟CLI/module、源worker及其仍可追踪的后代；无关Python不阻断。重复任务、执行锁、异常协调日志、cleanup-incomplete或当日目录占用均拒绝，不删除或重置。

复用已完成日历校验与腾讯session独立verifier。时钟直接解析原始`w32tm /query /status /verbose`和既有`stripchart /rdtsc`三行原始输出：Running、Leap=0、Last Sync Error=0、成功同步≤3600秒；三样本偏差各≤2000ms、末样本≤600秒。Hold不单独拒绝。服务明确异常先拒绝，不用样本覆盖；FILETIME转换保留原精度，来源、解析出的RTT和同源局限均保存，不额外测量。环境基线和启动复核之间需同服务PID/系统启动身份、同同步源且没有已记录的校时/休眠唤醒事件。

每次新目录只允许创建一次；intent、原生环境JSON、原始clock输出、查询stdout/stderr/退出码/PID/清理诊断、派生前置与外部SHA均保留。独立验证重读原文件锚点、重算12项检查与派生内容；仅重签内部hash不能改变结论。解析/权限/子进程异常、超时或缺失均有具体NO_GO原因。环境查询进程使用既有有界执行器的独立10秒IO/500ms清理上限，不占用、不改变行情整波2000ms。查询清理不确认时不得据此启动采集。

## 可用命令与操作顺序

本轮实际可执行的零联网演练（合成/test-only，不是交易日证明）：

```powershell
$py = 'D:\A-stock\.venv\Scripts\python.exe'
& $py -m overnight_quant.scripts.run_minute_capture_preflight --validate-only
& $py -m pytest overnight_quant/tests/test_phase108_minute_preflight.py -q -k actual_entries_end_to_end
```

以下用于另获当日许可、固定审核合并SHA后的人工准备。变量是操作输入，不是新启动器；目录必须未占用。不得把本轮开发SHA或10月21日旧授权自动当作未来许可。

```powershell
# $day、$code是实际批准的日期/代码SHA；$plan是原冻结计划文件。
$planSha = (Get-FileHash -LiteralPath $plan -Algorithm SHA256).Hash.ToLowerInvariant()
# 1. 测量前只读环境基线；不测NTP。
& $py -m overnight_quant.scripts.run_minute_capture_preflight --inspect-environment `
  --date $day --approved-code-sha $code --plan $plan --expected-plan-sha256 $planSha --output-dir $baseline
$before = Join-Path $baseline 'environment-query.json'
$beforeSha = (Get-FileHash -LiteralPath $before -Algorithm SHA256).Hash.ToLowerInvariant()
# 2. 由已批准的原入口取得当天calendar/session及原始三样本文件。
# $calendar/$session/$clockRaw是这些真实原件；不复制历史或合成文件。
$calendarSha = (Get-FileHash -LiteralPath $calendar -Algorithm SHA256).Hash.ToLowerInvariant()
$sessionSha = (Get-FileHash -LiteralPath $session -Algorithm SHA256).Hash.ToLowerInvariant()
$clockSha = (Get-FileHash -LiteralPath $clockRaw -Algorithm SHA256).Hash.ToLowerInvariant()
# 3. 启动前生成新复核材料，自动检查自基线以来的事件和身份。
& $py -m overnight_quant.scripts.run_minute_capture_preflight --prepare `
  --date $day --approved-code-sha $code --plan $plan --expected-plan-sha256 $planSha `
  --calendar $calendar --calendar-sha256 $calendarSha --session $session --session-sha256 $sessionSha `
  --before-environment $before --before-environment-sha256 $beforeSha `
  --clock-raw $clockRaw --clock-raw-sha256 $clockSha --output-dir $launch
$preflight = Join-Path $launch 'preflight.json'
$preflightSha = (Get-FileHash -LiteralPath $preflight -Algorithm SHA256).Hash.ToLowerInvariant()
& $py -m overnight_quant.scripts.run_minute_capture_preflight --verify $preflight `
  --expected-file-sha256 $preflightSha --date $day --approved-code-sha $code
```

生成成功为`MINUTE_PREFLIGHT_PASSED_NOT_AUTHORIZED`；独立核验通过为`MINUTE_PREFLIGHT_VERIFIED_NOT_AUTHORIZED`，两者sampling_authorized始终false。无真实PM v2单日授权文件，捕获仍拒绝。由人工另外提供的v2文件需绑定该preflight的路径/SHA及批准的date/code/plan/single_use；不能从成功预检反推PM许可。

获准采集后仍只使用原`run_minute_capture_audit.py --network`入口及原计划，见[完整捕获命令](v050_minute_capture_audit.md#完整命令)。消费者在占用单次intent之前验证v2材料；第一波实际派发再检查600/3600秒有效期。原子intent处理并发竞争，拒绝材料零capture worker。无原件不补造evidence；观察稳定、采集成功、完整性与来源资格分别判断。

## 离线验收与停止

### PowerShell文件哈希兼容热修复

PR #40合并后实际Python子进程为Windows PowerShell `5.1.26100.9711`，路径为`C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`。继承的模块搜索路径优先包含运行时Core版Utility 7.0，后有Windows Desktop版3.1；真实`Get-Command Get-FileHash`返回CommandNotFoundException。直接从PowerShell调用的查询则可发现Desktop命令，不能用该状态代替Python派生路径。原始诊断、异常及两批NO_GO保存在仓库外`local-archive/recovery/v050-preflight-hash-compat-20261021`和既有审计索引；本次不修改任何PATH、PSModulePath、执行策略或系统配置。

环境脚本改用局部`Get-FileSha256`：.NET FileStream流式读取原始文件字节，SHA256输出64位小写hex；文件缺失、共享读取/权限失败或算法失败均抛错，finally释放算法和流。不用空hash补齐，不跳过日志检查；锁仍保全并拒绝，pending/损坏/未知日志仍保全并拒绝。成功环境schema与既有验证规则不变；失败回执增加inspection_succeeded=false和service_check_status，服务尚未查询或查询失败时service_running=null，只有实际Get-Service结果才可为true/false。检查异常始终拒绝，不能把未知状态当通过或当作真实停服。

真实PS5.1/7子进程在隔离harness中屏蔽Get-FileHash，执行实际环境脚本。哈希单测从实际脚本提取同一函数，与Python hashlib独立核对空文件、二进制及非ASCII路径，并验证句柄释放；正常/空目录、pending、损坏/空/二进制日志、锁、文件缺失/拒读及服务查询异常分别覆盖。合成设施只在独立临时目录mock服务/任务/事件，不访问行情或NTP、不写真实协调日志。哈希计算成功不授予预检、PM授权、来源资格或采集许可。

Phase109新增36项，连同Phase108/104/107为185 passed in 116.17s。原正常日志反例2项失败和新测试二进制case ID过长的设施错误均保留；短ID修正不改变测试输入或行为。Python编译/diff及实际只读入口、Draft PR最终CI与恢复SHA索引位于同一仓库外恢复目录，不重复本地全量、不以开发环境只读成功授权捕获。

修复提交后的真实入口仅执行一次：环境查询退出0，两份真实日志均哈希成功、合法且非pending，无workers/tasks/locks/blockers；但完整前置仍以clock_status_query_failed拒绝，因为内层clock_query.exit_code为null。原始W32Time内容显示服务Running、Leap=0及同步错误0，不能用正文代替缺失的进程退出码。该独立兼容阻断保全报告，本轮不扩大哈希修复、不追加真实检查或采集；没有日历/session/NTP测量、捕获worker、任务或资格增加。

### PR #41退出码跟进

独立跟进授权下，使用本地cmd退出7复现PS5.1：不预取Handle时WaitForExit后的ExitCode为空，Refresh不能补回；预持Handle后正确为7。PS7三种路径均为7。检查脚本在有界等待前取得原生Handle，读取真实ExitCode及重定向文件后再Dispose；不把null转0、从正文猜成功或改系统配置。

本机查询保持5秒总预算，单调计时含启动/等待/读取，预留内部500ms用于Kill后的有界退出确认；整体环境10500ms与捕获2000ms均不变，隐藏窗口不变。Phase110实际PS5.1/7→本地Python fixture覆盖0/7、超时/回收、完整stdout/stderr、启动失败及null继续拒绝；所有输入明确合成，零时钟服务/NTP/行情请求。PS7 fixture参数以-u开头导致的绑定错误已保留并修正，不增加生产绕过开关。

旧Phase103 CI失败在coordination_wait、无客户端调用；缺历史持锁者证据，精确根因仍未确认。最多两轮本地诊断，不放宽成功用例或计时；仅增失败时PID/阶段剩余预算/同批peer/回收后的协调日志摘要，不声称收尾快照证明历史锁归属。最终专项、一次真实环境查询和新增一次CI结果以`local-archive/recovery/pr41-exitcode-ci-followup-20261021`为准。若CI再失败报告停止，不重跑求绿；通过也不把未知根因写成已修复。

Phase108实际入口贯通：生成CLI→独立verifier CLI→捕获CLI→4个真实fixture子进程/20个合成bars调用。替换仅在测试harness，不给实际脚本增加skip、任意worker命令或test-only公共CLI开关；真实准入拒绝test-only材料。另以Windows PowerShell5.1/7执行真实环境脚本的隔离mock行为测试，覆盖脚本/带空格路径/`-m`及紧凑模块启动、残留根/后代、无关Python、名称不含AStock的分钟任务、历史Disabled任务与权限拒绝。模块/任务漏识别先复现6项拒绝断言失败，再修复；最终新阶段62项与旧编排59项共121项通过，较早兼容组213项与之重叠，不相加。

错误日期/SHA/计划、缺失/过期材料、时钟失败、清理/当日输出阻断、异常退出/非法或缺失JSON、超时回收均拒绝；拒绝结果交给捕获CLI仍不得派发worker。外部SHA错误、重签派生内容、旧授权与合成提权均拒绝。没有真实行情或NTP，不表示生产最坏耗时、最终性或来源资格得到证明。

下一步为Draft PR审核及受控合并；本轮不合并、不发布、不建任务。qualification=0/3，data_ready=false，hard_gate_authorized=false；records/research_ranking/candidates/tickets/orders=[]。
# Explicit Local-Preparation Profile

The legacy default remains unchanged. The unactivated
[MINUTE-OPS-3 candidate](minute_ops3_local_prepare.md) explicitly selects
`--execution-contract local_prepare_v2` for environment inspection, preparation
and independent verification. It requires its new plan, v2 preflight and v3
manual authorization; passing preflight never grants PM approval. Do not mix
legacy authorization or plans with that path, or treat these commands as a live
date authorization.
