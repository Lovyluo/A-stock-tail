# 概念与成分单批观察：离线使用与待批计划

独立工作区，不修改原历史工作台、演示数据、用户设置或正式记录。默认不联网；本轮只有`synthetic_test_only`演练，真实采集未授权、上游用途UNKNOWN。它不是实时热门榜、龙头榜、交易信号或资格验证。

## 离线查看

1. 在仓库根目录启动现有Dashboard，并确保`OBSERVATION_QUOTE_NETWORK_ENABLED=0`。选择“捕获批次观察”；页面加载不派请求。
2. 明确填写批次目录、完整的返回报告文件、报告SHA-256和最终确认SHA-256，确认输入后点击“打开指定批次”。页面重读并校验启动票据、确认、回执、请求、原件及覆盖；未完成、失败、篡改或缺少最终确认均拒绝。早写的`execution.json`或报告本身不能证明最终完成。
3. 测试材料明确标记`synthetic_test_only`；真实批次若将来获准，仍是历史快照。接收/封存时间不是上游事件时间；当前来源字段身份、涨跌和成交额单位、原生时点均UNPROVEN，不计算真实涨跌扩散和活跃代表股，不作当前判断。风险和可交易性仍未评估。

在隔离测试目录可执行以下**test-only**命令（`<SHA>`为当前已核验HEAD，输出和状态目录均须为全新路径；保留完整stdout作为独立返回报告；本命令不连接来源）：

```powershell
& powershell.exe -NoProfile -File overnight_quant/scripts/run_concept_batch_manual.ps1 -Day 2026-11-05 -ApprovedSha <SHA> -BatchId demo-001 -Fixture overnight_quant/tests/fixtures/concept_batch_demo.json -TestState <NEW_TEST_STATE> -Output <NEW_BATCH_DIR> -PythonPath D:\A-stock\.venv\Scripts\python.exe
```

入口先完整写入`returned-report.json`，重新读取并在严格90秒内检查，再原子发布一份`final-confirmation.json`；只有后者完整发布才是成功，确认持久化是检查后的独立操作，不重跑采集。入口输出报告和确认各自的SHA供页面选取，不能用早期文件补造；哈希只用于核对字节，不能单独证明来源。明确外部锚点后也可零网络复核：

```powershell
& D:\A-stock\.venv\Scripts\python.exe -m overnight_quant.scripts.run_concept_batch --verify-dir <BATCH_DIR> --returned-report <REPORT_PATH> --report-sha <REPORT_SHA256> --confirmation-sha <CONFIRMATION_SHA256>
```

## 有界合同及真实验证待批单

**DRAFT_NOT_AUTHORIZED。** 新候选请求计划为`concept_batch_observation_v3`，许可格式仍为`concept_batch_authorization_v3`，但必须绑定新合同hash `6cb0e62da380c9cafb7950ae554dded368b4a286cea3b7cfa8e6d6c1d4f66a26` 和另行获批的固定SHA；旧许可不能沿用。先用`D:\A-stock\.venv\Scripts\python.exe -m overnight_quant.scripts.run_concept_batch --describe`离线核对版本和合同hash；此输出不是许可。

- **另行待批**：最终审核固定的完整代码SHA、单日日期、当日北京时间10分钟窗口、全新批次ID及ignored输出路径；核对干净验证树、活动锁、清理阻断、既有intent和启动票据。此前未启动记录不改写，旧日期或许可不能沿用或自动顺延。
- **分开记录**：`source_use_basis=UNKNOWN`；`status=USER_APPROVED_LIMITED_EXPERIMENT`、`user_approved=true`和独立的`user_acknowledges_unknown_basis=true`。个人内部结构验证不是上游授权或法律合规保证。两项确认及`single_use`均须为JSON布尔true。
- **范围**：`purpose=PERSONAL_INTERNAL_STRUCTURE_VALIDATION`。固定`scope`包括列表3页、成分3页、总请求6次上限、代码字典序最小BK、只选一个概念、禁止公开传播。实际字段和值以代码内`AUTH_SCOPE`为准。
- **明确拒绝**：已知禁止保持`PROHIBITED`，不能降为UNKNOWN；只有确无已知禁止时才能填`known_use_constraint=NO_KNOWN_PROHIBITION`，且`access_control_bypass`须为JSON布尔false。缺失、未知枚举、范围漂移、过期或旧版均不通过。
- **原文追溯**：`approval_evidence`须包含可定位原文的`thread_id`与`message_id`、原文摘录`verbatim_quote`及其UTF-8字节SHA-256（`quote_sha256`）。人工须先对照原文核实许可、知情和范围；入口只核对锚点及结构，不认证发言人或独立判断上游权利。原文随intent留存，原件、执行及返回报告携带许可状态、引用和摘要；页面明确展示UNKNOWN与有限用户许可。

