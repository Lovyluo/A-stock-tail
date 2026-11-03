# 工作包2：主动刷新观察与变化跟踪

观察合同：`manual_quote_observation_v1`。只在现有Dashboard增加一种观察方式；默认历史模式、零业务联网，不恢复扫描、综合评分、卖出/持仓动作或票据。腾讯用途依据仍为UNKNOWN；本轮开发及test-only演练不构成真实联网许可或上游用途授权。

## 操作与入口

仓库根目录，使用已有环境，不安装依赖：

```powershell
$env:OBSERVATION_QUOTE_NETWORK_ENABLED = '0'
D:\A-stock\.venv\Scripts\python.exe -m streamlit run overnight_quant/ui/dashboard.py --server.address 127.0.0.1 --server.port 8504 --browser.gatherUsageStats false --server.headless true
```

本分支预览为8504（已有该服务时直接打开，不重复启动）；原8502包1服务保留，不因本轮开发重启。进入“历史/离线观察”，选择“主动刷新观察”；默认联网关闭，刷新按钮禁用。查看历史、切换快照、设置条件、保存跟踪及比较本身均不联网。现有设置与历史原件保留，加载版本仍需用户显式选择。

真实验收另获批准后，使用同一入口启动前显式设置`OBSERVATION_QUOTE_NETWORK_ENABLED='1'`，再人工点击“刷新一次报价”；环境开关不是PM批准或用途依据。不能直接调用内部worker。一次点击不隐含下一次点击、失败重试或定时刷新。

操作闭环：①维护自选池，人工刷新一次；②查看实际价格、原生事件/响应完成时间、年龄、请求/条件状态及拒绝原因，保存绝对价条件和跟踪；③第二次独立成功刷新后，手工选前后两份快照及规则版本查看变化。仅一次成功显示“尚无可比较快照”。改条件保留规则版本，不能把改阈值解释成行情触发。

## 固定请求与时间合同

| 项目 | 本版边界 |
| --- | --- |
| 来源/调用 | 既有TencentDirectHttpProviders的quote能力＋quote Adapter；不调用quote/valuation双能力快照 |
| 请求 | GET `https://qt.gtimg.cn/q=sz000001,sz000333,sh600000,sh600519,sh601318`，实际参数次序由已有Provider规范化；每次固定五股、最多一条批量报价 |
| 范围外 | 自选池仍可保存现有身份校验支持的证券；未支持项保留并显示“本轮刷新范围未支持”，不扩请求 |
| 不允许 | 自动轮询、加载联网、重试、redirect、fallback、估值/日历/session/新闻/公告/分钟/资金流请求 |
| 频率 | 同观察存储共享原子在途锁；重入/并发零新增派发。至少10秒间隔，保守从上次确认退出/回收后起算，不靠页面按钮状态单独限频 |
| HTTP | timeout=5秒；它不是绝对总截止，连接/持续读取仍受父控制器的绝对截止 |
| 单调总预算 | 从控制器入口起10秒，包含准备、进程启动、连接、读取、原件写入、解析、清理、重放校验及结果存储 |
| 内部分配 | worker正常工作绝对截止不晚于控制器起点+8秒，既有有界进程树/管道清理共享至+8.5秒；余下最多1.5秒用于父端校验、最终回执/存储及解锁，任一超时失败，不给步骤重置截止 |
| 无法确认清理/收尾 | 保留PID、输出、失败材料及active.lock/cleanup-incomplete阻断；不自动删除、改名或解除。不能声称无残留或继续刷新 |

这是新的观察产品合同，不修改旧TCP/HTTP执行器参数或正式时间门槛。离线fixture耗时不证明真实网络预算。总预算使用单调时钟；真实时间只用于审计和报价年龄。本地文件操作均纳入计时并在阶段边界检查，不能把写出后超时判成成功。

## 价格、年龄与失败

- 只接受原Provider/Adapter通过的完整五股实际未复权报价（CNY/股），保留来源版本和原生时间，不给qfq收盘价改名。Provider的字段/身份/覆盖/原生时间拒绝行为不放宽。
- 每次页面交互重新取评估时间。`年龄=评估时间-原生事件时间`，0至60秒（含60）为年龄符合，大于60秒为陈旧；缺失/未来/时间倒序拒绝当前判断。这不是供应商时效保证、开市判断或成交许可；没有后台持续监控。
- 条件仍为用户正数有限绝对上下沿，下沿<上沿。有效时只显示“本次报价满足/不满足”；风险和可交易性始终NOT_ASSESSED，不生成综合动作。未设置仍为未设置；有效性不足不给比较价，不补零。
- 失败或缺股整批失败，不拼旧值补覆盖。已选旧快照可显示历史价格/时间，但本次判断为数据不足。新一轮失败不能被上次成功覆盖；选择旧快照也不能冒充本轮报价。
- 比较只使用两份独立成功、兼容且时间有序的已保存快照；它是明确的历史变化比较，不自动重置年龄，不证明连续路径/突破/当前触发。

