# Phase 7.7 注册表驱动的只读来源适配合同

## 1. 阶段范围

阶段 B2.1 只建立 B1 静态来源注册表与仓库现有 provider 能力之间的离线映射。
适配层不实例化 collector、不建立 HTTP/TCP 连接、不自动回退，也不修改任何来源配置。
执行入口只接受测试或研究调用方显式注入的 `SourceProviderEnvelope`，其中同时携带
固定 `provider_key` 和零参数 callable；裸 callable 不被接受，也不提供默认联网构造器。

本阶段固定为：

```text
require_hard_gate=false
hard_gate_authorized=false
data_ready=false
candidates=[]
tickets=[]
orders=[]
automatic_configuration_change=false
```

因此 `SOURCE_ADAPTER_BOUND` 只表示“provider key、调用签名、返回结构、来源身份和 B1
追溯字段全部兼容”，不表示来源在线、来源已取得正式资格或数据可参与策略评分。当前
生产矩阵只有腾讯 quote/valuation 两项满足这一条件，`bound_count=2`。这里的 `bound`
只允许调用方显式注入匹配的只读 Provider envelope，不创建默认网络连接，也不表示来源
已取得正式资格或数据可参与策略评分。

## 2. 两层注册表

B1 注册表负责来源政策：

```text
capability + origin_source + adapter + source_version
```

B2.2b 将适配注册表升级为确定性的三槽位生命周期模型：

```text
provider_key + candidate_provider_key + legacy_provider_key
implementation_status
```

`provider_key` 只保存已经选择的显式只读 Provider，当前仅腾讯 quote/valuation 两项非空；
`candidate_provider_key` 保存合同兼容但尚未启用的候选实现；`legacy_provider_key` 保存仅供
审计的历史实现。`legacy_implementation_present` 如需出现在兼容审计输出中，只能由
`legacy_provider_key` 是否非空确定性派生，不能由调用方自由声明。审计命令不会导入这些
符号，也不会创建真实 provider。每个适配身份必须在 B1 固定注册表中唯一匹配；重复、
缺失、新增、版本不符、非规范大小写/空格或固定状态被修改都失效关闭。

适配注册表哈希同时绑定：

- `source_capability_adapter_registry_v3`；
- B1 registry schema；
- B1 registry hash；
- 规范排序后的 28 项适配绑定。

生产公共哈希函数不接受调用方自定义条目，只计算固定的完整 28 项矩阵。测试所需的自定义
子集和换序哈希入口为模块私有函数，不能用于生产授权。输入顺序不影响私有测试哈希，但
删除一项、增加一项或修改固定绑定即使重新签名，也不能通过生产矩阵校验。

执行结果明确区分固定生产矩阵和本次实际选择矩阵：

| 字段 | 含义 |
|---|---|
| `production_adapter_registry_hash` | 固定 28 项生产矩阵的哈希 |
| `selection_adapter_registry_hash` | 本次执行实际使用、规范化后的绑定矩阵哈希 |
| `selection_registry_scope` | 只能为 `production` 或 `test_only` |
| `adapter_registry_hash` | 兼容字段，固定等于本次 `selection_adapter_registry_hash` |

公开 `execute_source_adapter()` 只能使用完整生产矩阵，因此 scope 为 `production`，两个
哈希必须完全相同。私有测试入口的 scope 为 `test_only`，selection hash 必须根据实际传入
的测试绑定重新计算；修改测试 provider key 等绑定字段会改变 selection hash，不能用生产
哈希冒充测试选择矩阵。离线审计命令只审计固定生产矩阵，三个哈希字段均指向生产哈希，
scope 为 `production`。

## 3. 覆盖矩阵

B2.1/B2.2b 为 B1 的全部 28 项能力输出一行。B2.2c 只将腾讯 quote 和 valuation 的候选
key 移入 `provider_key`，状态改为 `bound`，并保留历史实现；其余 11 项历史实现仍为
`contract_incompatible`：

