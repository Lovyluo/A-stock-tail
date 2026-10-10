# T2.6 东财原生语义取证结论

日期：2026-10-10；代码起点：`b9a8a5c3fc571e0a78836e8985fc181b79525ccb`。
PR #30 保持 OPEN / Draft。本轮只保存材料及研究结论，不修改生产解析器、注册表或时间合同。
本地研究工具位于 ignored cache，不进入 Provider、Adapter 或 CI。未运行 pytest，不推送、不触发 CI。

## 一次性裁决

| 项目 | 状态 | 依据及范围 |
| --- | --- | --- |
| 板块涨跌幅字段身份 | VERIFIED | 官方 BK 页面调用 stock/get；vendor 枚举“涨跌幅”=8，stock 映射使用 f170；不是成分股均值 |
| 官方 fltt=1 缩放 | VERIFIED | f170 与 f152 传入 p；数值除以 10^f152，并显示百分号 |
| 本轮固定 fltt=2 缩放 | UNPROVEN | 已捕获页面的 stock/get 使用 fltt=1；新 BK 请求没有响应，不根据价格等式或第三方示例推断 fltt=2 |
| 板块原生时间字段 | VERIFIED | 枚举“交易时间”=95，使用 f86；x 以 new Date(1000*f86) 展示。只证明字段身份，不证明四 BK 当日时点 |
| 四 BK 原生完整响应 | UNPROVEN | 两次派发、零响应；另两项按既有停发规则未派发。不得推断字段无效、付费或网络根因 |
| 分钟资金流单位 | VERIFIED | 官方 stock.js 的 getmkdata(klt=1) → drawchart_fs → 原始 arr[1..5]，图标题“单位:元”；另一官方图表将原值/10000显示为万元 |
| 分钟资金流字段名称 | VERIFIED | f52主力、f53小单、f54中单、f55大单、f56超大单；原始值进入对应图表 series |
| 累计还是区间增量 | UNPROVEN | 官方脚本直接画原始数组，没有明确累计起点/增量定义。H5 两市最后一行展示使用不同服务端端点，只作辅助，不能证明个股接口 |
| 分钟标签区间 | UNPROVEN | klt=1及 tradePeriods 的 b+1..e 证明展示分钟标签；没有证明标签对应 (t-1,t] 或 [t,t+1) |
| 五分钟窗口/前置基准 | UNPROVEN | 软件要求五个连续增量区间；若上游确认累计，则需六行含基准。条件性数学要求不是上游语义证明 |
| 目标行发布延迟 | UNPROVEN | 当日 session 未确认，15个发布观察请求均未派发；没有精确发布时间或最坏延迟结论 |

综合状态仍为 `INDUSTRY_STRENGTH_NATIVE_SCHEMA_UNPROVEN`、`FUND_FLOW_SEMANTICS_UNPROVEN`。
停止相应真实解析器开发。单位得到证明不等于 Provider 合同完整，不提高资格或 readiness。
净流入允许负数；累计净值下降若未来被确认应产生负增量，不能解释成计数器重置。

## 上游请求→字段→公式→展示链

