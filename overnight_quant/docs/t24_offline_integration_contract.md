# T2.4 离线接入合同与一次性剩余事项评审

基线：`275e5453ba6aa30bdecaccaeab671917d9ae09b7`。日期：2026-10-10。
本轮零新增网络请求、零采样、零任务、零 push/CI；只运行受影响专项。

## 新旧合同隔离

- 新记录合同：`s2_pit_integration_record_v1`。
- 显式映射合同：`eastmoney_security_board_association_v1`。
- 新合同只接收原生元字段，不给旧 Provider 记录自动补时间、published_at 或 hash。
- 旧记录继续按已有 PIT 合同处理；旧行业名称关联保留，禁止与新 BK 分类静默混用。
- Source/Adapter 注册表、Provider/evidence/verifier 版本和生产绑定本轮不变。
- 原四次 BK 查询仅证明没有取得响应，不证明字段无效或具体网络根因。
- 原生行业强度仍为 `INDUSTRY_STRENGTH_NATIVE_SCHEMA_UNPROVEN`。
- 合成记录全部为 `test_only=true`。演练可以报告 synthetic readiness 通过，但对外
  `data_ready=false`；不得把此结果登记为实际快照就绪或真实资格 evidence。

## 全链路字段表

| 层 | 必需字段与语义 | 验证结果/缺口 |
| --- | --- | --- |
| Provider | capability/origin_source/adapter/source_version；source；原生 event、真实 observed/available；request/raw hash | 原生 BK 强度及资金流语义仍未证明；不新增解析映射 |
| Provider 行业 | code、BK、分类体系/版本、映射证据 hash、名称、percent 单位 change_pct、breadth_ratio | 合成值可通链路；实际候选继续拒绝未证明强度 |
| Adapter | 精确 envelope/provider key、capability、版本、B1 provenance；不修补原始字段 | 演练调用现有私有 test_only 入口，selection hash 不冒充 production hash |
| normalize/PIT | 新记录 schema、完整四阶段时间合同、非新闻显式 published_at=null、逐子记录原生时间 | 新版本检查通过后仍走原 records_available_at，不替换 PIT 含义 |
| materialize | 规范证券身份 + 独立有证据的 code→BK；无名称猜测；保留映射与来源身份 | 腾讯原始 quote 不增加 industry_name；股票派生状态保存 industry_association |
| readiness | 原 12 分钟/14:50、60 qfq/可信历史日历、quote、market、industry 强度/宽度、fund、news 门槛不变 | 缺映射、冲突、混分类拒绝；合成固定五股全部通过，非真实就绪 |
| features | 使用行业 change_pct 及 breadth；资金流 last-five 仅接受已验证增量窗口 | 只调用 feature 构建，不调用策略评分、hard gates 或生成交易输出 |

名称只用于展示及合同指定的一致性检查，不作为新分类的关联键。
同名 BK 下的不同股票映射不再被 industry 名称去重吞掉。
不同映射事实不被“挑选最新一条”隐藏；同股冲突明确拒绝。

## 时间合同

| 新字段 | 对应既有 PIT 字段/含义 | 正式现有值 |
| --- | --- | --- |
| feature_cutoff | feature_event_cutoff，正式事件上限 | 14:50:00 |
| collection_deadline | observed/available 及批次完成上限 | 14:51:05 |
| decision_time | 验证冻结与决策时刻 | 14:51:10 |
| execution_start | execution_not_before，最早执行边界 | 14:52:00 |

`decision_cutoff` 是“该记录允许被用于决策的最晚时间”，不是特征事件上限。
新 Provider 必须原生声明为对应 decision_time；旧记录写 feature_cutoff 时仍拒绝，
不会在 Adapter/PIT 中重命名或覆盖来绕过 `decision_after_cutoff`。
非新闻/公告显式 null 发布时间只由新记录版本允许；新闻/公告仍必须有合法发布时间。
所有事件、观察、可用时间保留秒/微秒精度；不通过分钟截断接纳未来事件。
每个子请求保留原生时间列表及请求开始/完成/可用时间、request/raw hash；
逐条校验日期、顺序、原生截止，不用一个最大时间掩盖旧日期或未来记录。
静态映射时间只作分类审计，不能作为正式行业 feature event。

## 资金流已证实与未知

仅离线读取 `market_source_t2_audit_recheck_2026-09-30_f9e91bd.json`，未改写。
五股每份原始响应保存 100 行，标签从 13:04 至 14:43；请求 klt=1，
fields2=`f51,f52,f53,f54,f55,f56,f57`。响应 data 无 units/value_semantics 声明。

| 项目 | 已知 | 尚未证明 |
| --- | --- | --- |
| 单位 | 旧实现按 CNY 解析 | 原始响应没有单位声明，本轮未获得独立来源单位证明 |
| 累计/增量 | 旧实现直接取原值，并标 incremental，无差分 | 不能由连续数值、主力=大单+超大单或旧标签证明累计/增量 |
| 分钟标签 | 原始字符串精确到分钟；旧实现要求 event==feature_cutoff | 区间起止、目标行最早发布时间与最坏发布延迟 |
| 窗口 | 原响应100行；旧 Provider只返回目标一行；features取末5行求和 | 一行不能证明五个增量区间；若累计需第六条前置基准 |

