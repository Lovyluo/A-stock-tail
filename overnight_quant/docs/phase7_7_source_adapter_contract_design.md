# Phase 7.7 注册表驱动的只读来源适配合同

## 1. 阶段范围

阶段 B2.1 只建立 B1 静态来源注册表与仓库现有 provider 能力之间的离线映射。
适配层不实例化 collector、不建立 HTTP/TCP 连接、不自动回退，也不修改任何来源配置。
执行入口只接受测试或研究调用方显式注入的零参数 provider callable，不提供默认联网
构造器。

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

因此 `SOURCE_ADAPTER_BOUND` 只表示“静态身份可映射且注入数据通过追溯合同”，不表示
来源在线、来源已取得正式资格或数据可参与策略评分。

## 2. 两层注册表

B1 注册表负责来源政策：

```text
capability + origin_source + adapter + source_version
```

B2.1 适配注册表在完全相同的身份上增加：

```text
provider_key + implementation_status
```

`provider_key` 只是对仓库中已有实现的符号引用。审计命令不会导入该符号，也不会创建
真实 provider。每个适配身份必须在 B1 固定注册表中唯一匹配；重复、未知版本、来源不符
或退役来源都失效关闭。

适配注册表哈希同时绑定：

- `source_capability_adapter_registry_v1`；
- B1 registry schema；
- B1 registry hash；
- 规范排序后的 28 项适配绑定。

输入顺序不影响适配注册表哈希。

## 3. 覆盖矩阵

B2.1 为 B1 的全部 28 项能力输出一行。当前 13 项具有仓库内明确实现映射：

| 能力 | 原始来源 | 适配器 | provider key | 边界 |
|---|---|---|---|---|
| quote、valuation | 腾讯 | direct_http | `AStockClient._tencent_quotes` | 只读注入 |
| trading_calendar | 腾讯 | direct_http | `collect_trading_calendar` | 只读注入 |
| daily_bar_qfq | 腾讯 | direct_http | `collect_qfq_daily_bars` | 只读注入 |
| industry_snapshot | 东财 | direct_http | `collect_industry` | 未取得 hard gate 资格 |
| fund_flow | 东财 | direct_http | `collect_eastmoney_fund_flow` | 未取得 hard gate 资格 |
| fund_flow | 新浪 | direct_http | `collect_sina_fund_flow` | proxy/audit only |
| global_news | 东财 | direct_http | `collect_global_news` | 研究展示 |
| global_news | 财联社 | direct_http | `fetch_cls_telegraph` | 研究展示 |
| stock_news | 东财 | direct_http | `collect_stock_news` | 研究展示 |
| announcement | 巨潮 | direct_http | `collect_announcements` | 研究展示 |
| minute_bar | 通达信 | mootdx | `collect_minute_bars` | audit only，资格 0/3 |
| transaction | 通达信 | mootdx | `collect_transaction_evidence` | audit only，资格 0/3 |

其余 15 项保持以下状态之一：

- `not_implemented`：仓库没有身份和版本均明确的当前实现；
- `optional_unconfigured`：iwencai 不读取 Key，也不输出 Key；
- `retired`：Tushare、Ashare 固定政策禁用。

AKShare 条目仅保留原始来源身份和可选 wrapper 身份。本阶段不安装 AKShare，也不将它
视为独立来源或正式硬门禁来源。mootdx 盘口、F10 和财务等仅在 B1 注册表出现、但当前
仓库没有相同版本的可执行 provider 合同时，保持 `SOURCE_ADAPTER_NOT_IMPLEMENTED`。

## 4. 失效关闭执行链

`execute_source_adapter()` 的顺序固定为：

1. 校验请求身份完整；
2. 使用 B1 `route_source_capability(..., require_hard_gate=False)`；
3. 路由拒绝时不调用 provider；
4. 核对 28 项适配矩阵中的完整身份和 provider key；
5. 仅调用显式注入的 provider callable；
6. 区分合法空结果和 provider 异常；
7. 使用 B1 `validate_source_provenance_batch(..., require_hard_gate=False)`；
8. 追溯通过后仍固定 `hard_gate_authorized=false` 和 `data_ready=false`。

适配层不会补写或修复 provider 返回记录。以下字段必须由 provider 原样提供：

```text
origin_source, adapter, source_version,
event_time, observed_at, available_at,
request_hash, raw_hash
```

新闻、研报、公告还必须提供 `published_at`。来源混用、字段缺失、时间倒置或 SHA-256
无效均返回 `SOURCE_ADAPTER_PROVENANCE_REJECTED`。适配层不跨来源拼接，不自动 fallback。

## 5. 稳定状态

| 状态 | 含义 |
|---|---|
| `SOURCE_ADAPTER_AUDIT_COMPLETE` | 28 项离线矩阵已生成 |
| `SOURCE_ADAPTER_BOUND` | 静态绑定存在，注入数据通过 B1 追溯 |
| `SOURCE_ADAPTER_NOT_IMPLEMENTED` | 注册能力存在，但没有当前明确实现 |
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

## 7. 安全边界

- research/shadow only；
- 不连接券商、不下单、不点击证券软件；
- 不实例化真实 collector，不发起联网验证；
- 不安装 AKShare，不读取 iwencai Key；
- 不导入或回退到 Tushare、Ashare；
- 不修改策略、评分、阈值、正式配置、CI 或连续资格计数；
- 不启动 mootdx 连续三日任务；
- 不进入 B2.2。
