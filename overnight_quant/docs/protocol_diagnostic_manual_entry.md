# 单次协议诊断人工清单（待单日审批）

仅离线交付；本文件不是执行许可，不预定日期。独立计划v2、授权v2、evidence v2/verifier v3、preflight v1；执行预算仍`single_bars_protocol_diagnostic_v1`，worker v1。旧v1/v2验证身份只历史审计，不选新路径。

## 固定范围与停止条件

- `59.36.5.11:7709`、`000001`、`1m/start=0/count=800`；一次bars＋三次必要握手，0逐笔。无重试/扫描/fallback，不接四波或资格入口。
- T-5启动纯本地准备，最多3500ms工作＋共享500ms失败清理，原T-1前READY；GO只一次。连接/握手/读取/取证/正常退出及父进程输出冻结严格早于T+1500ms，共享清理严格早于min(T+2000ms,完成+500ms)。总驻留独立记录，最长7秒；迟到不重置。
- 复用既有3000ms封存预算、14:51:05完成截止及独立完成见证；单调计时从worker的finished标记开始，含结束回执发布及父进程原件编码/hash，不留下预算外空档。目标为PM批准的当日+08:00时点，必须预留2000ms＋3000ms全预算，故不晚于14:51:00；这不是执行日期/T授权，真实TCP性能仍未证明。
- 当日已完成日历、腾讯五股session及独立SHA/验证，真实环境与唯一三样本材料必须完整；来源/日期/代码/计划/配置严格匹配。时钟仍Running、Leap0、错误0、同步≤3600秒、各偏差≤2000ms、GO时末样本≤600秒；Hold不单独拒绝。不校时或追加测量覆盖失败。
- 固定HEAD/干净工作区；无相关worker/任务、锁/异常日志、共享清理阻断、未配对准备回执、该日诊断输出/intent。异常保全并停，不删除/重命名/绕过。旧四波失败intent不覆盖；独立诊断只有自己的单次intent。
- 前置只生成`PASSED_NOT_AUTHORIZED`；授权草案始终`DRAFT_NOT_AUTHORIZED`。必须另有明确人类PM批准，绑定具体日期/T/代码、计划内容hash及文件SHA、配置、真实preflight外部SHA和single_use；不能把字段存在或文件hash相同当作实际前置通过。

## 实际命令

以下变量是待PM批准/当日真实生成的材料，不是模板证据；不得填历史或合成内容。唯一执行CLI是仓库内入口，不加外层启动脚本。

```powershell
$py = 'D:\A-stock\.venv\Scripts\python.exe'
$repo = 'D:\Projects\A-stock-tail\worktrees\active\A-stock-v043-eastmoney-t2'
$cli = Join-Path $repo 'overnight_quant/scripts/run_minute_protocol_diagnostic.py'
$pre = Join-Path $repo 'overnight_quant/scripts/run_minute_capture_preflight.py'
$ops = '<新的不可覆盖审计材料目录>'
$plan = Join-Path $ops 'protocol-plan-v2.json'
# 仅冻结草案，零子进程/零来源请求，不确定执行日期：
& $py $cli --validate-only --plan-output $plan `
  --authorization-draft-output (Join-Path $ops 'authorization-draft-v2.json')
```

计划内容hash读`plan_hash`并独立重算规范JSON摘要；文件SHA为原始字节SHA，两者不可混用。本次离线交付的实际双hash、代码SHA、材料SHA及恢复位置在外部`protocol-entry-20261024/technical-index.json`；采集前使用受控合并后的批准SHA，不盲目跟随main。

```powershell
# 以下路径/外部SHA来自当日真实材料；本轮不运行这些命令。
$day = '<PM批准日期>'
$target = '<同日T的完整ISO时间，含+08:00>'
$code = '<PM批准的固定合并SHA>'
$planSha = '<冻结计划原始文件SHA>'
& $py $pre --inspect-environment --execution-contract single_bars_protocol_diagnostic_v1 `
  --date $day --target-at $target --approved-code-sha $code `
  --plan $plan --expected-plan-sha256 $planSha --output-dir '<新baseline目录>'
# 使用现有批准入口各取一次当日日历/session，独立验证；按另行授权取得一组三样本。
& $py $pre --prepare --execution-contract single_bars_protocol_diagnostic_v1 `
  --date $day --target-at $target --approved-code-sha $code `
  --plan $plan --expected-plan-sha256 $planSha --output-dir '<新final-preflight目录>' `
  --before-environment '<baseline/environment-query.json>' --before-environment-sha256 '<SHA>' `
  --calendar '<当日completed-calendar>' --calendar-sha256 '<SHA>' `
  --session '<当日session>' --session-sha256 '<SHA>' --clock-raw '<原始三样本>' --clock-raw-sha256 '<SHA>'
& $py $pre --verify '<final-preflight/preflight.json>' --expected-file-sha256 '<SHA>' `
  --execution-contract single_bars_protocol_diagnostic_v1 --date $day --target-at $target --approved-code-sha $code
# 仅在单日PM批准v2授权文件已具备并且仍早于T-5时执行一次：
& $py $cli --network --date $day --target-at $target --plan $plan --expected-plan-sha256 $planSha `
  --authorization '<PM批准v2授权文件>' --authorization-sha256 '<该文件外部SHA>'
```

执行入口重放真实preflight、核对当前Git/依赖，再运行只读实时环境复核；子进程cwd固定批准工作树，不依赖调用终端所在目录。仅准确识别的当前CLI及venv重定向祖先可排除自身，任何协议worker/其他控制器及其后代仍阻断。内部worker需再验证启动回执、intent及实际授权/前置；直接调用不能跳过门禁。

## 封存与重放

原件固定在ignored `overnight_quant/data/cache/minute_protocol_diagnostic/<日期>/`，不可换目录绕过同日intent。`protocol-evidence-v2.json`、`seal-receipt-v1.json`、`seal-completion-v1.json`各自外部SHA；启动/结束回执、环境查询及PM引用一并保留。共享根仍为`minute_capture_audit`，清理未知写持久阻断，不自动解除。

```powershell
# 用同样三个外部SHA执行两次，replay路径必须不同且不存在：
& $py $cli --verify '<protocol-evidence-v2.json>' --expected-file-sha256 '<原件SHA>' `
  --seal-receipt '<seal-receipt-v1.json>' --expected-receipt-sha256 '<回执SHA>' `
  --seal-completion '<seal-completion-v1.json>' --expected-completion-sha256 '<完成见证SHA>' `
  --replay-output '<新的replay-A.json>'
# 第二次仅将replay-A改为replay-B；逐字节比较，复算三份原件SHA不变。
```

CLI与独立重放统一报告原件完整性、执行材料完整性、capture_result、seal_status、最终diagnostic_result；退出0仅表示本次候选诊断完整成功。短体/超时可以完整性通过但执行失败；封存材料缺失/失败不得借发现文件自动升级。成功不证明分钟语义/最终性、长期TCP预算、覆盖或来源资格。

正式资格0/3，data_ready=false、hard_gate_authorized=false，records/research_ranking/candidates/tickets/orders为空。ROOT_CAUSE_UNCONFIRMED及CAPTURE_BUDGET_FEASIBILITY_UNPROVEN继续保留。