因此保持 `FUND_FLOW_SEMANTICS_UNPROVEN`。旧证据中的 incremental 只是一项旧实现声明，
不是新版接入认可的语义证明；旧 Provider 仍 candidate，不激活。
合成演练展示累计 1000→1500 六行差分为五个100，总和500，而原五累计值之和6500；
差分 helper 为私有 test-only，检查同股、同日、连续分钟、前置基准及 CNY。
不把该演示用于真实数据转换；未证明的资金流不能进入 features。
目标行尚未发布、较早行、重复窗口、跨日基准和累计直接求和均拒绝。

原始资金流 raw hash 索引：

| code | raw hash |
| --- | --- |
| 000001 | 977da1d37a766dfbeda0bad95e9ae934c0034bf1f451245d5ca64b655318a045 |
| 000333 | 752c279c7d566f0e54e4a2358abab0f5964f0e55f0baf3e44dc0f11f6652efa3 |
| 600000 | 48f2141e0d985a22bc24989b06b451cd39f19cf224870eed01d1add10547b03c |
| 600519 | f7cac17192f43153a4f30a4805898c40413d7854a384633244fab60d218f808c |
| 601318 | 85cb1d86fb70d5440470cbf445d9b65938df0d49713a9b466c901db0d6d681a5 |

## 虚拟时钟与 PM 调度参数表

请求计划：market2 + mapping5 + constituents4~7 + native board4 + fund5 = 20~23。
四 BK 原生字段未证明，不创建真实请求计划/worker；这是离线预算模型，不授权调度。
单请求 2000ms 已包含进程启动；另留每 wave 100ms 编排及500ms冻结处理余量。
分阶段：市场/行业 → 等待精确资金流目标行 → 固定五股资金流 → 验证/冻结。
deadline/cutoff同日且严格有序；预算不足不静默追加页、不替换目标行、不延后截止。

| 情景 | 23请求最坏预算 | 结论/PM事项 |
| --- | --- | --- |
| 14:39:30启动，串行，audit cutoff14:40/deadline14:40:10 | 48.8秒，冻结14:40:18.8；超出8.8秒 | 不可行；即使更早完成前段，资金流5次10.5秒+冻结0.5秒仍超10秒尾段 |
| 既有 CloseWindowCollector max_workers=4，仅模拟 | 分组依赖：market→mapping→constituents→board quote；前段12.6秒，后段4.2秒+冻结0.5秒，剩余5.3秒 | 假设目标行在cutoff即发布；发布延迟>5.3秒仍不可行 |
| 同主机并发/阶段划分 | 不改变生产并发参数 | 需PM决定可用并发及限频；旧collector线程池不是新的可终止HTTP并发证明 |
| 原正式14:50/14:51:05合同 | 不修改 | 目标发布延迟、来源稳定耗时尚UNKNOWN，不能据模型批准真实资格 |

虚拟执行器真实推进阶段时钟并返回请求超时、分页预算耗尽、目标行未发布等状态。
所有模拟 `scheduling_authorized=false`；不新建生产 executor，不增加网络并发。

## 零联网演练

```powershell
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_t24_offline_rehearsal.py --synthetic-test-only
```

该命令不接受 --network，不实例化真实采集器，显式制造 test-only 五股夹具。
完整输出包含现有 Adapter 的7项 test-only选择、5个 BK 关联、实际 readiness 与 features、
snapshot/feature hash、串行和四worker预算；成功状态 `OFFLINE_CHAIN_EXECUTABLE`，退出码0。
TCP/requests 均由测试拦截，连续两次CLI输出逐字节一致，正式 data_ready始终false。

## 一次性剩余事项

- 代码：本轮完成证券/BK关联、显式元字段门禁、PIT贯通、目标资金流窗口和虚拟分阶段预算；
  生产 Provider 的新原生字段解析/可终止并发尚不实现，因为证据与参数未批准。
- 来源证据：四BK原生强度字段、百分比缩放、原生时点；资金流单位、累计/增量、标签和发布延迟。
- PM参数：同主机并发/限频、允许的分阶段计划、处理余量及发布延迟预算；不得放宽正式四阶段截止。
- 在这些事项一次性评审完成前不安排真实来源复验，不推送、不扩大资格。

最终专项结果和提交/备份 SHA 见《项目进度说明.md》及本轮恢复 manifest。
专项299 passed in 46.64s（含Phase98的42项）；最终依赖分组预算7 passed in 0.15s。
双演练snapshot hash：`0f9d4e7cb526071904c4d0250ba348e08681c0260d1e1b676df29026a5e97e79`；
feature hash：`2485224ed1f8d05a3bc9c5330fd542dc871216ba0f1fec9a5d5c61a380c363f0`；
两份final合成报告文件SHA：`c4f4450bf801ce3a373ae8cb1ba0cdf79e8473f7e325301c01385a724edd52d2`。
qualification=0/3；data_ready=false；hard_gate_authorized=false；candidates/tickets/orders=[]。
