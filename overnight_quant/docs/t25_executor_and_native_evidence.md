# T2.5 两进程执行器、原生依据与调度可行性

固定起点：`3e610a613c84d7e4198cc1edf245789da0004004`。2026-10-10。
本轮不进行正式采样，不创建任务/提醒。所有来源保持 candidate，S2资格0/3。

## 独立 audit 时间版本

`t25_audit_time_configuration_v1`：14:37启动，最迟14:39:30；
feature_cutoff=14:40:00，collection_deadline=14:41:05，
decision_time=14:41:10，execution_start=14:42:00。
execution_start只是边界，不授权执行。旧audit14:40:10和正式14:50四阶段合同不改。
新配置只用于新的调度预算/审计，不能重写已有记录的原生时间。

## 有界进程控制

- 执行器版本：`two_process_rate_limited_audit_executor_v1`。
- 全局排他锁放在系统临时目录，跨工作树拒绝第二个生产执行器；不自动清理未知锁。
- 一个协调器最多两个实际子进程，每主机最多两个。请求启动定义为派发前开始计时，
  包括Popen启动、输入输出读取；同主机派发起点至少相隔500ms，排队不从计时中抹除。
- 现有.NET HTTP worker直接复用，无requests/urllib fallback，不新增后台网络线程。
  两个IO线程只负责communicate/drain；协调器持有PID、终止进程树并等待退出。
- 单请求2000ms；批次剩余不足2000ms+500ms冻结+1000ms清理余量不派发。
  排队、release等待和分页依赖都消耗批次预算。所有错误停止继续派发；
  429/403/截止时取消并回收其他在途进程，保留失败和未派发项，不返回部分正式记录。
- HTTPS、原主机、200、零重定向、5MB、raw hash、请求原生时间顺序均检查。
  结果按request_id确定性排序，计时/PID保留真实性，不宣称运行审计文件逐字节相同。
- 该执行器尚未接入生产Provider或资格采样入口；公开调用必须显式network。
  audit错窗零进程。旧串行Provider/证据的时间字段不被新执行器改写。
- 全局预算模型按实际DAG、各主机限频、release及两个槽位逐事件推进，不用N/2。
  页面2依赖页面1成功；研究计划中的页数是假设，真实total未知时不授权采集。

## 来源依据表

本轮先离线读取既有完整response及T2.4索引，再只读检查上游自身页面。
不把旧Skill、旧Provider的incremental声明、数值趋势或等式当语义证明。

| 项目 | 已取得依据 | 仍缺失/状态 |
| --- | --- | --- |
| 四BK身份 | 既有CoreConception映射/成分证据；固定BK0474、BK0475、BK1239、BK1277 | 不扩大分类，不以名称推断代码 |
| 原生板块强度 | 官方[银行Ⅱ](https://quote.eastmoney.com/bk/90.BK0475.html)、[白色家电](https://quote.eastmoney.com/bk/90.BK1239.html)页面分别显示板块与成分股涨幅概念 | 页面动态值未加载；未证明具体字段/缩放/原生时间；`INDUSTRY_STRENGTH_NATIVE_SCHEMA_UNPROVEN` |
| 资金流单位 | [美的官方资金流页面](https://data.eastmoney.com/zjlx/000333.html)提供资金流展示；已有5股klt=1响应/原始hash见T2.4文档 | 页面展示单位不能自动证明fflow/kline字段单位 |
| 累计/增量、分钟区间 | 既有原始labels可见；没有units/value_semantics声明 | 未获得上游明确合同；`FUND_FLOW_SEMANTICS_UNPROVEN`；不新增真实转换 |
| 五分钟窗口 | 实际features需要5个连续增量区间；累计情形需6行含前置基准 | 旧单行不足，不替换精确目标、不跨日差分 |
| 发布延迟 | 预先固定3个观察时点，可记录目标行首次观察available_at | 有限观测不能证明最坏延迟；当前尚无新的真实观测 |

本轮上游文档只读查阅不是Provider成功证据；无可重放原始HTTP字节，不能伪造raw hash。
生产字段解析、Source/Adapter矩阵、hash及资格规则不变。

## 有限取证计划

`t25_native_source_research_plan_v1`，源API计划19次，上限20次；剩余1次禁止用于重试。
4个BK的stock/get每个至多一次，参数逐条保存；这些field名称是调查清单而非已批准映射。
固定五股资金流在14:39:55、14:40:05、14:40:35各一次，共15次；每个独立观察不是失败重试。
trade_date保持null，须由当日completed-calendar/session实际确认后才可实例化日期。
不创建自动等待/任务，不将15次观察拼成正式资格批次。

2026-10-10本轮未取得当日双合同，不运行资金流观察。上游说明仍不足，不耗尽请求预算。
本轮新增源API请求0；文档通过web工具只读查阅，不能宣称整个会话网络请求0，
也不把网页工具内部网络活动伪造为精确HTTP数。19次源API额度未消耗。
计划生成入口不发网络；未来取证仍须一次性登记叶子请求，原始响应/外部SHA/双重放后再判断。
观察helper只读标签、最新行和首次可用时刻，不解释数值单位或累计语义。

## 可行调度表

下表为最新启动14:39:30、每请求最坏2000ms、两槽、500ms限频、
映射→成分页、逐页依赖、4个原生BK调查槽、5股资金流release的事件模型。
包含1000ms终止清理与500ms冻结余量。不是来源发布时间的实证上界。

| 请求计划/目标行发布假设 | 最坏冻结时间 | 到14:41:05余量/结论 |
| --- | --- | --- |
| 单页20次，发布延迟0 | 14:40:07.500 | 57.5s，预算可行 |
| 23次（3个板块各预留第2页），发布延迟0 | 14:40:07.500 | 57.5s，分页依赖在目标发布前完成 |
| 23次，发布延迟30s | 14:40:37.500 | 27.5s，条件可行 |
| 23次，发布延迟55s | 14:41:02.500 | 2.5s，仅模型余量，不代表稳定真实采集 |
| 23次，发布延迟60s | 14:41:07.500 | -2.5s，不可行；不放宽截止 |

资金流尾段不能直接算5/2：同主机派发0、0.5、2、2.5、4秒，
最后2秒响应，再留1.5秒清理/冻结。真实目标若没有发布则拒绝。
仍缺最坏发布延迟、原生语义和字段证据；不得由预算可行推导来源资格成立。

## 真实CLI→本地响应进程→Provider→Adapter→PIT→features

```powershell
& D:\A-stock\.venv\Scripts\python.exe D:\Projects\A-stock-tail\worktrees\active\A-stock-v043-eastmoney-t2\overnight_quant\scripts\run_t25_offline_rehearsal.py --synthetic-test-only
```

20个请求槽真实启动本地隔离worker，仅回传显式合成记录；经现有私有test_only envelope
进入真实Adapter、normalize/materialize、readiness、features，不修改腾讯原始quote。
特征链保持原14:50合成合同；新14:40配置单独验证audit执行预算，不伪装成实际快照就绪。
合成软件成功仍对外data_ready=false、provider_contract_complete=false。
两次运行计时/PID真实，因此报告字节不同；确定性结果hash、snapshot/feature hash相同。
原子排他写入复用既有写入器，不覆盖已有文件。

计划零联网命令：同一入口使用`--research-plan-only --output <新的ignored路径>`。
本轮只运行Phase99及直接受影响专项；不跑本地全量。集中推送后由PR #30 CI全量一次。
未完成原生证据前不开始Day1、不合并、不发布、不激活source或创建任务。
