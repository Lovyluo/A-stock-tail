# v0.4.1 正式来源资格验证设计

## 1. 范围

本阶段只验证数据来源资格，不修改策略权重、阈值、风险门禁或交易安全边界。所有结果
继续属于 research/shadow only。来源通过技术资格后也只能进入 PM 配置复核，不会自动
启用正式来源，不会生成 candidates、tickets 或 orders。

## 2. 分钟线双来源

分钟线候选来源完全独立：

| 来源标识 | 接口 | 来源版本 | 传输 | 当前资格 |
|---|---|---|---|---|
| `eastmoney` | 东财 `push2 trends2` | `push2_trends2_fields_v2026-07-30` | HTTPS | 仅作独立审计，不参与连续合格日计算 |
| `mootdx` | 通达信标准行情 `bars(frequency=1m)` + `transaction()` | 分钟与逐笔分别版本化 | TCP 7709 | 主要资格候选，仍不是正式来源 |

每次运行只能指定一个 `--source`。四个时点的样本均写入 `probe_source`；分类器发现
多个来源或请求来源与样本来源不一致时，必须返回 `MINUTE_LABEL_INCONCLUSIVE`。
禁止使用东财的两个成功点和 mootdx 的另外两个成功点拼成一份 VERIFIED 证据。

### 2.1 mootdx 分钟区间归因

最后一个四时点样本完成后，mootdx 使用同一客户端采集逐笔成交，分别聚合：

- `14:49:00-14:49:59`；
- `14:50:00-14:50:59`。

逐股比较 open、high、low、close、volume，并记录成交笔数。只允许三种结论：

- 仅 14:49 区间匹配：`minute_end_provisional`；
- 仅 14:50 区间匹配：`minute_start_provisional`；
- 逐笔覆盖不完整、非秒级时间、原生数量单位不一致、两边都匹配、两边都不匹配或
  五股归因不一致：`INCONCLUSIVE`。

mootdx 分钟 K 线没有成交笔数字段，所以逐笔 `trade_count` 只作可重算审计字段，不能
宣称与分钟行成交笔数已经等价。程序不通过标签名称推断语义。

## 3. 单日证据合同

每份单日证据必须包含：

- `source` 和稳定的 `source_version`；
- 14:49:55、14:50:05、14:50:30、14:51:05 四个目标时点；
- 每次请求的 `request_started_at`、`request_completed_at` 和耗时；
- 5 只股票逐股覆盖、14:50 行存在性和 OHLCV 哈希；
- 每次来源响应的 `raw_response_hashes` 和 `provider_raw_hash`；
- 规范化证据的 `probe_evidence_hash`；
- 同源 `transaction_evidence_hash`；
- 分钟、逐笔和归因共同计算的 `combined_evidence_hash`；
- `bar_label_time`、`interval_start`、`interval_end`、`first_observed_at`、
  `finalized_at`、`is_final` 和 `finalization_delay_ms`；
- 固定为空的 candidates、tickets 和 orders。

任一请求失败、迟到、覆盖不足、来源版本缺失、原始哈希缺失、证据哈希无法独立重算
或分钟变化模式不明确，都只能得到 `INCONCLUSIVE`。

`compute_probe_evidence_hash()` 必须显式传入非空 `source`。独立校验命令分别重算
分钟、逐笔和组合哈希；任何来源不一致或哈希漂移都会失败。`is_final=false` 的分钟行
不进入 readiness、评分、`snapshot_hash` 或 `decision_hash`。

## 4. 文件写入

`run_minute_label_probe.py --output` 由 Python 在目标目录创建临时文件，写入无 BOM 的
UTF-8 JSON，刷新并同步后使用 `os.replace` 原子替换目标文件。不再依赖 PowerShell
`Tee-Object`，避免 UTF-16 和中途写入不完整 JSON。

原始证据只保存在 `overnight_quant/data/cache/`，该目录已被 Git 忽略。仓库只提交
脱敏摘要和 evidence hash。

## 5. 多日资格门槛

作为主要候选的 mootdx 必须同时满足：

1. 同一天四个时点全部成功；
2. 每个时点 5 只股票全部覆盖；
3. 五只股票同源区间归因一致，provisional 行最终稳定；
4. 连续至少 3 个可信 A 股交易日满足上述条件；
5. 全部关键请求的 P95 不超过 2000ms；
6. 交易日来自显式、可信、带来源、版本和 raw hash 的交易日历；
7. 所有单日 evidence hash 可独立重算；
8. 来源版本稳定，不存在跨来源拼接；
9. 不存在迟到记录进入决策或哈希漂移。

Eastmoney 结果无论是否完整都标记为 `audit_only`，不得进入上述连续合格日计算。

`evaluate_minute_source_qualification()` 只返回
`SOURCE_QUALIFIED_FOR_PM_REVIEW` 或 `SOURCE_NOT_QUALIFIED`。即使达到技术门槛，
`automatic_configuration_change=false`、`data_ready=false`，仍需 PM 人工复核。

## 6. 2026-08-04 至 2026-08-06 运行命令

两条命令应在 14:49:40 前分别启动，脚本会等待四个目标时点：

```powershell
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_minute_label_probe.py --source eastmoney --codes 000001,000333,600000,600519,601318 --date YYYY-MM-DD --output overnight_quant/data/cache/minute_label_probe_eastmoney_YYYY-MM-DD.json

D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_minute_label_probe.py --source mootdx --codes 000001,000333,600000,600519,601318 --date YYYY-MM-DD --output overnight_quant/data/cache/minute_label_probe_mootdx_YYYY-MM-DD.json

D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_probe_evidence_verify.py --source mootdx --input overnight_quant/data/cache/minute_label_probe_mootdx_YYYY-MM-DD.json
```

`YYYY-MM-DD` 依次替换为 2026-08-04、2026-08-05、2026-08-06。14:40-14:52
不运行 30/50 股票压力测试。单日得到 provisional 也不修改正式配置，必须累计至少
三个连续交易日并完成 PM 复核。

## 7. 其他正式来源

以下来源资格继续保持独立，不允许互相替代完整性门禁：

| 数据类型 | 当前状态 | 后续验证要求 |
|---|---|---|
| 市场宽度正式备用 | 未通过 | 指数状态、上涨/下跌/平盘家数、事件时间、完成时间、版本和 raw hash 完整 |
| 行业宽度正式备用 | 未通过 | 稳定行业标识、涨跌幅、上涨/下跌/平盘家数、宽度和完整时点合同 |
| 非代理正式资金流 | 东财主源未通过 | 分钟语义、字段定义、事件时间和连续多日 SLA 均需证明；新浪仍只作 proxy |

任一关键来源未通过时，正式完整股票数保持 0，`data_ready=false`。首版尾盘实时池
上限仍为 10 只；30/50 股票只用于盘前预筛和容量研究。

## 8. 安全边界

- 不连接券商；
- 不调用交易 API；
- 不自动下单；
- 不点击证券软件；
- 不绕过 session gate、risk gate 或数据完整性门禁；
- 不因来源资格不足启用 demo fallback；
- 不提交原始采样、运行记录、报告、缓存、日志、持仓或密钥。
