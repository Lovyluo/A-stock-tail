# T2.3 组合 audit 运行合同

本入口只执行市场宽度与派生行业宽度模块审计，不是正式资格采样，不生成计划任务或提醒。正式策略四阶段时点与资格门槛不变。

## 集中时间参数

| 参数 | 默认北京时间 |
| --- | --- |
| start_at | 14:37:00 |
| latest_start_at | 14:39:30 |
| feature_cutoff | 14:40:00 |
| collection_deadline | 14:40:10 |
| request_deadline_ms | 2000 |

collection_deadline 必须严格晚于 feature_cutoff。四个时间必须有 +08:00 时区并属于目标日；启动窗口不得接触 cutoff。最迟启动剩余预算至少覆盖最大请求计划 14 × 2000ms；真实请求仍受绝对批次截止与单请求硬截止的较小值控制。所有原生事件必须不晚于 feature_cutoff，延后的 collection_deadline 不接纳未来事件。

固定五股：000001、000333、600000、600519、601318。宽度请求 2 次；行业 5 次映射、4 个唯一 BK 成分请求，单页完整时共 9 次，因此基本组合为 11 次。只有声明 total 要求时继续连续分页；行业最大请求预算沿用 12，组合最大 14。不得沿用旧 13/13 摘要。

## 离线演练与生产前检

默认不联网。离线预检调用两个实际 Provider 的构造器并提供禁止网络的 transport，不使用文件存在检查冒充参数校验。Phase96 通过真实 CLI handler、两个实际 Provider、隔离 fixture transport 和独立 replay CLI 演练完整链路。test_only 路径不会授权采样。

离线参数检查（DATE 必须由当日日历/session 确认，不预先猜测）：

```powershell
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_t23_combined_audit.py --date DATE --validate-only
```

实际执行必须显式 --network，同时绑定：
- 当前干净工作树的完整 --expected-head。
- --completed-calendar 和 --calendar-file-sha256。
- --session-confirmation 和 --session-file-sha256。
- 新的 ignored cache --output 路径，已有 evidence 永不覆盖。

只有当日 completed calendar 与 session 独立校验通过，才可在 14:37 至 14:39:30 内执行一次。参数非法、错窗、SHA 不符、代码漂移、dirty 工作树、已存在输出或运行锁时，所有网络 worker 零调用。读取前置文件后重新检查实际启动时间。

## 隔离与截止

唯一候选 transport 为 eastmoney_dotnet_httpclient_v1，复用可终止 .NET worker。请求从进程启动前计时，超时由既有 worker 控制器终止进程树。无 retry、fallback 或跨来源/跨日期拼接。

两个模块分别生成 records 与完整原始响应。任一模块失败，组合返回 AUDIT_INCOMPLETE，失败模块 records 为空；另一个模块结果仅供同轮审计，不能自动成为组合成功。批次预算耗尽不启动后续模块。不可证明已发出的 HTTP 数量时 network_requests_made=null、upstream_network_activity=unknown，request_attempt_count 从实际调用列表重算。

独占运行锁防止第二份并发批次；共享排他原子写入器不改动。仅清理本次持有的锁。写入失败后不能声称网络请求为零。

## 证据与重放

使用独立 audit 包装合同 t23_combined_audit_evidence_v1，现有市场/行业 evidence 及来源版本不改写。独立 verifier 要求外部文件 SHA-256；从原始响应重放两个实际 Provider，核对来源、请求、时间、叶子数量和 records。合法失败 evidence 只证明文件完整性，不证明无响应失败的具体原因。两份 replay 输出必须逐字节一致，原始 SHA 前后不变。

```powershell
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_t23_combined_audit_verify.py INPUT --expected-file-sha256 SHA --output REPLAY
```

BK1239 invalid_count=2 保持原样；不得转换为平盘或删除。2026-10-08 已通过的行业 evidence 仅是模块验收材料，下一轮组合必须来自新一轮同日采集，不拼接历史成功结果。

## 安全与停止点

所有结果固定 audit_only=true、qualification=0/3、data_ready=false、hard_gate_authorized=false，candidates/tickets/orders 为空。组合 audit 通过也不自动增加资格计数。失败保留证据，不当日补跑。

## 2026-10-09 候选合同集成

固定提交 26e6a62963fa141cf5f95aad1c5b9c0dc21188a0 于 14:37:01 开始的单轮组合审计通过：宽度 2 次、行业 9 次，无重试或 fallback。BK1239 的 invalid_count=2 不变。

行业候选版本为 eastmoney_coreconception+board_constituent_breadth_v1。现有显式 envelope key collect_industry_records 保留，但实现只委托新的精确代码/成分 Provider；旧行业分页仅供私有历史回归，worker 不选用。Go/No-Go 检查五股 CoreConception 映射及四个唯一 BK 首个成分页；正式批次依据 total 检查所有连续页、原始响应及五股成员关系。

新合同：source_capability_registry_v7、source_capability_adapter_registry_v10、market_source_evidence_v5、market_source_verifier_v5、market_source_go_nogo_evidence_v5、market_source_go_nogo_verifier_v5。旧市场 v1–v4 和旧 Go/No-Go v1–v4 只作 audit-only，不授权新采样。T1 的 13 请求/来源版本冻结于原 requests 诊断合同，不跟随新候选矩阵漂移。组合 audit 的独立 v1 包装合同保持不变，旧原始 evidence 与 replay 不改写。

Registry 仍为 32 项，bound=8、candidate=7、资格=0/3；新行业不能由生产执行入口调用，未创建任务。provider_contract_complete=true 仅描述本轮两模块审计。派生宽度不提供 change_pct/relative_strength_pct，不能补造行业强度，完整快照仍会受现有 industry_strength_missing 门禁阻断；本轮不修改 readiness、不启用来源、不进入评分。

外部锚定原文件 SHA-256：93a7370819a9b152a7e617ea18d466f8942c03249d1a6bfccf519bbc833bab4b；evidence hash：bb0c17bec969cbb6b3f1dd9ecb0ae1b9c8afd77f58ab64f008a5050cd79a49eb；replay hash：9c05960df05ad1449b0e90ddbb5e422ded2c1c82e78ff9ddebcf36490b4046b7。新合同代码下再次双重离线重放仍逐字节一致，replay 文件 SHA-256 均为 06dcf26d5ec9de5f24ba2b3b8e2a7322c380cad8eb56adf3598cde488b0f1788。
