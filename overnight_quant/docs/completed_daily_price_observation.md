# 已完成日线价格观察 v1

独立策略 `completed_daily_price_observation_v1`，仅内部历史价格观察，默认离线。
不是买入建议、上涨概率、含分红总收益或可交易推荐。不修改旧策略、
Registry、资格、正式时间合同或生产绑定；不提供网络采集、任务、模拟成交或交易入口。

## 计算与准入

- 固定五股：000001、000333、600000、600519、601318。
- R是运行日期，严格排除R；D是输入有效完成日历中R之前最后一个交易日。
  日历和五股响应均须在R当日真实取得，不能用旧日历认定新日期的D。
- 每股须覆盖日历截至D的最后60个交易日，不允许采用更旧共同日。
- `R20 = 100 × (C_D / C_D_minus_20 - 1)`，按交易日索引回溯20日，需要21个收盘价。
  原Provider校验OHLC、qfq身份和有效数值，计算只用收盘价，不评分成交量/额。
  Decimal 34位精度计算后输出有限浮点百分数；降序，同值按规范证券身份升序。
- 所有60日记录先从原始响应重放校验，不信任外部提供的R20或ready标志。
  复用原qfq参数与`qfqday`键校验，不兼容未知来源版本。
- 完全一致的规范化重复合并，冲突记录拒绝；原Provider对原始响应重复日期的
  拒绝行为不变。缺股、缺日、错误身份/单位/版本、非法价格、跨批或未来可用时间均拒绝。

## 冻结输入

入口消费一个不可变static-source evidence文件及其外部SHA-256，另一个外部SHA锚定的
`completed_daily_frozen_batch_v1`清单。后者必须是实际批次封存记录，而非事后为旧材料
补造的时间证明。不同股票可有不同请求时间/raw hash，但所有输入绑定同一批次、
同一源文件、R及来源版本。此版本不开发真实采集或批次清单生成器。

清单字段：

| 字段 | 合同 |
| --- | --- |
| schema_version / contract_hash | 对应本版本`MANIFEST_SCHEMA`/`CONTRACT_HASH`，不能复用旧策略 |
| batch_id / run_date | 非空批次身份、R；必须匹配原件trade_date |
| source_file_sha256 | 原始evidence文件的真实字节SHA，不能用内部evidence_hash代替 |
| frozen_at | 有时区真实封存时间；所有响应读完≤冻结≤结果生成；不得伪造或回填 |
| provenance_kind | frozen_capture、historical_offline或synthetic_test_only；历史及合成须显式选择 |
| capture_bindings | 六个已捕获请求的capability、request_hash、raw_hash、source_version及同一batch_id |

六个请求是已有材料的离线重放次数（日历1、五股日线5），**不是本轮或未来联网授权**。
保留源文件中的请求参数、响应键、全部字段和逐请求观察/完成时间；输出引用源文件SHA、
逐股两端价格及来源/时间/hash。外部SHA能锚定字节，不能单独证明供应商语义或人工清单真实性。

15:00仅是原Provider的完成日归因标记，不解释为供应商最终发布时间。
qfq历史可能随复权基准重算，当前取回历史不代表当时可见版本；不补造复权/最终性证明。
原始日线中的volume仍执行既有Provider校验，但不参与公式或权重，不新增量额语义声明。

## 命令

正常离线消费（替换为实际封存文件及其外部SHA，不填占位数据冒充真实证据）：

```powershell
& 'D:\A-stock\.venv\Scripts\python.exe' -m overnight_quant.scripts.run_completed_daily_observation `
  --source-evidence <frozen-static-evidence.json> --source-sha256 <file-sha256> `
  --manifest <frozen-batch-manifest.json> --manifest-sha256 <manifest-file-sha256> `
  --run-date <R-YYYY-MM-DD> --output <new-isolated-observation.json>
```

明确历史回归（没有冻结清单时允许重放组件，但必须拒绝观察排序）：

```powershell
& 'D:\A-stock\.venv\Scripts\python.exe' -m overnight_quant.scripts.run_completed_daily_observation `
  --source-evidence <existing-original.json> --source-sha256 <original-file-sha256> `
  --run-date 2026-09-17 --historical-offline --output <new-history-replay.json>
```

合成演练额外要求`--synthetic-test-only`及一致的输入标记；不能进入普通路径。
默认无输入只报参数缺失，CLI没有`--network`选项，所有Provider调用使用离线replay transport，
不会回退到网络。输出采用现有原子不可覆盖写入；退出0只表示观察计算通过，拒绝退出2。
文件/外部SHA读取失败不生成伪证据；目标已存在保持不变。

## 结果与限制

- `observation_input_ready`只表示本观察合同输入通过，非正式data_ready。
- `observation_ranking`独立输出名次、两端日期/收盘、R20和证据引用；失败时为空。
- `risk_assessment=NOT_ASSESSED`：公告、停牌/ST/退市、涨跌停与可交易性均未审核。
- 始终qualification=0/3、data_ready=false、hard_gate_authorized=false，正式records及交易输出为空。
- 决策hash绑定策略/公式合同、R、实际60日交易日期与收盘价；审计时间、原件/清单SHA、
  捕获追溯另列audit。两次重放比较decision_hash及完整observation_ranking，审计生成时间可不同。
  可选信息/资金流不参与计算、准入或决策投影；原始字节变化仍须提供新的真实外部SHA。

## 本轮验收与最小下一步

9月17日原件可以重放日历及五股60日记录，但没有本观察合同的批次冻结清单，
因此历史观察必须拒绝`frozen_batch_manifest_missing`，不修补原件、不输出真实排序。
合成正向路径仅证明计算、失效关闭及确定性，不证明当前真实来源/策略可用。

下一步只需PM审核本离线实现，并单独决定是否批准一次固定五股同批日历/qfq冻结验证；
本轮没有真实请求预算或日期授权。不得因A（计算正确）通过便宣称B（研究可用）或
C（绩效有效）通过；不自动进入绩效验证、模拟成交、正式资格或全市场扩展。
