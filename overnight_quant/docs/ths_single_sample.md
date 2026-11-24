# THS 单日样本：仅待批、默认零网络

入口 `overnight_quant/scripts/run_ths_single_sample_manual.ps1`，合同 `ths_single_day_sample_v1`，独立许可 `ths_single_day_permission_v1`。只补一份题材归因样本，不是热点排名、核心股筛选或 Provider。旧报价/概念许可不能使用。完整代码及计划 SHA 以本轮仓库外增量索引为准；本文不生成执行许可。

## 可运行的离线入口

```powershell
# 默认：0 worker、0 HTTP；Python 描述入口另输出完整 PLAN 与 contract_hash。
& .\overnight_quant\scripts\run_ths_single_sample_manual.ps1
& D:\A-stock\.venv\Scripts\python.exe -m overnight_quant.scripts.run_ths_single_sample

# 只用明确 test-only 的本地响应；输出及 TestState 都必须新建、与用户材料隔离。
& .\overnight_quant\scripts\run_ths_single_sample_manual.ps1 `
  -TargetDate '<YYYY-MM-DD>' -ApprovedSha '<当前完整HEAD>' `
  -BatchId '<新test-only编号>' -Fixture '<绝对路径\test-only-fixture.json>' `
  -TestState '<新测试状态目录>' -Output '<新测试输出目录>' `
  -PythonPath 'D:\A-stock\.venv\Scripts\python.exe' |
  Set-Content -LiteralPath '<仓库外\returned-result.json>' -Encoding utf8
```

fixture 格式为 `{"status":200,"content_type":"application/json; charset=utf-8","body_b64":"<实际测试字节的base64>"}`。测试 worker 拒绝 socket/DNS；不访问源地址。文件输入仍有界；fixture 不产生实网许可，结果标记 `synthetic_test_only=true`。

## 真实执行待批单（不可执行草案）

| 项目 | 精确范围与未决事项 |
| --- | --- |
| 代码/计划 | 本 PR 最终提交及计划 hash 见本轮增量索引；合并/改版本后须重新绑定，不继承旧许可 |
| 目标日期 D | 用户另批一个已完成交易日，严格早于执行日；不以星期、系统日期或请求参数证明源事件日期，不额外查日历 |
| 实际日期/窗口 | **未批准、未填写**；同一北京时间日期、含起不含止；准备完成后也不得自行决定已获批准 |
| 请求 | 固定 `http://zx.10jqka.com.cn/event/api/getharden/date/{D}/orderby/date/orderway/desc/charset/GBK/`；GET 1次、1页；无 query/body、Cookie、凭据或批准原文；不跟重定向、不重试、不换源、不补页 |
| HTTP 层级/用途 | 明文 HTTP，无 TLS 来源认证；只保存个人内部样本，不公开传播。`source_use_basis=UNKNOWN` 与用户批准、知情确认独立；已知禁止、认证/访问控制绕过均不放行 |
| 输出 | 全新 `overnight_quant/data/cache/ths-single-sample/<batch>`；锁、持久清理阻断及不可覆盖 intent 防重复，不删除旧记录 |
| 限额 | 接收/保存最多1MiB；到上限但无法证明完整则拒绝。连接与读取最多5秒；10秒从版本化 PowerShell 入口计时，包含 Python/worker 启动、前检、封存、解析、回收、报告写完与实际检查。预留回收/收尾，不把 socket timeout 当绝对截止 |
| 预算细节 | 前检最多1秒；页 worker 的启动、联网、读取和封存共受父进程5秒工作界限，独立绝对回收最多1秒；外层 Python 在第9秒前结束，PowerShell 留最后1秒写回执并检查，必须严格早于10秒。调用方保存最终标准输出及人工锚定不是新增采集工作；不声称该外部留存耗时也在10秒内 |
| 批准记录 | 新许可绑定 schema、计划hash、固定SHA、工作树、D、新batch/output、窗口、最多1请求、single_use、个人内部用途、非公开传播、明确批准及独立布尔知情确认。引用复用已有本地消息字节/SHA及操作者核对格式；私有原文在仓库外，禁止虚构消息ID，哈希不是用户身份签名 |
| 停止 | 缺项、版本/窗口/输出不符：零HTTP；重定向、非200、读取/长度/编码/JSON/写出/回收异常：保留材料、无第二请求；清理未知保留锁/阻断。空结果只说明收到0条 |

