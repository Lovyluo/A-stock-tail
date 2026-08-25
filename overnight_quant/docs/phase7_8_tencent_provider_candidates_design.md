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

测试使用私有 `test_only` 执行核心验证候选 provider key、返回结构和 B1 provenance。
测试绑定保留 `legacy_implementation_present=true`，它故意不能通过当前生产 schema，不能
被序列化为正式绑定。B2.2b 必须由 PM 决定以下方案之一：

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
