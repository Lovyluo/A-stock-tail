# T2.7 候选合同收敛与 PR #30 收尾审查

代码起点：`92a8a308b539103a28c150953af999bc6cbd852b`，其生产代码起点为
`b9a8a5c3fc571e0a78836e8985fc181b79525ccb`。没有 reset、rebase 或提交历史改写。
本轮只新增离线候选解析、限定测试及文档，不修改生产矩阵、旧解析器、readiness、策略或资格台账。

## 行业强度候选合同

- 版本：`eastmoney_bk_fltt1_strength_candidate_v1`。
- 来源版本：`push2_bk_f170_f152_fltt1_candidate_v2026-10-10`。
- 合同 hash：`8c57607efce041b8963d03a50fa4d609f8454bbea72889e2aa8f18c1103a0a4c`。
- 四 BK 请求计划 hash：`87a4caaf9cdb5b97acb6febffcaef4dd7484d4613ece11c1c5300263658d1086`。
- 官方依据：T2.6 保存的 vendor.js 原始文件 SHA
  `504cabef35a0f41ceb8ab4905c911ad317f47e36eabf82e3775bed30acc6d683`，字段及格式化函数定位见既有 `source_locators.json`。
- `GET https://push2.eastmoney.com/api/qt/stock/get`；`invt=2`、`fltt=1`；
  `fields=f57,f58,f86,f170,f152,f169`；`secid=90.<BK>`。
- 固定四板块：BK0474、BK0475、BK1239、BK1277；代码和名称必须与已批准的显式映射一致，不能按名称猜测代码。

| 原始字段 | 候选合同 | 缺失或异常 |
| --- | --- | --- |
| f57/f58 | 精确板块代码/名称；只允许四 BK | 整条候选拒绝，不补代码/名称 |
| f170 | JSON 有限数值；主板块原生涨跌幅字段，不是成分均值 | 拒绝字符串、bool、NaN、Infinity、null、缺失 |
| f152 | 严格整数精度；候选实现保守接受 0–8 | 未知精度拒绝；该范围是实现子集，不宣称上游完整定义 |
| f170/f152 | `change_pct = f170 / 10**f152`，单位为 percent；1.23 表示 1.23% | 不沿用 fltt=2，不补零、不推断缩放 |
| f169 | 官方格式化函数用于正负颜色；请求保留此字段 | 不作为数值计算的替代字段 |
| f86 | Unix 秒，转为北京时间且保留秒精度 | 不截分钟；错日期、未来、晚于 cutoff 均拒绝 |

官方 `p` 对数字且正精度进行除法；零精度保持原值。转换采用 Decimal 缩放，再输出有限数值。
未计算 `relative_strength_pct`。未来若新增，必须同时验证同口径市场基准与双方原生时点/追溯；不得从当前旧 fltt=2 基准静默推导。

`parse_captured_board_strength()` 没有 transport、默认股票池或网络构造器。
调用方必须提供请求、原始响应字节、外部预期 raw SHA、HTTP/最终 URL、版本和全部采集时间。
请求 hash、raw hash、精确参数与来源身份重新核对；拒绝重复 JSON 键、非 200 和重定向。
HTTP 起止必须位于进程起止之内，进程启动至完成不超过 2000ms；接收完成不晚于 observed_at，
且 observed_at <= available_at <= collection_deadline；所有日期/时区一致。
事件必须 <= HTTP 完成及 feature cutoff，collection deadline 必须严格晚于 cutoff。

成功状态是 `INDUSTRY_STRENGTH_CANDIDATE_PARSED`，只返回 `audit_records`；
`records=[]`、`source_qualification_verified=false`、`provider_contract_complete=false`。
合成输入显式 `test_only=true`，其通过只证明软件行为。外部 raw 锚定只证明捕获字节未变，不自行证明来源真实资格。
该新 source version 不在生产 Adapter 矩阵中，公开执行入口拒绝且 Provider 调用为零。

## 后续真实验证范围

本轮不发起来源 API 请求，不等待交易日，不安排任务/提醒，也不生成真实成功 evidence。
只有当日 completed-calendar/session 均独立通过后，才允许另行人工执行预先固化的新版本四 BK 各一次计划。
复用两 worker、同主机500ms、2000ms含进程开销、批次剩余预算及失败停发规则。
新 fltt=1 计划不是旧 fltt=2 失败的 fallback；不能拼入旧快照。四 BK 原生覆盖仍为 UNPROVEN。

## 资金流追加核查与决策表

暂停累计/增量解析变更和15次发布观测。核查范围小于60分钟，复用八份原始官方材料，
另只读检索同一端点正式说明和查看官方个股资金流页面；没有发起个股资金流 API 调用。
域名中的财富号投稿、第三方 Skill、走势图、数值趋势、不同 H5 两市端点均不作为本端点语义依据。
搜索无明确依据不等于证明上游没有内部定义。

