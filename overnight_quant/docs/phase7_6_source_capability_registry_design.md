# Phase 7.6 数据源能力注册与来源追溯合同

## 1. 阶段范围

阶段 B1 只建立离线能力目录、来源追溯合同和失效关闭路由器。它不发起网络请求，
不修改现有 collector、策略、评分阈值、来源资格规则、CI 或正式配置，也不启用采样任务。
所有审计和路由结果继续保持 `data_ready=false`，`candidates`、`tickets`、`orders`
为空。

本阶段没有安装或导入 AKShare、Tushare、Ashare。AKShare 只是注册表中的可选包装器；
Tushare 和 Ashare 固定为 `retired/disabled_by_policy`。

## 2. 三层身份

一个能力条目必须同时记录三种不同语义：

| 字段 | 含义 | 示例 |
|---|---|---|
| `capability` | 项目需要的数据能力 | `quote`、`announcement`、`minute_bar` |
| `origin_source` | 数据的真实原始来源 | `tencent`、`cninfo`、`eastmoney` |
| `adapter` | 获取或包装原始来源的技术适配器 | `direct_http`、`mootdx`、`akshare` |

AKShare 不是独立原始来源。例如 `stock_news_em` 的身份仍是
`origin_source=eastmoney, adapter=akshare`。直连与 AKShare 若读取相同东财接口，
属于同一来源族，不能作为双来源确认。

来源角色固定为：

- `primary`：当前首选来源；
- `secondary`：候选或补充来源；
- `audit_only`：只允许审计和研究；
- `optional_enrichment`：缺失时不影响核心项目；
- `retired`：政策禁用，不允许导入、路由或回退。

## 3. 注册表合同

每项能力至少包含：

```text
capability, origin_source, adapter, role,
time_critical, requires_secret,
hard_gate_eligible, qualification_required,
point_in_time_required, fallback_group,
source_version, enabled_by_policy
```

实现还保留：

- `qualification_status` 和 `qualification_progress`；
- `is_proxy`，标记语义不等价的代理数据；
- `is_wrapper`，标记 AKShare 等包装适配器。

注册表按能力、回退组、原始来源、适配器和来源版本规范排序。`registry_hash` 只对
静态政策和规范排序后的条目计算，因此输入顺序不会改变哈希。运行环境中的密钥是否
存在只影响 `source_statuses`，不会改变静态注册表哈希。

所有布尔字段使用严格 `bool`，字符串 `"false"` 或数字 `0/1` 均不接受。注册表还会
交叉验证 role、wrapper、proxy、retired、qualification 和 hard-gate 语义：AKShare
适配器必须声明为 wrapper；wrapper、proxy、audit-only、optional-enrichment、retired
不得获得 hard gate；退役来源必须 disabled 且资格状态为 retired；未资格来源不得通过
hard gate。

## 4. 固定来源矩阵

| 能力 | 原始来源 | 适配器 | 角色 | 当前 hard gate |
|---|---|---|---|---|
| 报价、估值、交易日历、前复权日线 | 腾讯 | `direct_http` | `primary` | 仅注册表允许的能力可路由 |
| 分钟线、逐笔、盘口 | 通达信 | `mootdx` | `audit_only` | 禁止，资格为 `0/3` |
| 研报、新闻 | 东财 | `direct_http` | `primary` | 研究展示，不单独形成交易就绪 |
| 行业、资金流 | 东财 | `direct_http` | `secondary` | 未资格，禁止 |
| 公告 | 巨潮 | `direct_http` | `primary` | 来源合同允许，实际数据仍需时点门禁 |
| 公告摘要、基础资料 | 通达信 | `mootdx` | `audit_only` | 禁止 |
| 全球资讯 | 财联社 | `direct_http` | `secondary` | 研究用途 |
| 财报与资金代理 | 新浪 | `direct_http` | `secondary/audit_only` | 资金代理禁止 |
| 东财、巨潮、财联社包装能力 | 对应真实来源 | `akshare` | `optional_enrichment` | 永远禁止 |
| 语义研报增强 | 问财 | `iwencai_openapi` | `optional_enrichment` | 永远禁止 |
| 旧行情能力 | Tushare、Ashare | 对应退役适配器 | `retired` | 政策禁用 |

东财行业和资金流虽然是候选来源，但阶段 B1 不改变其既有资格状态。mootdx 的分钟、
逐笔和盘口继续保持 `audit_only/unqualified`，连续资格计数仍为 `0/3`。

## 5. 失效关闭路由

`route_source_capability()` 每次最多选择一个条目：