| 能力 | 原始来源 | 适配器 | candidate/legacy provider key | 状态或不兼容原因 |
|---|---|---|---|---|
| quote | 腾讯 | direct_http | `TencentDirectHttpProviders.collect_quote_records` / `AStockClient._tencent_quotes` | `bound`，只接受显式 envelope |
| valuation | 腾讯 | direct_http | `TencentDirectHttpProviders.collect_valuation_records` / `AStockClient._tencent_quotes` | `bound`，只接受显式 envelope |
| trading_calendar | 腾讯 | direct_http | `collect_trading_calendar` | 需要实例与 observed_at，返回 `ProviderBatch` |
| daily_bar_qfq | 腾讯 | direct_http | `collect_qfq_daily_bars` | 需要实例与 observed_at，返回 `ProviderBatch` |
| industry_snapshot | 东财 | direct_http | `collect_industry` | 需要实例与 observed_at，返回 `ProviderBatch` |
| fund_flow | 东财 | direct_http | `collect_eastmoney_fund_flow` | 需要实例与 observed_at，返回 `ProviderBatch` |
| fund_flow | 新浪 | direct_http | `collect_sina_fund_flow` | 返回 proxy `ProviderBatch`，非 B1 batch |
| global_news | 东财 | direct_http | `collect_global_news` | 返回 `ProviderBatch`，记录使用旧 `source` 身份 |
| global_news | 财联社 | direct_http | `fetch_cls_telegraph` | 返回普通 list，缺 B1 身份与哈希字段 |
| stock_news | 东财 | direct_http | `collect_stock_news` | 返回 `ProviderBatch`，记录使用旧 `source` 身份 |
| announcement | 巨潮 | direct_http | `collect_announcements` | 返回 `ProviderBatch`，记录使用旧 `source` 身份 |
| minute_bar | 通达信 | mootdx | `collect_minute_bars` | 返回 `ProviderBatch`，audit only，资格 0/3 |
| transaction | 通达信 | mootdx | `collect_transaction_evidence` | 返回证据 dict，不是 provenance record list |

其余 15 项保持以下状态之一：

- `not_implemented`：仓库没有身份和版本均明确的当前实现；
- `optional_unconfigured`：iwencai 不读取 Key，也不输出 Key；
- `retired`：Tushare、Ashare 固定政策禁用。

其中 12 项为 `not_implemented`（包含 6 项未安装 AKShare wrapper），1 项 iwencai 为
`optional_unconfigured`，2 项 Tushare/Ashare 为 `retired`。AKShare 仅保留真实原始来源
和可选 wrapper 身份，不是独立来源或正式硬门禁来源。mootdx 盘口、F10 和财务等虽在
B1 注册表出现，但当前没有相同版本的可执行 provider 合同。

Schema v2 的旧适配注册表哈希为：

```text
4f274abcce88fedb425b9544e901d92da69a5cafa951fcda864a0c5fc06dd6be
```

三槽位 Schema v3 的固定生产适配注册表哈希为：

```text
1eb8114cf3aa68bf85473a67513dfdd7a3ab5b32a464a66d5c4664163a4f8b2d
```

B2.2c 腾讯只读影子绑定后的 Schema v3 哈希为：

```text
61758c08282a12b0c68d07bc46155dfe5848d1e44bf9a42ac96276e51642bd39
```

本次哈希变化仅来自两个候选进入显式只读 `provider_key`；B1 registry hash 保持
`303db7cd50d8cc53e3729d69c7aeb3c053e203ba1885cecda1b10f0cdd321c69`。

## 4. 失效关闭执行链

`execute_source_adapter()` 的顺序固定为：

1. 校验请求身份完整；
2. 使用 B1 `route_source_capability(..., require_hard_gate=False)`；
3. 路由拒绝时不调用 provider；
4. 核对 28 项适配矩阵中的完整身份和 provider key；
5. 遇到 `candidate_not_activated` 立即返回
   `SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED`，不检查或调用候选/历史 provider；