固定端点`https://push2.eastmoney.com/api/qt/clist/get`，遵循系统/环境代理配置，不静默强制直连；禁止重定向、重试、自动换址或fallback。响应字节上限1 MiB，超限保留明确标记的不完整片段并失败。公共参数`pn=1..3,pz=100,po=1,fltt=2,fid=f12,fields=f2,f3,f6,f12,f13,f14,f104,f105,f124`；列表`fs=m:90 t:3 f:!50`至多3页；成分`fs=b:{BK} f:!50`至多3页。最多6次HTTP请求，不请求报价、新闻、历史、竞价或资金流。

v3计划在同一处冻结请求排序参数`fid=f12, po=1`与验证方向：列表按原始代码字典序严格降序，依据仅为单个真实列表第一页；成分也固定为严格降序，但尚待实样验证，列表不证明成分方向或上游长期承诺。采集和重放共用校验，逐页检查原始顺序、跨页边界、重复身份、总数漂移及实际行数。不在本地重排，不自动改方向。列表须取得`min(总数,300)`，超过300仅称有限覆盖；成分首响应声明超过300立即停止，不再派余页。

从降序分页所得有限集合中选合法BK代码字典序最小者，仅作为**固定结构验证样本**。它不代表全市场代码最小，也不是最强题材。真实概念类型、代码范围、字段单位、原生时点及用途仍需单独核验。

日期、窗口、用途和固定代码均另获批准后，调用形式如下；尖括号为待批材料，不是现有可执行许可。输出必须位于本入口的ignored状态目录下且此前不存在，授权文件必须外部SHA锚定；不得传入`--fixture`或`--test-state`。

```powershell
& powershell.exe -NoProfile -File overnight_quant/scripts/run_concept_batch_manual.ps1 -Network -Day <DATE> -ApprovedSha <FIXED_CODE_SHA> -BatchId <NEW_BATCH_ID> -Output <NEW_BATCH_DIR> -Authorization <APPROVED_AUTH_FILE> -AuthorizationSha <AUTH_FILE_SHA256> -PythonPath D:\A-stock\.venv\Scripts\python.exe
```

单页绝对上限10秒，包含子进程启动、连接、读取、原件封存及回收（预留最多1秒清理）；全批从版本化PowerShell启动入口开始单调计时，覆盖Python启动、采集、进程回收、报告写完及写后检查，严格小于90秒。PowerShell进入脚本前的宿主启动不在该合同内；单份最终确认在检查后独立持久化，不宣称其写入耗时属于90秒。清理未知保留阻断，禁止派发后续请求。请求完成、结构解析、身份/单位/时间依据、指标可计算及是否能用于当前观察分别记录。失败时保留真实原件、请求标记与回执，无法确认实际请求数为UNKNOWN；不把无效值补零或当平盘。v1批次不能自动升级为v2成功。

真实执行模式必须经版本化入口显式`-Network`，提供外部SHA锚定的v3单日许可文件，绑定本合同、批次、日期、窗口、代码、输出和预算；逐页复查许可及窗口。此处不提供可用许可文件。旧v2证据只按原hash和原升序本地规则离线核验，旧失败批次不重判，不补造新许可或成功确认。失败即停，不补采、不删除intent或清理阻断；任何结果不提升来源资格，不写正式records/candidates/tickets/orders。

许可文件和概念子进程管道均按严格UTF-8读写，不用系统代码页或替代字符。许可摘录上限为解码后UTF-8的4096字节；独立的完整JSON任务管道上限为4096字节，包含上下文和页参数。即使摘录合法，若协调任务或最大页任务超限，整批仍在首个HTTP请求前拒绝，不截断、不改原文哈希。合法非BMP字符可原样传输，非法UTF-8及孤立代理码点均拒绝。请求计划、代理、10秒/90秒预算与最终确认不变，旧许可不恢复。
