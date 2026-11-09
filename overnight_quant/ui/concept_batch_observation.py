"""Explicitly selected offline batches, separate from user settings and demo state."""
from overnight_quant.data.concept_batch_observation import load_batch


def render(st):
    st.title("概念与成分 · 捕获批次观察")
    st.warning("离线查看指定批次；原生行情时点未确认，不代表实时行情、当前强势或买卖建议。")
    st.caption("仅打开明确指定的成功批次，不自动刷新、不连接来源。风险和可交易性未评估。")
    directory = st.text_input("批次目录", key="concept_batch_path")
    report = st.text_input("命令返回报告文件", key="concept_batch_report")
    anchor = st.text_input("返回报告外部 SHA-256", key="concept_batch_anchor")
    confirmation = st.text_input("最终确认外部 SHA-256", key="concept_batch_confirmation")
    if st.button("打开指定批次"):
        st.session_state.pop("concept_batch_selected", None)
        try:
            st.session_state["concept_batch_selected"] = {
                "result": load_batch(directory, report, anchor, confirmation), "directory": directory, "report": report,
            }
        except Exception as exc:
            st.error(f"批次不可用：{type(exc).__name__}: {exc}")
    selected = st.session_state.get("concept_batch_selected")
    if not selected:
        st.info("尚未选择有效批次。")
        return
    result = selected["result"]
    st.caption(f"已载入：{selected['directory']}；返回报告：{selected['report']}")
    if result["synthetic_test_only"]:
        st.warning("测试数据 · synthetic_test_only；不是实际行情。")
    if result.get("experiment_permission") is not None:
        st.warning("上游用途依据 UNKNOWN；用户仅批准本次个人内部有限结构验证，并已知悉该未知状态。"
                   "不是上游授权或法律合规保证，不得公开传播。")
    listing, members = result["list_coverage"], result["member_coverage"]
    st.subheader("覆盖与时点")
    st.write(f"批次 {result['batch_id']}｜采集完成 {result['capture_completed_at']}｜原生事件时点未确认")
    scope = "列表完整性按返回总数核对，不代表全市场" if listing["complete"] else "有限覆盖范围，不代表全市场排名"
    st.write(f"概念列表 {listing['received']}/{listing['total']}｜{scope}")
    st.caption(f"列表字段结构可读 {result['list_valid_numeric_rows']}｜无效/缺失 {result['list_invalid_numeric_rows']}")
    st.write(f"成分 {members['received']}/{members['total']}｜按本批返回总数核对")
    st.write(f"请求成功：是｜结构可解析：是｜身份及单位依据：{result['field_basis']}｜当前观察：不可用")
    st.subheader("已覆盖概念列表")
    st.caption("原始字段不构成强弱排序；单位、身份和事件时间须另行核验。")
    st.dataframe([{"代码原值": r.get("f12"), "名称原值": r.get("f14"), "涨跌原值": r.get("f3")}
                  for r in result["list_raw"]], hide_index=True, use_container_width=True)
    st.subheader("固定结构验证样本与成分")
    st.write(f"{result['selected_bk']} · {result['selected_name']}｜固定结构验证样本：覆盖范围内代码升序第一，非强势候选")
    if result["synthetic_test_only"]:
        change = result["selected_daily_change"]
        st.write(f"合成当日表现：{change}%" if change is not None else "合成当日表现：资料不足")
        counts = result["counts"]
        st.write(f"合成成分：上涨 {counts['up']}｜下跌 {counts['down']}｜平盘 {counts['flat']}｜无效 {counts['invalid']}")
        st.write("合成成交额排序的活跃代表股（不是龙头）：")
        st.dataframe(result["active_representatives"], hide_index=True, use_container_width=True)
    else:
        st.info("涨跌及成交额单位未证明，不计算当日表现、扩散或活跃代表股。")
    st.caption(f"结构可读数值 {result['valid_numeric_rows']}｜无效/缺失 {result['invalid_numeric_rows']}；原值单位及事件时间未证实。")
    st.dataframe([{"代码原值": r.get("f12"), "市场原值": r.get("f13"), "名称原值": r.get("f14"),
                   "涨跌原值": r.get("f3"), "成交额原值": r.get("f6")}
                  for r in result["member_raw"]], hide_index=True, use_container_width=True)
    st.caption("仅比较本批原始字段；不能推断近期持续性、成交机会或风险审核结论。")
