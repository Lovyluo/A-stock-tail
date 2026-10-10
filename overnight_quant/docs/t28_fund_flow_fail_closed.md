# T2.8 资金流语义失效关闭与 PR #30 收尾

## 基线与边界

核对起点 `8b8514c7f228b84cd901c7d4bcd5a5f418916b04`，原分支及 active 工作树不变。
本轮没有行情接口请求、四 BK 采集、任务、提醒、新依赖、资格更新或生产绑定变更。
历史 evidence、T1/T2 材料及主目录五个保护文件不得改写。

## 原始值不等于已验证的增量

| 项目 | 依据/处理 |
| --- | --- |
| 字段名称 | T2.6 官方页面/脚本证据：主力、小单、中单、大单、超大单净流入 |
| 金额单位 | CNY，沿用已保存的官方展示链依据；不是新网络验证 |
| 原始值 | 保留原始五个文本 token 及对应有限数值；负值和零合法 |
| 累计/增量 | UNPROVEN，不求和、不差分 |
| 标签对应区间 | UNPROVEN，不声称单分钟增量 |
| 累计起点 | UNPROVEN |
| 修订规则/历史时点可用性 | UNPROVEN |

正常五股响应返回 `FUND_FLOW_SEMANTICS_UNPROVEN`：`records=[]`，五条原始审计行在
`audit_records`，`data_type=fund_flow_audit`，`eligible_for_hard_gate=false`。
缺失或非法金额不补零；任何解析/请求失败不保留部分正式记录。

worker 继续独立报告 transport 与 Provider 结果。传输成功可以为 true，Provider 完整性必须 false。
当前证据允许“完整性通过、Provider 准入失败”，不能得到 `MARKET_SOURCE_NETWORK_VALIDATED` 或资格日。
verifier 重放原始字节，核对审计行、状态和空正式记录；篡改语义/eligible/正式记录后重签内部 hash 仍拒绝。
文件级验收继续要求外部 SHA 锚定。它不为无响应失败提供独立的根因证明。

Adapter 公共路径仍拒绝未激活 candidate；私有 test binding 也不能将真实未证明资金流升级。
PIT normalize/materialize、nested readiness 与 features 输入过滤均拒绝当前及旧东财资金流。
features 只增加数据准入谓词，不改求分公式、权重或阈值。

现有离线演练只有显式 `test_only=true`、合成来源名、独立记录 schema 和合成语义 scope
同时匹配时才能证明软件行为。它不是实际东财证据；公共 Adapter 拒绝合成记录，verifier 不能以合成行
替代原始响应重放，外层 `data_ready=false`。不能靠单个 test_only 或 eligible 标签取得资格。

## 显式版本化

| 合同 | 当前版本/hash |
| --- | --- |
| 资金流 source version | `push2_fflow_kline_raw_audit_dotnet_v2026-10-10` |
| 市场 evidence/verifier | `market_source_evidence_v6` / `market_source_verifier_v6` |
| Provider verifier hash | `33c8a28e414ff935a6f331560b79e1325e36ce3888bd229c0a35307f87b38f4e` |
| Go/No-Go evidence/verifier | `market_source_go_nogo_evidence_v6` / `market_source_go_nogo_verifier_v6` |
| Go/No-Go verifier hash | `11cbfa28e1e98a5c6cd78027cf28179e3b0521bc84534ff9d3c940048519acb7` |
| Capability schema/hash | `source_capability_registry_v8` / `dc542eb72213e483827f7fd9f2b357f56f18bda2af101b36012b88f73d8756ec` |
| Adapter schema/hash | `source_capability_adapter_registry_v11` / `bb00c1311f8d214c9e2d088943675aac5a7980b69362ca791470c237beb0b9c1` |
| PIT admission contract | `close_confirmation_pit_v5`；12分钟/60日及时间门槛不变 |