6. 拒绝裸 callable、错误 envelope 和不匹配的 provider key；
7. 只有 `implementation_status=bound` 才调用 envelope 中的 callable；
8. 区分合法空结果和 provider 异常；
9. 使用 B1 `validate_source_provenance_batch(..., require_hard_gate=False)`；
10. 追溯通过后仍固定 `hard_gate_authorized=false` 和 `data_ready=false`。

适配层不会补写或修复 provider 返回记录。以下字段必须由 provider 原样提供：

```text
origin_source, adapter, source_version,
event_time, observed_at, available_at,
request_hash, raw_hash
```

新闻、研报、公告还必须提供 `published_at`。来源混用、字段缺失、时间倒置或 SHA-256
无效均返回 `SOURCE_ADAPTER_PROVENANCE_REJECTED`。适配层不跨来源拼接，不自动 fallback。
旧 collector 常用的单一 `source` 字段不会被转换成 `origin_source` 和 `adapter`，而是明确
拒绝。

生产矩阵当前没有 `bound` 条目，因此不会调用候选或这 13 个旧实现。模块私有测试入口可以
注入一条合同兼容的测试绑定，用于验证 envelope、provenance 和安全状态；该入口始终为
`test_only`，不能改变生产矩阵或 hard gate。

## 5. 稳定状态

| 状态 | 含义 |
|---|---|
| `SOURCE_ADAPTER_AUDIT_COMPLETE` | 28 项离线矩阵已生成 |
| `SOURCE_ADAPTER_BOUND` | 完整 envelope 绑定和注入数据均通过；生产矩阵当前为 0 |
| `SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED` | 合同兼容候选已登记但未启用，provider 未调用 |
| `SOURCE_ADAPTER_NOT_IMPLEMENTED` | 无实现或已有实现与 B1 合同不兼容 |
| `SOURCE_ADAPTER_REQUEST_INVALID` | 身份、版本或 provider 参数无效 |
| `SOURCE_ADAPTER_ROUTE_REJECTED` | B1 政策拒绝，provider 未调用 |
| `SOURCE_ADAPTER_PROVIDER_EMPTY` | provider 合法返回空集合 |
| `SOURCE_ADAPTER_PROVIDER_FAILED` | provider 抛出异常，错误正文不回显 |
| `SOURCE_ADAPTER_PROVENANCE_REJECTED` | provider 数据未通过 B1 追溯合同 |

## 6. 只读审计命令

```powershell
D:\A-stock\.venv\Scripts\python.exe `
  overnight_quant/scripts/run_source_adapter_audit.py
```

命令固定使用空的可选密钥环境合同，不读取 `IWENCAI_API_KEY` 或
`IWENCAI_BASE_URL`。它只输出确定性 UTF-8 JSON、两层注册表哈希、覆盖矩阵和
`network_requests_made=0`。连续运行输出逐字节一致。

`network_requests_made=0` 只适用于该离线矩阵审计和 provider 未调用的拒绝路径。适配层
自身始终记录 `adapter_network_requests_made=0`；一旦注入 provider 被调用，适配层无法
独立证明上游网络活动，因此顶层输出固定为：

```text
network_requests_made=null
upstream_network_activity=unknown
```

不得把未知活动伪装为零次网络请求。

## 7. 安全边界

- research/shadow only；
- 不连接券商、不下单、不点击证券软件；
- 不实例化真实 collector，不发起联网验证；
- 不安装 AKShare，不读取 iwencai Key；
- 不导入或回退到 Tushare、Ashare；
- 不修改策略、评分、阈值、正式配置、CI 或连续资格计数；
- 不启动 mootdx 连续三日任务；
- 腾讯仅为显式只读影子绑定，不接入 collector、策略、默认网络或正式 hard gate。