| 事项 | 裁定 | 具体依据/缺口 |
| --- | --- | --- |
| 单位 | VERIFIED：元/CNY | stock.js getmkdata(klt=1)→drawchart_fs，原始 arr[1..5] 与单位标签；SHA见T2.6 |
| 累计或区间增量 | UNPROVEN | 绘图直接使用原始数组，没有明确累计起点或区间计算 |
| 累计起点 | UNPROVEN | 没有同端点开盘/午间/交易日基准定义 |
| 分钟区间左右边界 | UNPROVEN | tradePeriods 仅生成显示轴，不证明 CSV 标签区间 |
| 修订、回补、最终态 | UNPROVEN | 脚本“修正”仅是过小负值的显示符号修正，不是历史资金值修订规则 |
| 五增量窗口/基准 | UNPROVEN | 五个增量区间或六累计行含基准仅为条件性数学要求，尚不能选择真实解析方案 |

| PM 选项 | 下一动作 | 授权/成本/接入待确认 |
| --- | --- | --- |
| A：取得同端点正式说明 | 获取明确 API/数据定义，含单位、累计起点、分钟区间、修订规则；再确定解析和回归 | 官方说明取得渠道、使用许可与时点保证 UNKNOWN；不保证免费或可取得 |
| B：语义明确的独立来源 | 单独 candidate/身份/授权/真实 evidence/verifier；不能以新源补旧批次 | 数据授权、费用、五股覆盖、延迟、历史修订、技术接口均待确认；本轮不采购/安装/接入 |
| C：独立策略研究版本 | 单独立项研究不依赖该指标的特征/策略 | 研究目标、回测和新门禁由 PM 单独决定；不能降低当前门禁来通过本 PR |

建议唯一下一动作：PM 对上述 A/B/C 与新版本四 BK 审计计划作一次统一裁决。
在获得明确语义或新的独立项目授权前停止资金流解析/发布观测，不逐字段扩建框架。
负净流入合法；即使未来证明累计，下降也不能按计数器重置处理。

## PR #30 收尾审查

| 审查项 | 结果/复现依据 |
| --- | --- |
| 默认零联网 | 新模块仅解析 supplied capture/生成计划；Phase100 禁止 socket/requests；Phase99 默认 run 不构造 transport |
| 未资格不能激活 | 32项矩阵、bound8/candidate7不变；Phase97三项candidate公开入口调用0；新版本不注册 |
| 合成路径隔离 | Phase98/99 explicit test_only，软件 readiness 通过但外层正式 data_ready=false；不是市场证据 |
| 失败不升级 | Phase94/99超时/HTTP错误/停发回归；新解析异常 audit_records/records均为空 |
| 子进程清理 | Phase94实际.NET超时及Phase99阻塞/并发取消/迟到写入测试，进程必须回收；没有本轮外网 worker |
| 旧证据 | 新模块没有改旧schema、verifier、版本、原始证据；旧合同回归及外部锚定重放，不重新生成历史证据 |
| 完整来源/策略接入 | 未成立；候选解析通过不代表有完整行业快照、资金流或正式资格 |

### 集中阻断，不在本轮扩大实现

1. 行业真实四 BK fltt=1 响应、原生时点、可用性尚未验证；新值尚未与同轮成分宽度形成已验证完整 Provider。
2. `market_source_providers.collect_fund_flow_batch()` 旧候选路径仍将单行值声明为
   `minute_interval_net_flow/incremental` 且 `eligible_for_hard_gate=true`；没有本端点语义证据支持。
   生产 Adapter 尚未激活，T2.4新快照记录合同会拒绝真实未证明资金流，但旧来源 audit 路径不能因此宣称语义完整。
   Phase89 合成响应可复现该旧声明；这是候选代码阻断，不是上游增量语义证明。本轮不重写旧证据或新建差分解析。
3. 五个连续增量区间的真实目标行/发布延迟和修订规则未知；两 worker预算模型可行并不证明真实发布保证。
4. 合法失败证据完整性通过与来源/资格通过仍必须区分；无响应错误不可独立证明根因。

PR 可以作为未激活候选基础设施审阅，但以上事项阻断来源资格、完整快照和正式启用。
不合并、不创建Release、不开始Day1。

## 验证与恢复

专项结果、最终提交、唯一CI及完整源文件保护/恢复hash由本轮恢复清单记录：
`D:\Projects\A-stock-tail\local-archive\recovery\pr30-t27-20261010\`。
T2.6及此前证据不改写；原始材料完整hash继续留在T2.6文档与已有外部manifest。
本地不运行全量；稳定后集中普通push一次，由GitHub CI运行一次全量。

```ini
qualification=0/3
data_ready=false
hard_gate_authorized=false
provider_contract_complete=false
candidates=[]
tickets=[]
orders=[]
```
