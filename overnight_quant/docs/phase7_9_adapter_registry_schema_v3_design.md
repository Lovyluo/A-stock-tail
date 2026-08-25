# Phase 7.9 Adapter Registry Schema v3 设计

## 1. 目标与范围

本阶段只升级只读来源适配注册表的生命周期表达，并登记两个腾讯候选 Provider。它不创建
默认 transport、默认股票池或真实 Provider envelope，不联网，也不改变 collector、策略、
评分、资格规则、CI、计划任务和交易边界。

固定安全状态为：

```text
bound_count=0
data_ready=false
hard_gate_authorized=false
candidates=[]
tickets=[]
orders=[]
automatic_configuration_change=false
```

## 2. 三槽位生命周期

每个 B1 来源能力身份在 B2 注册表中恰好对应一行，并使用三个互斥用途的 key：

| 槽位 | 含义 | 可否由生产执行 |
|---|---|---|
| `provider_key` | 已选中并启用的正式 Provider | 只有状态为 `bound` 才可调用；本阶段全部为空 |
| `candidate_provider_key` | 合同兼容但尚未启用的候选 | 不可调用 |
| `legacy_provider_key` | 历史实现，仅用于追溯和审计 | 不可调用 |

`legacy_implementation_present` 不是输入事实。兼容审计输出若包含该字段，只能由
`bool(legacy_provider_key)` 派生；调用方提供的值与派生值不一致时拒绝。

状态只允许：

```text
bound
candidate_not_activated
contract_incompatible
not_implemented
optional_unconfigured
retired
```

状态与槽位必须交叉一致。例如 `candidate_not_activated` 要求 active key 为空、candidate
key 非空；`contract_incompatible` 只允许 legacy key；`retired` 三个 key 必须全空。

## 3. 腾讯候选

只登记两个候选：

| capability | candidate provider key | legacy provider key |
|---|---|---|
| quote | `tencent_direct_http_providers.TencentDirectHttpProviders.collect_quote_records` | `astock_client.AStockClient._tencent_quotes` |
| valuation | `tencent_direct_http_providers.TencentDirectHttpProviders.collect_valuation_records` | `astock_client.AStockClient._tencent_quotes` |

两行的 `provider_key` 均为空，状态均为 `candidate_not_activated`。候选 key 是身份保留值，
不能注入到其他 capability、origin、adapter 或 source version；candidate 和 legacy key 也
不能相同。

## 4. 生产门禁

生产完整矩阵必须与 B1 的 28 项身份一一对应。删除、增加、重复、换成子集、修改关键字段
或重新计算哈希均不能改变固定政策。provider key 必须是规范的点分 Python 符号引用；
空白、首尾空格和非规范字符串均拒绝。

公开执行遇到候选时返回：

```text
status=SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED
provider_called=false
hard_gate_authorized=false
data_ready=false
```

此门禁发生在 envelope 调用之前，因此候选 Provider 和历史 Provider 都不会执行。私有测试
入口可以构造独立 `bound` 行验证合同，但 scope 固定为 `test_only`，selection hash 根据
实际测试矩阵计算，不能冒充生产 hash。

## 5. 审计与哈希

Schema v2 生产 hash：

```text
4f274abcce88fedb425b9544e901d92da69a5cafa951fcda864a0c5fc06dd6be
```

Schema v3 生产 hash：

```text
1eb8114cf3aa68bf85473a67513dfdd7a3ab5b32a464a66d5c4664163a4f8b2d
```

变化原因固定记录为
`schema_v3_provider_candidate_legacy_slots_and_tencent_candidates`。B1 registry 内容没有
变化，其 hash 继续为：

```text
303db7cd50d8cc53e3729d69c7aeb3c053e203ba1885cecda1b10f0cdd321c69
```

离线审计必须输出 28 项完整矩阵、`bound_count=0`、`candidate_count=2`、
`legacy_implementation_present_count=13`、生产/selection hash、`network_requests_made=0`
及全部安全空输出。注册表输入换序不改变 hash；任意槽位或状态变化会改变测试 hash，并在
生产完整矩阵校验中被拒绝。

## 6. 证据兼容

Schema v3 不修改腾讯 v1/v2/v3/v4 原始证据及 verifier。既有 v4 文件必须继续在固定生产者
commit 工作树中，使用外部登记 SHA-256 完成离线重放；不能为了 schema 升级放宽证据合同，
也不在本阶段重新联网生成证据。

## 7. 后续决策

将腾讯候选移入正式 `provider_key` 属于 B2.2c 的独立 PM 决策。该阶段开始前仍须完成正式
绑定、配置、来源资格及剩余市场宽度、行业宽度和正式资金流门禁的单独审查。
