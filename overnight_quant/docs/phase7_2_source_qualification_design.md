# v0.4.1 正式来源资格验证设计

## 1. 范围

本阶段只验证数据来源资格，不修改策略权重、阈值、风险门禁或交易安全边界。所有结果
继续属于 research/shadow only。来源通过技术资格后也只能进入 PM 配置复核，不会自动
启用正式来源，不会生成 candidates、tickets 或 orders。

## 2. 分钟线双来源

分钟线候选来源完全独立：

| 来源标识 | 接口 | 来源版本 | 传输 | 当前资格 |
|---|---|---|---|---|
| `eastmoney` | 东财 `push2 trends2` | `push2_trends2_fields_v2026-07-30` | HTTPS | 未通过，关键时点存在断连 |
| `mootdx` | 通达信标准行情 `bars(frequency=1m)` | `mootdx_<package>_tdx_std_bars_1m_v2026-07-31` | TCP 7709 | 仅完成盘后连通性验证 |

每次运行只能指定一个 `--source`。四个时点的样本均写入 `probe_source`；分类器发现
多个来源或请求来源与样本来源不一致时，必须返回 `MINUTE_LABEL_INCONCLUSIVE`。
禁止使用东财的两个成功点和 mootdx 的另外两个成功点拼成一份 VERIFIED 证据。

## 3. 单日证据合同

每份单日证据必须包含：

- `source` 和稳定的 `source_version`；
- 14:49:55、14:50:05、14:50:30、14:51:05 四个目标时点；
- 每次请求的 `request_started_at`、`request_completed_at` 和耗时；
- 5 只股票逐股覆盖、14:50 行存在性和 OHLCV 哈希；
- 每次来源响应的 `raw_response_hashes` 和 `provider_raw_hash`；
- 规范化证据的 `probe_evidence_hash`；
- 固定为空的 candidates、tickets 和 orders。

任一请求失败、迟到、覆盖不足、来源版本缺失、原始哈希缺失、证据哈希无法独立重算
或分钟变化模式不明确，都只能得到 `INCONCLUSIVE`。

## 4. 文件写入

`run_minute_label_probe.py --output` 由 Python 在目标目录创建临时文件，写入无 BOM 的
UTF-8 JSON，刷新并同步后使用 `os.replace` 原子替换目标文件。不再依赖 PowerShell
`Tee-Object`，避免 UTF-16 和中途写入不完整 JSON。

原始证据只保存在 `overnight_quant/data/cache/`，该目录已被 Git 忽略。仓库只提交
脱敏摘要和 evidence hash。

## 5. 多日资格门槛

同一来源必须同时满足：

1. 同一天四个时点全部成功；
2. 每个时点 5 只股票全部覆盖；
3. 连续至少 3 个可信 A 股交易日满足前两项；
4. 全部关键请求的 P95 不超过 2000ms；
5. 交易日来自显式、可信、带来源、版本和 raw hash 的交易日历；
6. 所有单日 evidence hash 可独立重算；
7. 来源版本稳定，不存在跨来源拼接；
8. 不存在迟到记录进入决策或哈希漂移。

`evaluate_minute_source_qualification()` 只返回
`SOURCE_QUALIFIED_FOR_PM_REVIEW` 或 `SOURCE_NOT_QUALIFIED`。即使达到技术门槛，
`automatic_configuration_change=false`、`data_ready=false`，仍需 PM 人工复核。

## 6. 2026-08-03 运行命令

两条命令应在 14:49:40 前分别启动，脚本会等待四个目标时点：

```powershell
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_minute_label_probe.py --source eastmoney --codes 000001,000333,600000,600519,601318 --date 2026-08-03 --output overnight_quant/data/cache/minute_label_probe_eastmoney_2026-08-03.json

D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_minute_label_probe.py --source mootdx --codes 000001,000333,600000,600519,601318 --date 2026-08-03 --output overnight_quant/data/cache/minute_label_probe_mootdx_2026-08-03.json
```

14:40-14:52 不运行 30/50 股票压力测试。单日得到 VERIFIED 也不修改正式配置，必须
累计至少三个连续交易日并完成 PM 复核。

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
