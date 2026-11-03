# 历史/离线观察工作台 v1

本页是尾盘工具恢复包1，不是R20产品改名，也不是实时尾盘选股。包2另见[主动刷新观察合同与操作](manual_quote_observation.md)：同一页面的独立观察方式，真实联网默认关闭，尚未真实验收，不改变本历史合同。
合同：`offline_price_observation_v1`。不评分、不生成买卖动作、票据或订单。

## 打开与操作

在仓库根目录使用现有Dashboard入口：

```powershell
D:\A-stock\.venv\Scripts\python.exe -m streamlit run overnight_quant/ui/dashboard.py --server.address 127.0.0.1 --server.port 8502 --browser.gatherUsageStats false --server.headless true
```

打开 `http://127.0.0.1:8502`，默认进入“历史/离线观察”。本模式提前返回，不执行旧工作台的扫描、卖出富集、持仓读取、自动刷新或票据动作；旧模式及其规则未删除或放宽。旧样式只作用于旧模式，防止遮住新页数值。

1. 维护显式自选池，保存。五股只是初始验收样本；证券范围复用现有身份校验，不扩市场。不支持的身份拒绝，缺报价的池内证券不删除。
2. 在“真实历史材料”手工导入既有腾讯 `market_session_confirmation_v1` 原件及来自原封存索引的外部SHA，复用独立verifier；不自动扫描证据目录或选择最新文件，不接受qfq日线。文件自算SHA只证明字节一致，不证明来源真实或授权。
3. 明确选择观察快照。查看代码、价格口径、来源、原生事件/实际获取/可用时间、批次及风险未评估。已有10月19日材料只表示那一时点，不代表当前行情。
4. 针对证券填写绝对价格上下沿，空白即未设置，无默认价。必须为正数有限值；同时填写时下沿严格小于上沿。分别显示未设置/满足/不满足/数据不足、比较价及阈值。精确等于阈值属于满足。
5. 加入跟踪，明确选第二份快照及两侧条件版本。无兼容第二快照时显示“暂无可比较数据”；历史价格变化不证明连续路径、突破或当前触发。条件版本不同明确提示，不能归因于行情。
6. 本地保存版本可显式加载，不按mtime恢复“最新”。修改规则保留旧版本；跟踪不是持仓，不读取或虚构成本、订单、成交。

## 输入与隔离

- 真实路径仅投影已核验session中的实际报价，保留来源/原始响应hash、外部文件SHA及原生时间；不补写或改写原件。已知quote来源可映射为 `UNADJUSTED_QUOTE_CNY_PER_SHARE`，仍要求行内价格单位为 `CNY_per_share`，不把BaoStock/腾讯qfq视作现价。
- 完整历史原件先过外部SHA、原verifier与批次身份检查。逐股再查有限正价、单位、身份、同批来源及 `event <= observed <= available <= captured <= inspection`；缺失、未来、无时区或矛盾时间不给条件结论。
- 没有实时有效秒数，任何 `require_realtime` 判断均数据不足，不以同日代替新鲜度。不存在实时入口、默认轮询、fallback或自动demo。
- `synthetic_test_only`必须显式选择独立空间，仅接受 `offline_price_snapshot_test_v1`。真实空间拒绝该格式；切换空间不能提升来源资格。测试行明确绑定批次、证券身份、价格/单位/口径、来源、各时点及模拟raw hash，不能冒充真实证据。
- 存储：`overnight_quant/data/cache/offline_observation/offline_price_observation_v1/{historical|synthetic_test_only}/`，仅保存导入字节/外部锚点和不可覆盖的设置版本。重复同字节导入幂等，同批不同字节拒绝；损坏、权限和写出错误明确失败，不删阻断。输入上限5MB/5000行是本地防护，不是五股上限或新来源请求合同。
- 同一证券、同空间/来源/口径、先后顺序成立的两次显式快照才比较。允许明确选择不同日期，但完整保留双方日期，不拼成另一批，也不声称连续历史。比较不自动挑选“当前最新”。

所有结果固定 `risk_assessment=NOT_ASSESSED`，条件满足不等于可交易。R20、分钟、VWAP、量额、资金流、市场/行业评分均不参与本模式。正式 `qualification=0/3`、`data_ready=false`、`hard_gate_authorized=false`，`records/candidates/tickets/orders=[]`。

## 验收与剩余限制

新专项与Dashboard实际AppTest覆盖条件/阈值/时点/身份/口径/混批/规则变更/重复导入/持久设置/零网络及旧动作隔离，并核对正式records/reports未写入。桌面1440×1100和窄屏390×844已通过实际浏览器操作；截图、原件引用、可信历史SHA、test-only材料及操作记录只在本轮仓库外增量索引，不提交运行数据。

真实历史只证明所选已封存报价可显示/比较；本轮已知真实批次只有一次观察，真实双快照跟踪未验收。合成双快照仅证明交互与计算。持续更新、真实时效合同、风险/可交易性、策略有效性及绩效均未接通。下一步须PM批准包2及其用途/请求/时效合同，不因包1通过自动联网或启用旧来源。

本轮完整提交/PR/CI、限定测试、页面记录及保护核对：`D:\Projects\A-stock-tail\local-archive\recovery\tail-observation-workbench-20261103\delivery-index.json`。