待全部另行批准后才可使用下面命令；占位符不构成批准：

```powershell
& .\overnight_quant\scripts\run_ths_single_sample_manual.ps1 -Execute `
  -TargetDate '<另批D>' -ApprovedSha '<索引中的固定完整SHA>' -BatchId '<另批新batch>' `
  -Output '<当前工作树\overnight_quant\data\cache\ths-single-sample\新batch>' `
  -Approval '<仓库外\真实批准记录.json>' -ApprovalSha '<批准文件外部SHA256>' `
  -PythonPath 'D:\A-stock\.venv\Scripts\python.exe' |
  Set-Content -LiteralPath '<仓库外\新returned-result.json>' -Encoding utf8

& D:\A-stock\.venv\Scripts\python.exe -m overnight_quant.scripts.run_ths_single_sample `
  --verify-dir '<该输出目录>' --completion-sha '<completion.json外部SHA256>' `
  --returned-result '<仓库外\新returned-result.json>' --returned-sha '<该文件独立SHA256>'
```

## 能证明与不能证明

先封存 `response-bytes.json` 中的原样实体字节/base64、长度/SHA；真实读取分段另存不可覆盖 `received-*.json`，超时/中断只保留实际得到的部分，不补造完整响应。随后才按 HTTP Content-Type charset、UTF-8 BOM 或严格 UTF-8 可解码事实解析；URL 的 GBK 字样不作为编码依据，不 ignore/replace。保存请求日期、接收/封存时间与来源原值，不能互相代填。`response.json` 是解析及阶段记录，不是网络帧原件。

唯一成功准入还须核对调用方实际取得的最终回执、其外部 SHA、原子 completion 和被绑定的报告/原件。早期文件存在不表示调用成功；最终回执缺失/失败/超预算或清理未知均不接纳。合法失败材料可完整性通过，但捕获仍 FAILED。仅看材料的 source clues 不必等于指标准入。

本轮代码可运行和离线测试通过，不表示取得真实 THS 样本。`reason` 只标为可读来源归因线索；code/market/name、日期/时间、total 原样展示，规范身份、归因真实性、覆盖语义、价格口径及原生事件时间均未证明。永远不生成当日排名、核心股、多日持续性、龙头或买卖位置。未证实时间时保留“源数据时点未确认”；无总数写“收到N条、总覆盖未知”。

不改原 Dashboard、正式来源绑定或资格。浏览器视觉验收仍未完成：沿用既有零网络预览被策略拒绝的记录，本轮不重试、不换工具、不用 AppTest 或旧截图代替。执行环境根因仍 `ROOT_CAUSE_UNCONFIRMED`。

安全状态不变：`qualification=0/3; data_ready=false; hard_gate_authorized=false`；正式 records、candidates、tickets、orders 为空。本轮零真实HTTP、零NTP、无许可签发、无任务、无合并或发布。

## 限定边界复核

派发计数按实际证据保留：进入监督调用前，或监督器明确证明进程未创建时，可记0；进入调用后先记 null/UNKNOWN，只有可靠回执才能确认。后续异常保留已有计数、原始错误及清理状态，追加后继错误，不以 fixture 选项或统一异常回填0。缺失/不完整回执不能成为成功；清理未确认时使用现有持久阻断，零计数不是进程树清理证明。已读取的响应字节直接用于哈希，避免二次读失败丢失已取得的事实。

重放仍按原合同，不新增许可或计划版本：必要阶段须为有限数值、顺序一致，严格早于 intent 的 `work_deadline`；等于截止也不通过。核对 intent 总截止、工作截止，以及进程 I/O、硬截止和清理截止的一致性，保留原相对5秒及总10秒门槛，不增加容差。字节完整但阶段超时/未知时，完整性可通过，`capture_execution_result=FAILED`、`execution_time_verified=false`；明确列出 `execution_validation_errors`，不升级分析可用性。
