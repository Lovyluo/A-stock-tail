# Phase 7.5 Go/No-Go 与 mootdx 节点基准设计

## 范围

本阶段只加固采样前门禁并提供 `audit-only` 节点性能证据。它不修改策略权重、评分阈值、
2000ms 硬截止、五只资格验证股票、连续三日政策或正式来源配置。所有输出继续保持
`data_ready=false`，`candidates`、`tickets`、`orders` 为空。

Tushare 和 Ashare 的角色固定为 `retired/disabled_by_policy`，本阶段不安装、不导入，也不
作为失败回退。AKShare 不参与本阶段，且不能被解释为独立原始来源。

## 通用 Go/No-Go

`run_minute_probe_go_nogo.ps1` 参数化交易日期、固定工作树、固定提交、计划任务名称、股票
代码、mootdx 节点、2000ms 截止和恢复模式。脚本可从任意当前目录运行，Python 入口根据
脚本绝对路径建立项目导入路径。

执行开始时先禁用全部目标采样任务。只有固定提交、干净工作树、时间同步、无已有输出、
无残留采样进程、任务安全设置、watchdog/audit 自检和节点 5/5 预检全部通过后才启用任务。
任一门禁失败或发生未处理异常时，任务保持 `Disabled`。

非 `ValidateOnly` 路径会在解析 endpoint、代码、Git 状态和结果文件之前先禁用任务。正式代码
范围固定为 `000001,000333,600000,600519,601318`，且计划任务 Action 中的 `Codes` 参数必须
与该范围完全一致。单股、缺股、多股或替换 `600000` 都返回 `SAMPLING_NO_GO`。
`ValidateOnly` 只读取状态，不启用也不禁用任务。

标准结果采用不可覆盖写入。恢复模式写入独立文件，并记录原始 `SAMPLING_NO_GO` 文件的
SHA-256；原文件永不删除或覆盖。

## 节点基准

节点基准复用可强制终止的独立 worker，每次请求硬截止固定为 2000ms：

1. 对 mootdx/tdxpy 可发现的全部去重节点各初筛一轮。
2. 只有 `ok=true`、同一 endpoint 且五股完整覆盖的节点才是初筛幸存者；再按端到端耗时
   选择前三名。
3. 对前三名分别至少运行五轮。
4. 输出每轮 5/5 覆盖、成功数、超时数、错误数、P50、nearest-rank P95、最大端到端耗时
   和 Python 子进程开销。

只有零错误、零超时、每轮 5/5 且最大端到端耗时不超过 1500ms 的节点才可进入“推荐候选”。
推荐结果只供 PM 审核，不会修改环境变量、正式配置或 Windows 计划任务。
没有初筛幸存节点时返回 `NO_SCREENING_SURVIVOR`，资格轮次为空、offset 对账不运行且推荐
节点为 `null`。任意股票诊断必须显式使用 `diagnostic_only`，并且永远不会产生推荐节点。

## 时间与证据合同

benchmark v2 将行情日期和采集时间分开：

- `data_trade_date`：被读取的分钟行情所属交易日；
- `benchmark_started_at` / `benchmark_completed_at`：本机真实开始与结束时间；
- 每轮保留 worker 的真实请求开始与完成时间；
- `observed_at` 等于真实 benchmark 开始时间，绝不回填成历史交易日。

历史日期过滤只存在于 `benchmark_preflight` / `benchmark_minute` 专用路径；正式 minute 和
transaction 路径仍按真实观察日期运行。新证据使用 schema `v2` 并将这些字段纳入哈希；无
schema 的旧 benchmark 证据按 `v1` 规则复核，原文件不修改。

## offset 对账

正式分钟采样默认 `offset=800` 保持不变。基准专用 operation 可以分别读取 `800` 和 `320`，
对比当日每股记录数、14:50 OHLCV 签名及排除采集时间后的规范化内容哈希。任何差异只会
形成审计结论，不会自动缩小正式 offset，也不会启用持久 worker。

两侧必须同时完整覆盖正式五股，`presence_by_code` 与签名代码集合必须精确一致，且每个
签名的事件时间必须是 `data_trade_date 14:50`。空签名、缺股或错误时点均返回
`OFFSET_COMPARISON_INCOMPLETE`，所有 `same_*` 字段为 `false` 并记录具体原因。

## 验收边界

- PowerShell 行为测试必须实际运行脚本，覆盖严格模式空进程列表、任意工作目录导入、
  `NO_GO` 不可覆盖和恢复哈希绑定。
- 节点与 offset 测试使用可注入 worker，证明错误、超时、部分覆盖均不能产生推荐。
- 历史 v1 与 2026-08-14 v2 证据只做回归验证，不修改原始 cache。
- 本阶段完成后只创建目标为 `codex/v041-source-qualification` 的 Draft PR，等待 PM 审核。
