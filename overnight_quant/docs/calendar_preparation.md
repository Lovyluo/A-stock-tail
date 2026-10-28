# 单次 completed-calendar 准备

## 范围与版本

`run_static_source_validation.py --calendar-only` 是日历准备的唯一仓库入口。
该分支不会进入原全来源循环，不调用日线、新闻、公告、session、NTP或协议worker。
不带 `--network` 时零请求；联网模式要求显式日期、五股、批准完整代码SHA和全新ignored目录。
旧全来源CLI、Provider语义、S1有限资格、Registry、矩阵和时间/限频门槛不变。

- 准备/原始封存版本：`completed_calendar_preparation_v1`。
- 独立重放：`completed_calendar_preparation_verifier_v1`。
- 下游合同仍为 `completed_calendar_contract_v1`，只证明目标日前已完成日期；
  它不独立证明目标日已开市，仍须当日腾讯五股session及原有前置。
- 固定请求：GET `https://web.ifzq.gtimg.cn/appstock/app/fqkline/get`，
  `param=sh000001,day,,,260,qfq`，既有请求头与10秒timeout，最多一次transport调用。
  这是前置日历请求，不是协议矩阵业务请求；没有自动重试或fallback。

## 原始材料优先

联网前验证ISO日期等于当前北京时间日期、完整SHA等于HEAD、精确五股集合（无重复）、
输出位于ignored cache且不存在，以及实际Provider/记录校验函数的必需参数合同。
独占创建目录并写入intent后，才可能派发一次请求；并发/重复目录不会发送请求。

transport返回后，在HTTP业务检查、JSON解析和记录校验之前，先封存
`raw-response.json`：真实返回字节的base64、字节数、原字节SHA、请求方法/URL/参数/
白名单请求头、request hash、状态码/最终URL/响应头、请求起止与单调耗时。
复用已有临时文件fsync加原子hard-link发布设施，不覆盖原件；没有从解析行重建原始响应。
底层transport在返回响应前抛异常时，只记实际异常，不补造未取得的body。

随后使用既有绑定Adapter、`validate_static_provider_records(..., codes=固定五股)`及
completed-calendar语义验证。通过才写`completed-calendar.json`，关联raw封存与intent的文件SHA，
保留原始响应、Provider记录及Adapter执行。契约日期严格早于目标日，排序唯一。
外部文件SHA在写完后计算，不将不存在的外部锚点假写进合同。

失败写`failure-receipt.json`，包含精确阶段、异常类型/文本、请求次数及已取得raw引用。
回执不带可准入的日期列表/资格字段；原始封存、结果或回执写失败均退出2，不再请求。
回执本身写失败时stdout明确`failure_receipt_persisted=false`，保留已有文件，不宣称完整落盘。
独立verifier可确认合法失败材料的完整性，但不会把失败变为准备成功或采集许可。
缺失raw时只保留拒绝记录，不能声称原始完整性通过。

## 仓库命令

以下命令供**审核合并且main CI通过后**、独立新批次使用；本PR不授予联网或合并许可。
`$approved`必须填PM批准的合并完整SHA，不自动取最新main；`$day`为获批的当日日期。
`$out`必须是全新ignored目录，不得用删除/改名旧目录的方式解除阻断。

```powershell
$python = 'D:\A-stock\.venv\Scripts\python.exe'
$approved = '<PM批准的完整合并SHA>'
$day = '<获批日期YYYY-MM-DD>'
$out = Join-Path (Get-Location) 'overnight_quant/data/cache/<独立新批次>/calendar'

& $python -m overnight_quant.scripts.run_static_source_validation `
  --calendar-only --network --date $day `
  --codes 000001,000333,600000,600519,601318 `
  --approved-code-sha $approved --output $out
```

在固定工作树根目录执行，以上`$out`为全路径。相对`--output`按既有静态CLI规则
相对于`overnight_quant/data/cache`解析，不要再重复加此前缀。

退出0且状态`COMPLETED_CALENDAR_CONTRACT_VALID`后，使用stdout的`result_path`和
`file_sha256`作为下一命令的路径/外部锚点；不从旧记录复制SHA，不内联调用Python业务校验函数。

```powershell
& $python -m overnight_quant.scripts.run_static_source_evidence_verify `
  '<result_path>' --calendar-only --expected-file-sha256 '<file_sha256>' `
  --output '<全新ignored replay文件全路径>'
```

该命令离线重放真实原始字节，经相同Provider和绑定Adapter重算记录，再执行既有完成日历验证。
校验外部文件SHA、raw文件SHA、请求/响应一致性、intent身份、原始记录及派生日期；
不同输出文件的重放可逐字节比较。成功文件旁存在失败回执时拒绝完整准备成功。
验证退出0后再按既有命令**一次**执行`run_market_session_confirmation.py`、其独立verifier，
并将两份真实文件及外部SHA交给`run_minute_capture_preflight.py --prepare`。
前置通过仍不是PM授权，不自动启动矩阵。新批次、日期、SHA、时钟、环境及单次intent规则均保持。

## 离线验收和反例

Phase119使用实际日历CLI、独立verifier、session CLI/verifier及preflight CLI；
来源transport为合成fixture，环境查询为既有隔离子进程fixture，禁止外网/NTP。
没有给生产脚本增加跳过校验参数，测试材料只存在test-only临时目录。

- 原内联漏`codes`的TypeError在响应后发生且未封存；新CLI缺参数零请求，实际调用形状变化也在联网前拒绝。
- 响应后校验抛TypeError：仅一次fixture请求，先有可核对SHA的raw，再有失败回执；不生成完成日历。
- 非法JSON、空体、无日期、非200、连接异常、写失败、占用、重复启动均拒绝，不重试。
- 合成正常双合同贯通既有preflight，双重放相同；这不是当日开市证明、来源资格或真实数据就绪。

今日旧NO_GO、丢失原始响应的事实及原请求计数永久保留。
`qualification=0/3`，`data_ready=false`，`hard_gate_authorized=false`，
正式records、研究排序和candidates/tickets/orders为空。
