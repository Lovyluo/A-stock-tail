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
| 执行单调预算 | 从控制器入口起10秒，包含准备、进程启动、请求/读取、解析校验、worker回收、原件封存、候选数据发布及发布后的实际完成检查；检查时间必须严格早于绝对截止 |
| 内部分配 | worker工作不晚于起点+8秒，共享有界回收至+8.5秒；其余最多1.5秒用于父端校验、数据封存/发布、解锁及发布后实际完成检查，不给步骤重置截止 |
| 最终确认持久化 | 只保存已观察到的执行结果，耗时另计，不属于上述10秒；不得请求、重采、补齐或改变执行结果。不再宣称包含确认的总耗时必定≤10秒 |
| 无法确认清理/收尾 | 保留PID、输出、失败材料及active.lock/cleanup-incomplete阻断；不自动删除、改名或解除。不能声称无残留或继续刷新 |

这是PM本轮明确批准的成功边界修订，不是把旧严格总预算合同追溯改判为通过；旧结论和原件保留。HTTP5秒、刷新间隔10秒、年龄60秒及旧TCP/HTTP生产执行器参数不变。执行预算使用单调时钟，真实时间仅作审计/年龄；离线成绩不证明实网预算。

## 价格、年龄与失败

- 只接受原Provider/Adapter通过的完整五股实际未复权报价（CNY/股），保留来源版本和原生时间，不给qfq收盘价改名。Provider的字段/身份/覆盖/原生时间拒绝行为不放宽。
- 每次页面交互重新取评估时间。`年龄=评估时间-原生事件时间`，0至60秒（含60）为年龄符合，大于60秒为陈旧；缺失/未来/时间倒序拒绝当前判断。这不是供应商时效保证、开市判断或成交许可；没有后台持续监控。
- 条件仍为用户正数有限绝对上下沿，下沿<上沿。有效时只显示“本次报价满足/不满足”；风险和可交易性始终NOT_ASSESSED，不生成综合动作。未设置仍为未设置；有效性不足不给比较价，不补零。
- 失败或缺股整批失败，不拼旧值补覆盖。已选旧快照可显示历史价格/时间，但本次判断为数据不足。新一轮失败不能被上次成功覆盖；选择旧快照也不能冒充本轮报价。
- 比较只使用两份独立成功、兼容且时间有序的已保存快照；它是明确的历史变化比较，不自动重置年龄，不证明连续路径/突破/当前触发。

## 存储与可追溯性

复用ObservationStore，设置与跟踪合同仍为`offline_price_observation_v1`；新快照及控制状态放在其对应historical/synthetic_test_only空间下的`manual_quote_observation_v1/`。不得指向持仓、正式records或orders目录。

每次新批次独立intent、源请求参数、原始响应base64/原始字节SHA、来源版本、请求开始/响应完成/处理完成时间、PID/退出/清理信息和最终回执均不可覆盖。原始响应在Provider解析前保存；读到的材料超过256KiB本地上限时标记不完整并失败。没有响应时保留实际异常，请求计数无法确认则UNKNOWN，不补造raw。文件SHA对应原样response.json字节，不与规范化内容hash混称。

成功快照仍重放原Provider＋quote Adapter，绑定同批原件/hash。`manual_quote_refresh_confirmation_v3`是唯一成功准入：

- `receipt.json`仅为候选回执，成功候选状态为CANDIDATE_SUCCEEDED，不是最终成功。原始响应和所有已写回执不覆盖。
- 候选写入、解锁、封存/发布及发布后检查均在执行10秒内。既有文件名`final-commit.json`在新schema中明确只是执行候选，不因其存在、无锁或早期回执而准入；原子发布只保证发布原子性，不证明完成时间。
- 发布后重读核对实际封存数据和元数据、确认已解锁，再读取实际单调时刻。仅当该时刻有限、有序且严格早于截止，内存才形成EXECUTION_SUCCEEDED；记录这个实际时刻，不用发布前时间替代。
- 然后只写一份最终确认`final-confirmation.json`：先按原不可覆盖方式写完、fsync、重读私有草稿，再同卷原子且不替换地公开。私有草稿与公开文件是同一份确认记录，不继续追加自证链。确认绑定批次、数据及执行候选hash、实际执行起止及耗时；自身I/O另计，不再重做采集或延长执行。
- `committed_refresh_outcome`供控制器、存储、页面共用，只认可完整有效的v3确认及其中实际执行检查通过的10秒合同。确认缺失/不完整/写失败按UNCONFIRMED拒绝成功列表；执行已明确超时/失败仍是FAILED。中断无法取得确认/总耗时则UNKNOWN，不伪造。
- 完整确认一经原子公开，之后进程退出、页面断开或迟到的退出诊断不撤销它；结果已提交不等于调用方一定收到返回。`caller_received_result`仍为UNKNOWN。`pending-finalization-*.json`保留，未确认的批次继续阻止新刷新，不自动解除保护。
- 正常返回分别给出`execution_elapsed_ms`、`confirmation_elapsed_ms`、`total_elapsed_ms`；兼容字段`elapsed_ms`为总耗时，不再受10秒总时长承诺。持久确认不自称知道自身最终写完耗时，重开读取时后两项为UNKNOWN。页面缓存不能绕过确认；正常已确认的历史批次及用户设置保持可用。

旧记录不得自动升级为v3成功，不回写旧原件、不补造完成时间、不改写旧严格合同的失败结论；包1历史快照及设置合同不变。内部hash仅证明本地一致性，不证明来源使用权或资格。确认原子方式不可用即未确认，无fallback。

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
