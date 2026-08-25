# Phase 7.8 腾讯报价与估值候选 Provider 设计

## 1. 阶段范围

B2.2a 只实现以下两个只读候选 Provider：

```text
quote | tencent | direct_http | qt.gtimg.cn~88_fields_v2026-07-30
valuation | tencent | direct_http | qt.gtimg.cn~88_fields_v2026-07-30
```

实现位于独立模块 `tencent_direct_http_providers.py`。它不调用
`RealPointInTimeCollectors.collect_quotes()`，不会混入东财行业映射，也不使用
`AStockClient` 的 mootdx fallback。每次调用只构造一个 `https://qt.gtimg.cn/q=` 请求；
响应最终主机不是 `qt.gtimg.cn` 时失效关闭。

本阶段继续固定：

```text
bound_count=0
data_ready=false
hard_gate_authorized=false
candidates=[]
tickets=[]
orders=[]
automatic_configuration_change=false
```

## 2. 注入合同

`TencentDirectHttpProviders` 必须显式接收：

- `transport`：实现单次 `request()` 的传输对象；
- `clock`：返回带时区 `datetime` 的时钟；
- `codes`：请求股票代码；
- `timeout_seconds`：单请求超时。

候选 Provider 本身没有默认网络 transport。只有带 `--network` 的只读验证命令才显式
创建 `TencentUrllibTransport`。单元测试全部注入 fake transport 和固定时钟，不产生真实
HTTP/TCP 请求。传输层无重试、无 fallback，也不调用其他来源。

## 3. 请求与响应合同

输入代码先规范化为 6 位代码，再排序。重复代码、无效代码、市场前缀不匹配均在调用
transport 前拒绝。规范请求材料包括：

```text
method + endpoint + sorted symbols + response encoding
```

`request_hash` 使用上述规范材料计算，因此输入顺序不影响请求 URL 或哈希。响应必须满足：

- HTTP 200，最终主机仍是 `qt.gtimg.cn`；
- 原始字节可以严格 GBK 解码；
- 每行必须恰好为当前 source version 定义的 88 个字段；
- key 中市场与代码一致，响应内代码字段与 key 一致；
- 请求代码必须恰好覆盖一次，不得缺失、重复或出现未请求代码。

`raw_hash` 直接对解码前的完整响应字节计算 SHA-256。记录按代码排序，不能依赖响应或
输入顺序。

## 4. 时点合同

每条记录原生提供：

```text
origin_source, adapter, source_version,
event_time, observed_at, available_at,
request_hash, raw_hash, payload
```

`event_time` 只读取腾讯响应索引 30 的 14 位时间。`observed_at` 是 transport 调用前的
真实时钟，`available_at` 是 transport 返回后的真实时钟。以下情况直接拒绝，不进行修补：

- 来源时间缺失或格式错误；
- `observed_at > available_at`；
- `event_time > observed_at`，即本地请求开始时间早于来源事件时间；
- clock 为无时区时间。

## 5. 字段与单位

报价记录覆盖：现价、昨收、开盘、最高、最低、涨跌幅、成交量、成交额、换手率、涨跌停
价及买卖五档。价格单位为 `CNY_per_share`，成交量和盘口量为 `lot`，成交额为
`CNY_10k`，比例为 `percent`。

估值映射固定如下：

| 索引 | 字段 | 单位 |
|---|---|---|
| 39 | `pe_ttm` | ratio |
| 43 | `amplitude_pct` | percent |
| 44 | `market_cap` | CNY_100m |
| 45 | `float_market_cap` | CNY_100m |
| 46 | `pb` | ratio |
| 52 | `pe_static` | ratio |

索引 43 明确是振幅，不得作为 PB。空估值字段输出 `null`，并在
`valuation_availability` 中标记 `missing`；Provider 不用数字 0 填充空值。

## 6. 生产绑定边界与 B2.2b

B2.2a 不修改 `SOURCE_ADAPTER_BINDINGS`。腾讯 quote/valuation 的生产行继续保留历史
provider key、`legacy_implementation_present=true` 和
`implementation_status=contract_incompatible`，所以正式 `bound_count=0`。

测试只使用 `_execute_source_adapter_with_bindings_for_test()`，并构造合法、隔离的
`SourceAdapterBinding`。该候选选择行使用 `legacy_implementation_present=false`，只表达
本次测试选择，不会进入生产矩阵。独立断言继续证明生产 quote/valuation 行保持
`contract_incompatible + legacy_implementation_present=true`。B2.2b 必须由 PM 决定
以下方案之一：

1. 增加独立 `legacy_provider_key`，同时审计历史实现与新候选实现；
2. 升级适配注册表 schema，以显式表达 candidate 与 legacy 共存。

未经 B2.2b 授权，不得删除历史实现标记、修改生产 provider key 或启用 hard gate。

## 7. 只读真实验证

离线执行不会联网：

```powershell
D:\A-stock\.venv\Scripts\python.exe `
  overnight_quant/scripts/run_tencent_provider_validation.py
```

真实验证必须显式提供 `--network`，且完整证据只能写入 Git 忽略的 cache：

```powershell
D:\A-stock\.venv\Scripts\python.exe `
  overnight_quant/scripts/run_tencent_provider_validation.py `
  --network `
  --codes 000001,000333,600000,600519,601318 `
  --output overnight_quant/data/cache/tencent_provider_validation_YYYY-MM-DD.json