## 存储与可追溯性

复用ObservationStore，设置与跟踪合同仍为`offline_price_observation_v1`；新快照及控制状态放在其对应historical/synthetic_test_only空间下的`manual_quote_observation_v1/`。不得指向持仓、正式records或orders目录。

每次新批次独立intent、源请求参数、原始响应base64/原始字节SHA、来源版本、请求开始/响应完成/处理完成时间、PID/退出/清理信息和最终回执均不可覆盖。原始响应在Provider解析前保存；读到的材料超过256KiB本地上限时标记不完整并失败。没有响应时保留实际异常，请求计数无法确认则UNKNOWN，不补造raw。文件SHA对应原样response.json字节，不与规范化内容hash混称。

成功快照重新运行原Provider＋quote Adapter验证，并绑定同批文件/原始响应hash。终态另以`manual_quote_refresh_finalization_v2`约束，不改变报价/年龄/限频合同：

- `receipt.json`仅为候选回执，成功候选状态为CANDIDATE_SUCCEEDED，不是最终成功。原始响应和所有已写回执不覆盖。
- 候选写入、解锁前后分别检查同一单调10秒截止；解锁未完成或超预算，不生成成功提交。`pending-finalization-*.json`不可覆盖保留，未完成提交会阻止后续刷新，不靠锁消失推断成功。
- 解锁通过后写入并重读`final-result.json`候选，记录实际阶段时间及hash；再将已写完、fsync且重读核对的私有`final-commit-candidate.json`通过同卷原子硬链接公开为`final-commit.json`，不替换旧文件。公开前后及最终确认仍检查原截止，同卷原子操作不可用即失败，无fallback。
- `committed_refresh_outcome`是控制器、成功快照列表与页面共用的终态解析：要求v2提交、批次/原件/候选回执hash、有限且有序的同一单调时钟阶段、预算及清理一致。`final-failure.json`优先否决成功；缺失、损坏、未提交、公开后超时或异常均不作为成功快照。失败回执无法落盘时保留/恢复阻断，不声明恢复完成。
- 页面重读成功列表，缓存的SUCCEEDED不能绕过丢失或失败的最终提交；失败批次不能作为第二份成功快照参与比较，其他合法批次与设置保持可用。

旧版只有早期成功回执的刷新候选不自动升级为v2成功，不回写旧原件或补造完成证明；包1明确历史快照及既有设置读取合同不变。内部哈希只证明本地材料一致性，不证明供应商使用权或来源资格。损坏材料明确拒绝，不自动修复。

## 明确的test-only页面演练

已有测试fixture文件仅用于注入；不能把真实批次或旧响应改标签。以下版本化harness复用实际页面和真实隔离worker，不是第二个工作台或仓库外启动胶水：

```powershell
$env:OBSERVATION_DEMO_ROOT = 'D:\Projects\A-stock-tail\local-archive\recovery\observation-manual-refresh-20261103\test-only-page-v2\isolated-store'
$env:OBSERVATION_DEMO_FIXTURES = '["D:\Projects\A-stock-tail\local-archive\recovery\observation-manual-refresh-20261103\test-only-page-v2\response-10.50.json","D:\Projects\A-stock-tail\local-archive\recovery\observation-manual-refresh-20261103\test-only-page-v2\response-11.00.json"]'
D:\A-stock\.venv\Scripts\python.exe -m streamlit run overnight_quant/tests/fixtures/manual_quote_observation_page.py --server.address 127.0.0.1 --server.port 8503 --browser.gatherUsageStats false --server.headless true
```

已有8503演练服务时直接打开。全程显式test-only，真实行情/NTP请求0。演练输入含原生测试时间，不会因再次打开而刷新时间；过期即显示陈旧，这是预期行为，不再自动生成新鲜输入。

## 真实验收待批单（本轮不执行）

最小目标为固定五股的两次独立人工报价刷新，最多2条批量quote请求，间隔至少10秒；不附带日历、session、NTP或其他能力。需要PM单独确认具体日期/窗口、用途依据UNKNOWN的适用性处置以及该有限请求授权；PM许可不替代上游使用依据。本轮不重复用途检索或联系供应商。

只验收真实字段/时间/完整覆盖、按钮限频、原件留存、条件解释和两快照变化。失败停止，不补请求凑成双快照，不扩大范围。即使通过，也不证明持续可用、策略有效、风险已审或来源正式资格。

qualification=0/3，data_ready=false，hard_gate_authorized=false；正式records/candidates/tickets/orders为空。仅允许隔离观察快照、条件及跟踪输出。
