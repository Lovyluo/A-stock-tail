# MINUTE-OPS-6 控制器身份修复

## 已复现与历史边界

基线1494004的真实进程对照：PS5.1/7、单次/差分实际CLI，直接启动通过；外层PowerShell命令文本包含CLI时，旧循环把普通父进程追溯成Python控制器并拒绝。修正后的相同对照通过。最初fixture准备错误及后续修正日志均保留，不以测试准备失败冒充生产原因。

历史10/27回执只有泛化错误，没有拒绝层PID/命令行，因此**历史具体拒绝层仍UNKNOWN**。本次确认代码缺陷不等于证明它就是历史根因；原同日intent、FAILED和NO_GO不改判，不恢复授权。

## 精确准入与停止追溯

- `minute_capture_preflight._query`从真实运行时传入当前PID、sys.executable、sys._base_executable和工作目录；不添加人工跳过开关。非正整数PID在子进程启动前拒绝。
- 检查进程必须由传入控制器直接创建；CIM快照须存在唯一PID，进程名/映像路径完整。解释器须匹配本次真实运行时路径，不能仅凭python.exe名称放行。
- 使用Windows原生CommandLineToArgvW解析参数并及时释放内存；只接受项目根目录的实际CLI文件（绝对/可核验相对路径）或精确模块入口，要求独立`--network`标志。文本中出现脚本名称、Python `-c`内容或同名异路径均不是控制器。
- 唯一可追加放行的祖先是已实测的Windows venv重定向器：base解释器子进程→本venv解释器父进程，二者除argv[0]外参数完全相同。最多两个控制器，不放行任意Python祖先。
- 普通启动父进程结束追溯，不列入允许控制器。只有其参数解析证明不是直接项目worker时，才避免把嵌套CLI文本误报为worker；直接worker/模块/PowerShell -File、未知或缺失身份仍拒绝。其他worker及其后代继续独立检查，不豁免全部祖先。
- 查询失败、进程消失、父链不明或循环、解释器/入口/启动链不符均失效关闭。前置查询预算仍10500ms；请求/准备/清理/封存预算、节点、count、顺序、单次intent和资格标准不改。

## 脱敏诊断

环境回执记录`controller_identity_version=protocol_controller_identity_v1`，按层给出stage、PID/父PID、进程名、classification、failed_item及命令行SHA。只显示已批准入口的名称，不保存完整命令行、参数值、密钥或授权正文。相关残留worker也仅留PID/父PID/名称/命令行SHA。执行失败与服务NOT_QUERIED继续分开。

## 离线证明及局限

新测试使用真实Python控制器、真实PS5.1/7子进程和实际CIM进程快照；任务/服务/时间/市场材料明确为合成，仅故障注入时变换快照。实际版本化CLI经实时身份检查进入已有test-only READY/GO/fixture worker与封存，拒绝路径零捕获worker。CI宿主时区仅在独立test-only信封中归一，保留原实时查询文件并引用SHA，不改写原查询，也不修改系统时区。

覆盖参数换序、引号/空格、相对/模块入口、普通父文本、错误PID、同名异路径、空命令行、进程退出、查询异常、未知父链、未授权worker；使用既有有界进程树回收测试自身。原准备/绝对截止及失败停发回归仍须通过。

这些仅证明软件进程识别和离线编排；不证明真实TCP预算、分钟语义或最终性。ROOT_CAUSE_UNCONFIRMED、CAPTURE_BUDGET_FEASIBILITY_UNPROVEN、资格0/3及空正式/研究/交易输出保持。修复仅Draft交付，本轮不联网、不采集、不校时、不创建任务、不合并、不发布；下一诊断日期必须另批。
