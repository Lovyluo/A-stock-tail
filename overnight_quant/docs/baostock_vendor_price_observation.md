# BaoStock供应商价格序列离线观察 v1

策略沿用`completed_daily_price_observation_v1`的R20公式，但来源合同独立：
`baostock_094_sdk_qfq_price_observation_v1`，SDK发行版0.9.4、日线d、前复权参数2、
数据层级SDK_RETURNED_UNMODIFIED。没有SDK导入/安装、联网Provider、生产绑定或资格授权。
不伪装成腾讯，不生成腾讯六请求清单，不将两种复权算法视为等价。

## 受限含义

**仅比较供应商返回价格序列；停牌与填充语义未证明，不代表期间可成交，也不代表未来上涨概率。**

固定五股，同批原件中的R严格排除；D按该批日历确定，不使用重放当天替换R。
重读外部SHA锚定的intent、请求参数/起止、SDK字段/字符串行及执行回执，
核对0.9.4、证券身份、日期唯一性、OHLC、行内复权2、同批完整日历与共同D。
120自然日原始范围必须匹配；最近60交易日完整，不退到更旧共同日。
SDK日历/日线日期重复即拒绝（与原诊断唯一性合同一致），缺股/缺日/错身份/时间/哈希整批拒绝。
沿用原计算：`R20 = 100 * (C_D / C_D_minus_20 - 1)`，34位Decimal上下文，
回溯20个交易日需要21个收盘价；R20降序，同值按规范证券身份排序。

tradestatus作为不透明字符串保留，包括0、1、空串或其他未知值；不解释、不删行、不生成中性状态。
结果audit保留全部原生行，选定60行的原生状态进入决策哈希。
风险NOT_ASSESSED；trading_status_semantics、suspension_fill_semantics及原交易日状态归属均UNKNOWN。
即使原件另附VERIFIED/eligible标志，本profile也不读取为语义证明或升级依据。
SDK顶层adjustflag为空可原样审计；请求参数和每条行内adjustflag必须为2，不能补写。

## 版本、追溯与命令

结果包含独立profile/contract/formula版本与hash，原生两端收盘价保持字符串，R20为百分数浮点值。
decision_hash绑定profile、公式、原R、选定60行及限制声明；原文件SHA和真实生成时间留audit。
换序不改决策；原生状态变化可改变决策hash但不改变UNKNOWN解释。
本入口只生成观察结果，不生成采集时冻结清单。未创建派生输入索引；输出audit.generated_at是真实重放时间。
两次重放比较排序、决策hash和限制；真实生成时间分别保留，不假装文件字节完全相同。

工作树根目录执行以下离线命令。`$anchorSha`必须先按既有独立增量索引核对，不能仅自算当前文件便当可信锚点：

```powershell
$batch = 'D:\Projects\A-stock-tail\reports\baostock-five-stock-20261102-v1'
$anchors = Join-Path $batch 'sample-file-anchors.json'
$anchorSha = (Get-FileHash -LiteralPath $anchors -Algorithm SHA256).Hash.ToLowerInvariant()
& 'D:\A-stock\.venv\Scripts\python.exe' -m overnight_quant.scripts.run_baostock_daily_observation `
  --batch-dir $batch --anchors $anchors --anchors-sha256 $anchorSha `
  --output 'D:\Projects\A-stock-tail\reports\baostock-price-observation-new.json'
```

输出必须在原件目录外且不存在；不可覆盖。默认缺参退出2，`--network`不受支持，所有路径均零来源请求。
控制台按当前输出编码显示；无法表示的字符使用可逆反斜杠转义，避免cp1252管道抛编码异常。
UTF-8/支持中文的终端仍显示原文；结果JSON始终UTF-8，中文限制、排序、决策hash及退出码合同不变。
`--synthetic-test-only`必须与原件test-only标记严格匹配；合成夹具不代表来源证据或资格。
拒绝时排序为空、退出2。成功退出0仅表示该供应商序列的受限观察输入可计算。

## 本次真实回归与边界

只读11月2日独立五股批次，各79行、共同D=10月30日，完整60交易日为7月31日至10月30日。
两次实际CLI离线重放结果及完整SHA见外部`local-archive/recovery/baostock-price-observation-20261102`索引；
旧单股PARTIAL、五股诊断原裁决及原件不改写。没有新增请求、SDK调用、收益回测、调参或模拟成交。
此前取得样本不代表持续可用、历史PIT或最终性已证明。
qualification=0/3、data_ready=false、hard_gate_authorized=false，正式records/candidates/tickets/orders为空。
仅隔离observation_ranking允许输出；未知状态进入本受限计算是独立产品范围，不放宽腾讯或旧策略门禁。
