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

先用`D:\A-stock\.venv\Scripts\python.exe -m overnight_quant.scripts.run_concept_batch --describe`离线输出代码内冻结合同和合同hash；此输出为`DRAFT_NOT_AUTHORIZED`，不是许可。真实验收须另行固定**已审核的代码完整SHA**、日期及北京时间窗口、计划/合同hash、新批次ID和ignored输出目录、用途依据及其来源，并核对活动锁、未完成清理和既有intent；UNKNOWN不得冒充上游使用授权。不生成有效授权或自动顺延。

固定端点`https://push2.eastmoney.com/api/qt/clist/get`，遵循系统/环境代理配置，不静默强制直连；禁止重定向、重试、自动换址或fallback。响应字节上限1 MiB，超限保留明确标记的不完整片段并失败。公共参数`pn=1..3,pz=100,po=1,fltt=2,fid=f12,fields=f2,f3,f6,f12,f13,f14,f104,f105,f124`；列表`fs=m:90 t:3 f:!50`至多3页；在已捕获列表中按合法BK代码字典序选择最小者，仅作为**固定结构验证样本**，不是强弱候选；成分`fs=b:{BK} f:!50`至多3页。最多6次HTTP请求，首响应总数决定必要页，逐页核对实际行数、总数、排序和身份；不请求报价、新闻、历史、竞价或资金流。列表须取得`min(总数,300)`，超过300仅称有限覆盖；成分首响应声明超过300立即停止，不再派余页。实际概念类型、代码范围、字段单位、原生时点和用途仍需单独验证；不得仅因此实现存在便认定已证明。

日期、窗口、用途和固定代码均另获批准后，调用形式如下；尖括号为待批材料，不是现有可执行许可。输出必须位于本入口的ignored状态目录下且此前不存在，授权文件必须外部SHA锚定；不得传入`--fixture`或`--test-state`。

```powershell
& powershell.exe -NoProfile -File overnight_quant/scripts/run_concept_batch_manual.ps1 -Network -Day <DATE> -ApprovedSha <FIXED_CODE_SHA> -BatchId <NEW_BATCH_ID> -Output <NEW_BATCH_DIR> -Authorization <APPROVED_AUTH_FILE> -AuthorizationSha <AUTH_FILE_SHA256> -PythonPath D:\A-stock\.venv\Scripts\python.exe
```

单页绝对上限10秒，包含子进程启动、连接、读取、原件封存及回收（预留最多1秒清理）；全批从版本化PowerShell启动入口开始单调计时，覆盖Python启动、采集、进程回收、报告写完及写后检查，严格小于90秒。PowerShell进入脚本前的宿主启动不在该合同内；单份最终确认在检查后独立持久化，不宣称其写入耗时属于90秒。清理未知保留阻断，禁止派发后续请求。请求完成、结构解析、身份/单位/时间依据、指标可计算及是否能用于当前观察分别记录。失败时保留真实原件、请求标记与回执，无法确认实际请求数为UNKNOWN；不把无效值补零或当平盘。v1批次不能自动升级为v2成功。

真实执行模式必须经版本化入口显式`-Network`并提供真实日期、完整代码SHA、新批次/输出、外部SHA锚定的v2单日授权文件。授权须绑定本合同、批次、日期、窗口、代码、输出、预算、PM来源和非UNKNOWN上游用途依据，且`single_use=true`；逐页复查窗口。此处不提供可用授权文件，未解决日期、窗口、用途及字段语义前不得执行。失败即停，不补采、不删除intent或清理阻断；任何结果不提升来源资格，不写正式records/candidates/tickets/orders。