1. [银行Ⅱ官方页面](https://quote.eastmoney.com/bk/90.BK0475.html)声明 quotecode=90.BK0475、code=BK0475，加载 bk2.js 与 vendor.js。
2. bk2.js 使用板块证券身份查询 stock/get，选择涨跌幅及交易时间。vendor 的 stock/get URL 为 `invt=2&fltt=1`，字段列表由所选展示项生成。
3. vendor 枚举8 → fid=[f170,f152,f169] → `p(f170,f152,f169,-1,"%")` → `value/10^precision` → 百分比。枚举95 → f86 → `new Date(1000*value)`。
4. [美的资金流官方页面](https://data.eastmoney.com/zjlx/000333.html)加载 stock.js；`loadchart1()` 调用 getmkdata，再传给 drawchart_fs。
5. production.quoteurl=`//push2.eastmoney.com/`；GET `api/qt/stock/fflow/kline/get`，klt=1、fields1=f1,f2,f3,f7、fields2从f51至f65。研究计划保留其前七个字段，不修改参数。
6. CSV arr[1..5]直接进入五个数列，不做求和或差分；series对应主力/小单/中单/大单/超大单；图注明单位元。
7. quotemoneyflowchart0715.js 的同一路径同klt绘图把 CSV 原值除以i；i=10000显示万元，i=100000000显示亿元。这是展示换算，不改变原生金额单位。
8. 原始分钟标签与图表轴生成不同：getPeriods遍历交易区间而不是解释CSV标签区间。不得从图上09:31起点推导精确的时间区间所有权。

原始脚本未执行：不会运行上游自动轮询、Cookie或埋点。仅单次下载公开静态材料。
`source_locators.json` 保留行号、UTF-8字节偏移、字符偏移及原文上下文；压缩JS用字节偏移定位，不伪造行号。

## 保存的官方原始材料

全部位于 `overnight_quant/data/cache/t26_native_semantics_2026-10-10/`。
每份 metadata JSON 保存完整URL、真实请求起止、长度、Content-Type、请求白名单/hash与代理模式；不保存代理凭据或Cookie。
获取范围：北京时间09:16:47–09:20:51。八次材料请求全部200、无重定向、各URL一次；TLS校验开启。
公开材料下载不冒充2000ms生产采集合同；生产源API研究仍使用既有.NET可终止2000ms进程。

| 原始文件 | 上游 URL | SHA-256 |
| --- | --- | --- |
| board_page.raw | https://quote.eastmoney.com/bk/90.BK0475.html | `3451107e6bfc930a036daf25ba3eab7139197248d15dde583f5d3d55c8e64191` |
| board_js.raw | https://quote.eastmoney.com/newstatic/build/bk2.js | `32cb7e67170a8fa75fecd19ebaeb1fdff9163161093f821210538d9068348958` |
| board_vendor.raw | https://quote.eastmoney.com/newstatic/build/vendor.js | `504cabef35a0f41ceb8ab4905c911ad317f47e36eabf82e3775bed30acc6d683` |
| board_moneyflow_js.raw | https://quote.eastmoney.com/newstatic/js/libs/quotemoneyflowchart0715.js | `5752bdae1f01a5c0ad0bbaf630074ba419f897199a615e26619f7d35836fcb70` |
| fund_page.raw | https://data.eastmoney.com/zjlx/000333.html | `8fefcd1f89c1995ec6da517f59a74218c5167aa5981e925597733e8f5cedcb39` |
| fund_js.raw | https://data.eastmoney.com/newstatic/js/zjlx/stock.js | `12bde3ac75cd2264a0d2b37a647c3099538dc4939ae3ccce34db3e9e612096e3` |
| fund_h5_page.raw | https://emdatah5.eastmoney.com/dc/zjlx/index | `19f991cb7b3d92cf1f34852b0bc5e0d97d98aea37adc0ebb3f636f07773cd1be` |
| fund_h5_js.raw | https://emdatah5.eastmoney.com/dc/Content/js/zjlx/index.min.js?v=1.0.10.1.4500 | `5468102b8629f6488b38dd579987e58ddf3d161a780ff354e5800d9d9464b182` |

第三方示例及财富号投稿未采用为字段语义依据。浏览工具内部HTTP数无法测量，单独记UNKNOWN，不把整个会话伪报为零网络。

## 四 BK schema 调查与请求账本

固化计划hash：`90b9558b7ae5645297ffa1da3db5bce018916540849356a2b81029bbfd1e30b4`。
四个BK各至多一次；剩余15次只用于通过双合同后的五股三波发布观测，未派发额度不自动转为重试。
本次 raw-schema scope 复用未修改的协调器core与真实.NET worker，不使用test_only transport，不进入14:50资格入口。

| BK | 实际派发北京时间 | 总耗时 | 结果 |
| --- | --- | --- | --- |
| BK0474 | 09:19:59.678211 | 1121.740ms | MARKET_SOURCE_REQUEST_FAILED，最内层HttpIOException，无HTTP响应 |
| BK0475 | 09:20:00.179757 | 898.557ms | 首请求失败后AUDIT_BATCH_CANCELLED，进程终止并回收，无保存响应 |
| BK1239 | 未派发 | 不适用 | 既有停发规则，不继续请求 |
| BK1277 | 未派发 | 不适用 | 既有停发规则，不继续请求 |

派发间隔501.546ms；峰值2；未重试、fallback或切换transport。
worker调用2，成功响应0；实际到达上游的HTTP数不能独立确定，记录null/UNKNOWN。
没有响应时 raw hash 为缺失，不为失败虚构raw hash；failure_reason_verified=false。
两次失败不证明字段无效或付费限制，不能根据异常链推断网络根因。

## 当日确认及发布时间观测表

生产腾讯quote一次批量请求，五股覆盖5/5，但event日期全部是2026-10-09。
2026-10-10 session状态 `MARKET_SESSION_CONFIRMATION_REJECTED`；独立外部锚定verifier为 `MARKET_SESSION_CONFIRMATION_EVIDENCE_VERIFIED`、session_confirmed=false、退出0。
独立验证通过的是合法失败记录完整性，不授权观察。未取得当日completed-calendar合同，不再发起多余日历请求。
系统时区China Standard Time，时间服务有最近成功同步；未额外测量时钟偏差，不宣称其数值为0。

| 波次 | 固定五股实际派发/完成 | 目标行/首次可用时间 | 状态 |
| --- | --- | --- | --- |
| 14:39:55 | 均未派发 | UNKNOWN | 当日session未确认 |
| 14:40:05 | 均未派发 | UNKNOWN | 当日session未确认 |
| 14:40:35 | 均未派发 | UNKNOWN | 当日session未确认 |

波次只是计划派发起点，不能宣称五股同时完成。未来首次观察到目标行的available_at只能给出可用时间上界，不能作为精确发布时间或最坏发布延迟保证。
本轮不创建下一日任务、提醒或自动等待；以后必须在新确认交易日重新取得前置合同。

## 证据索引与双重 replay

| 项目 | 完整 hash |
| --- | --- |
| semantic_evidence.json evidence hash | `d6334ec31e8562c38825235e61e24de9b988e0cfeda787cabd05c42c779d8dc7` |
| semantic_evidence.json 文件SHA | `9991522b78b0524a10c1015c223b8d27f77fa64877530127f547809dbc4f9e35` |
| external_manifest.json 外部SHA | `cdf981a40ceef4312ed14cba0fc7308105d57d746c2c2a5823a58feb30314726` |
| replay hash | `a2320743defbeab64ab9d4dae696ab84d3ed71a473311f34b78284f0fe790d6f` |
| replay_a.json / replay_b.json 文件SHA，各相同 | `abefb4d8f8a59e74ba466b6d2ca8f2be542e75e9c8034098152c09f86195221a` |
| boards_evidence.json evidence hash | `2fc11b40755d0a23ebd453c28974fbfc5a5a36830c061e527ffecde3bad39c35` |
| session_confirmation.json 文件SHA | `ba1426a7832a1069d692a578872b6a403a51c418f175c3114a655c2c0e50cdfa` |
| session evidence hash | `6ab49f346c813b59f4f3b245e229b88ab9403b765cf5cf894f8cb82e8fc370aa` |

研究 replay 独立绑定外部manifest SHA，逐文件重算raw hash、原始文件SHA、脚本字段链、计划/研究hash、session verifier和失败停发结果。
两次返回 `RESEARCH_EVIDENCE_INTEGRITY_VERIFIED`、退出0、逐字节一致；Provider validation仍false。
外部锚定不匹配必须返回EVIDENCE_INVALID。该研究检查器不是新的生产来源verifier，也不声称独立重放了无响应的网络失败根因。
资料代码原文/市场原始响应及运行记录只进入ignored cache，不提交Git。
恢复目录：`D:\Projects\A-stock-tail\local-archive\recovery\pr30-t26-20261010`，提交后manifest记录最终文档commit、bundle及逐资产副本SHA。

## 剩余缺口及最小后续方案

- 行业：需要固定四BK原生完整响应及与其参数完全一致的上游缩放依据；当前fltt=1前端依据不能静默用于fltt=2。若PM选择与官方页面一致的新合同，需显式版本化参数/精度字段，并重新取证，不在本轮执行。
- 资金流：需要对同一个股分钟端点累计/增量、累计起点和分钟区间的官方说明或明确上游运算；没有证明前不设计真实差分、不将原始累计值送入last-five求和。
- 发布观测：须待确认交易日、三波逐股实际派发及目标行记录；有限观测仍不能保证最坏延迟。两worker/500ms/2000ms停发规则不变。
- 已验证单位可供未来版本注明CNY，但不足以接入整个资金流Provider。本轮没有最小生产解析变更实施，也没有Provider激活。
- PM一次性决定继续索取明确上游合同、更换来源，或独立策略版本；不能在基础设施分支修改门槛来消除上述未知项。

```ini
qualification=0/3
provider_contract_complete=false
data_ready=false
hard_gate_authorized=false
candidates=[]
tickets=[]
orders=[]
```