```

验证记录 quote/valuation 各自覆盖率、耗时、字段数、来源时间、request/raw hash、B1
provenance 状态和总 evidence hash。失败时输出稳定错误并保持安全状态，不使用 demo、
mootdx 或其他来源。该命令不进入 CI、不创建计划任务，也不修改正式配置。

## 8. v4 证据、外部锚定与独立重放

新生成的网络证据固定使用 schema：

```text
tencent_provider_network_evidence_v4
```

每次 quote/valuation 响应的解码前原始字节以 base64 无损嵌入 ignored JSON，并同时记录
字节数、请求 URL、请求哈希、原始哈希、请求起止时间、HTTP 状态码和最终响应 URL。
verifier 要求状态码为严格整数 `200`，最终 URL 使用 HTTPS 且 hostname 精确为
`qt.gtimg.cn`。重签 evidence hash 不能绕过这些来源身份门禁。

v4 明确拆分两个结论：

- `evidence_integrity_verified`：独立 verifier 是否完整重算并接受证据结构、哈希、时点、
  来源身份和失败审计；
- `provider_validation_passed`：quote 与 valuation 是否都完成五股覆盖、88 字段解析和 B1
  provenance 验证。

生产者写盘时不能自证完整性，因此原始文件固定
`evidence_integrity_verified=false`。严格 v4 验收必须从文件之外提供已经登记的
`--expected-file-sha256`；该值与实际文件不一致时直接返回
`TENCENT_PROVIDER_EVIDENCE_INVALID`。同一 JSON 内重复错误码或增加另一层自算哈希不能替代
这个外部锚点。

收到 HTTP 响应后发生 GBK、字段数、覆盖、代码或来源身份错误时，证据无损保留原始响应、
HTTP 状态、最终 URL、请求时点和哈希。verifier 从原始字节重放解析，只有重现完全相同的
错误码才输出 `failure_reason_verified=true` 和 `REPLAY_VERIFIED`。超时、连接失败等没有响应
的错误没有可重放材料，即使 capability summary 与 attempt 中的错误码一致，也只能输出
`failure_reason_verified=false` 和 `UNCORROBORATED`。外部锚定可证明登记文件未被替换，不能
把不可观察的网络失败原因变成可独立证明的事实。

v4 同时绑定生产者 commit SHA、确定性的 Provider/verifier contract hash，以及 B1 registry
schema/hash。`TENCENT_PROVIDER_PROVENANCE_REJECTED` 保留原始响应、重建记录和完整 provenance
结果；当前合同能够重现拒绝时，证据完整性可以通过但 Provider 验证必须失败。当前 registry
或 provenance 合同无法复现原拒绝时返回独立的
`PROVENANCE_CONTRACT_VERSION_MISMATCH`，不混同于普通文件损坏。

合法超时、单能力失败或双能力失败在外部锚定和结构复核后可以得到
`evidence_integrity_verified=true`，但必须保持 `provider_validation_passed=false`。任何使用原
登记 SHA 校验的已篡改文件、HTTP 201、非腾讯最终地址或字段缺失仍返回
`TENCENT_PROVIDER_EVIDENCE_INVALID`。

旧 v2 继续使用原来的“两个能力全部成功才可验证”语义，不静默升级为失败证据合同。旧的
无 schema v1 证据保持原文件不变，并执行固定字段集合、来源身份、五股覆盖、时点顺序、
64 位哈希、字段单位和 B1 provenance 的严格校验；只有完整满足旧合同的文件才返回
`TENCENT_PROVIDER_EVIDENCE_LEGACY_AUDIT_ONLY`。任意 JSON 即使自行计算 evidence hash 也
不能进入旧版审计状态。v1 始终是 audit-only，不能升级为 v2/v3/v4 来源验证结果。v3 保持
既有语义和文件哈希，不被 v4 的外部锚定要求静默改写。

独立只读 verifier：

```powershell
D:\A-stock\.venv\Scripts\python.exe `
  overnight_quant/scripts/run_tencent_provider_evidence_verify.py `
  overnight_quant/data/cache/tencent_provider_validation_v4_YYYY-MM-DD.json `
  --expected-file-sha256 <externally-recorded-sha256>
```

verifier 不联网，并执行以下重算：

- 重算 evidence hash 与每份原始响应 SHA-256；
- 严格 GBK 解码并重新解析 88 字段；
- 核对固定五股覆盖、记录 capability、来源身份及 provider key；
- 核对 HTTP 200、HTTPS 和最终 hostname `qt.gtimg.cn`；
- 重建 quote/valuation payload，验证时间、request hash 与 B1 provenance；
- 重算 capability 成功或失败摘要，并核对尝试次数和网络请求数；
- 重放已捕获响应的解析失败；无响应错误明确标记为不可独立佐证；
- 核对 producer commit、Provider/verifier contract 和 B1 provenance contract；
- 强制 `data_ready=false`、hard gate 禁止、交易输出为空。

verifier 可将确定性重放结果写入另一个 ignored cache 文件。对同一原始证据执行两次，
输出字节与 replay hash 必须完全一致。证据和重放文件均使用跨进程排他、不可覆盖的原子
写入；并发写同一路径时只允许一个进程成功。

网络活动计数只有 transport 提供可核验计数时才输出整数。已测量的请求不得报告为 0；
无法独立测量时固定输出 `network_requests_made=null` 与
`upstream_network_activity=unknown`。