固定矩阵仍为 32 项、bound=8、candidate=7、legacy=13。没有激活新 Provider。
旧 v1-v5 evidence 与旧 Go/No-Go 仅历史 audit，不授权当前资格。
v5 历史 verifier hash 固定为 `51829b7ba3512266c1d305de27128cebdbd062e04599e021b2122f464e320411`，
历史 Capability hash 为 `b7d5b05e24f56155cc5342c9dbad89c84493da8529c407acff50fb6b3f8f1e24`。
旧 Adapter hash `104d1df920a7cdf062e95ca5a77d33b442ad0c5472683b358ef66b1c0b964f11` 继续作为历史索引。
这些历史值没有批量替换；当前矩阵的固定断言按版本变更分别更新并保留抗篡改回归。

## 两个替代来源的限时官方资料调查

调查仅访问官方说明页，不调用行情接口；两个候选，不安装、不采购、不开户。
调查结论是当前公开材料不足，不是断言厂商没有相应产品。

| 待确认项 | 同花顺 iFinD | Wind Client API/WDS |
| --- | --- | --- |
| 指定分钟资金流字段/端点 | 未取得完整公开定义 | 未取得完整公开定义 |
| 累计或增量、起点 | UNKNOWN | UNKNOWN |
| 单位、分钟边界 | 目标资金流字段 UNKNOWN；分钟K线说明不能代替 | 目标资金流字段 UNKNOWN |
| 修订规则/PIT历史可用性 | UNKNOWN | UNKNOWN |
| 固定五股覆盖、发布延迟 | UNKNOWN，未调用接口 | UNKNOWN，未调用接口 |
| 授权/费用 | 正式权限需购买；HTTP token 与权限限制有官方说明，具体字段授权/费用未知 | 需厂商确认具体权限、合同与报价，当前公开页不足 |

官方来源：
- [iFinD FAQ](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/faq.html)：账号权限、数据字典工具、HTTP token；不是本端点增量定义。
- [iFinD 官方示例](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/example.html)：高频行情示例不能证明分钟资金流语义。
- [Wind Client API](https://www.wind.com.cn/portal/zh/ClientApi/index.html)：公开服务说明，没有本轮所需的完整字段/修订合同。
- [Wind WDS/API](https://www.wind.com.cn/portal/zh/WDS/sapi.html)：产品说明，未取得目标字段合同。

返回 `NO_DOCUMENTED_ALTERNATIVE`，停止继续扩展调查。PM 可选择取得原端点正式定义；
或另行授权语义明确的新来源及合规/成本审核；或独立立项不依赖该指标的策略版本。本轮不实施后两者。

## 剩余阻断与唯一下一动作

1. 四 BK fltt=1 新合同实际响应尚未完整验证；同轮行业强度/成分宽度仍未组成合格完整行业 Provider。
2. 资金流累计/增量、窗口、起点与修订未知；已消除候选代码的错误完整性/准入声明，但来源证据仍阻断。
3. 精确目标行发布上界未知；两 worker预算模型不是实际发布保证。

保留四 BK 各一次的已冻结计划，本轮零采集。下一次人工触发需重新核对代码 SHA、当日 calendar/session
和窗口，遵守 2000ms、停发、无重试/fallback；成功也仅证明该次候选响应，不累加资格。
停止等待 PM 对资金流方案与四 BK 新版本审计统一裁决。

## 验证与恢复

Phase101 覆盖正常/负值/缺值、重签提权、旧证据 audit、公开路径、合成隔离与直接 features 过滤。
受影响专项包括 Registry/Adapter、Phase89/91、.NET worker/两worker执行器、PIT/readiness 及候选链路。
不运行本地全量；稳定后集中普通 push 一次，GitHub CI 全量一次。
最终结果、代码 SHA、完整历史资产保护及 bundle/副本 SHA 见
`D:\Projects\A-stock-tail\local-archive\recovery\pr30-t28-20261010\manifest.json` 与 `delivery.json`。

```ini
qualification=0/3
provider_contract_complete=false
data_ready=false
hard_gate_authorized=false
candidates=[]
tickets=[]
orders=[]
```