1. 未知 capability 返回 `UNKNOWN_CAPABILITY`；
2. 未知来源返回 `UNKNOWN_SOURCE`；
3. 退役来源返回 `SOURCE_DISABLED_BY_POLICY`；
4. 未配置问财返回 `OPTIONAL_UNCONFIGURED`，但 `execution_ok=true`；
5. audit、proxy、wrapper 或未资格来源请求 hard gate 时拒绝；
6. 只有单一条目通过全部政策检查后，才返回 `SOURCE_ROUTE_SELECTED`。

公开路由固定使用内置 `SOURCE_CAPABILITIES`，调用方不能注入自定义注册表。测试若需
构造自定义来源，只能调用模块私有辅助函数；该路径即使选择成功也固定返回
`hard_gate_authorized=false`。

`require_hard_gate` 是安全控制参数，只接受 Python 严格布尔值，即
`type(require_hard_gate) is bool`。字符串 `"true"`、`"false"`、整数 `0/1`、`None`
及容器值不会被隐式转换：路由返回 `SOURCE_ROUTE_REQUEST_INVALID`，追溯返回
`PROVENANCE_REQUEST_INVALID`，并在进入来源资格判断前保持失效关闭。公开入口、私有
测试入口和内部 core 均执行同一防御性检查。

路由成功只表示静态政策允许使用该来源，不表示实际数据已到达或可用于交易决策。
因此 B1 中 `data_ready` 始终为 `false`。

## 6. 来源追溯批次

`validate_source_provenance_batch()` 禁止把不同来源拼成一条正式记录。一个批次中的所有
记录必须具有相同的：

```text
origin_source, adapter, source_version
```

每条记录还必须保留 `request_hash`、`raw_hash`。需要时点合同的能力必须提供
`event_time`、`observed_at` 和 `available_at`；新闻、研报和公告还必须提供
`published_at`。缺失字段返回 `PROVENANCE_CONTRACT_INCOMPLETE`，来源混合返回
`MIXED_SOURCE_PROVENANCE_REJECTED`。

两个哈希字段必须为完整 64 位 SHA-256。时间字段使用项目统一的中国时区解析器，并
至少满足 `event_time <= observed_at <= available_at`；新闻、研报和公告还必须满足
`published_at <= observed_at <= available_at`。非法时间、倒置时间或非法哈希均以稳定
状态拒绝，且 `hard_gate_authorized=false`。

追溯记录在计算 `record_hash` 前会规范化身份、哈希和时间文本，并按确定性规则排序。
哈希材料绑定 registry schema、registry hash、capability、来源身份和全部规范化记录，
所以同一记录集合换序不会改变结果。所有追溯结果都返回
`registry_schema_version` 与 `registry_hash`。公开追溯入口同样只使用内置注册表。

## 7. 只读审计命令

```powershell
D:\A-stock\.venv\Scripts\python.exe `
  overnight_quant/scripts/run_source_capability_audit.py
```

可选的单路由审计：

```powershell
D:\A-stock\.venv\Scripts\python.exe `
  overnight_quant/scripts/run_source_capability_audit.py `
  --capability minute_bar `
  --origin-source tongdaxin `
  --adapter mootdx `
  --require-hard-gate
```

命令不导入 collector，不建立 HTTP/TCP 连接，不读取或输出密钥值。它只报告问财 Key 和
Base URL 是否配置的布尔状态。输出使用稳定排序的 UTF-8 JSON，并包含
`network_requests_made=0`、`registry_hash` 和逐能力来源状态。

## 8. 状态示例

```text
腾讯报价：SOURCE_ROUTE_SELECTED
mootdx 正式分钟请求：SOURCE_UNQUALIFIED
AKShare hard gate：OPTIONAL_ADAPTER_UNAVAILABLE
问财缺少 Key：OPTIONAL_UNCONFIGURED
未知来源：UNKNOWN_SOURCE
空记录批次：PROVENANCE_BATCH_EMPTY
混合来源批次：MIXED_SOURCE_PROVENANCE_REJECTED
缺少发布时间：PROVENANCE_CONTRACT_INCOMPLETE
非法 SHA-256：PROVENANCE_HASH_INVALID
非法时间：PROVENANCE_TIME_INVALID
倒置时间：PROVENANCE_TIME_ORDER_INVALID
```

这些都是合同状态，不是联网成功、空数据或失败样本。真实成功、合法空数据、网络失败和
缺时间字段的实测对账属于后续独立阶段；B1 不通过静态注册表宣称来源已经在线可用。

## 9. 安全边界

- research/shadow only；
- 不连接券商，不生成交易委托；
- 不把注册表选择解释为数据就绪；
- 不允许 demo fallback 或跨来源拼接；
- 不安装 AKShare，不导入 Tushare/Ashare；
- 不修改连续三日门槛、正式来源配置或现有 collector；
- 不开始阶段 B2。
