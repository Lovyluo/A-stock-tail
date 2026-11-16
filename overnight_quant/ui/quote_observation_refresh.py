"""Manual refresh controls inside the existing observation workspace."""
from overnight_quant.data.quote_observation_refresh import (
    LIMITED_VERSION, SELECTED_CONTRACTS, SELECTION_VERSION, VERSION, RefreshController, selection_supported,
)


def render_refresh(st, store, state, prefix, *, controller=None, contract=SELECTION_VERSION):
    controller = controller or RefreshController(store, contract=contract)
    if controller.store.root.resolve() != store.root.resolve():
        raise ValueError("refresh_controller_storage_scope_mismatch")
    live = controller.contract == LIMITED_VERSION
    selectable = controller.contract in SELECTED_CONTRACTS
    selection = []
    if selectable:
        selection = st.multiselect("本次报价证券（1–5只）", [i for i in state["pool"] if selection_supported(i)],
                                   max_selections=5, key=prefix + "_quote_selection")
        st.caption("待确认集合：" + ("、".join(sorted(selection)) if selection else "未选择"))
    st.caption("一次点击最多一条批量报价；无轮询。报价年龄60秒仅为观察规则，不是成交或来源时效保证。")
    st.caption("腾讯用途依据UNKNOWN；开发或执行批准不替代上游用途依据。")
    st.caption("执行预算10秒；确认持久化另计。结果已提交不等于此前调用方已收到返回。")
    enabled = controller.enabled if not live else False
    if live:
        permit, remaining, reason = controller.limited_status()
        enabled = reason is None
        if permit:
            st.caption("许可范围：" + "、".join(permit["selection"]) +
                       f"｜北京时间 {permit['window_start']} 至 {permit['window_end']}")
        st.caption(f"联网状态：{'测试注入' if enabled and controller.test_only else '已启用' if enabled else '关闭/不可用'}"
                   f"｜剩余额度：{remaining if remaining is not None else '未知'}/2")
        if reason:
            st.info("不可发起请求：" + reason)
        enabled = enabled and permit["selection"] == sorted(selection)
    elif not enabled:
        st.info("新版自选报价只接受测试注入，真实联网未启用。" if selectable else
                "真实联网默认关闭；腾讯用途依据UNKNOWN。本轮开发不产生联网许可。")
    if store.scope == "synthetic_test_only":
        st.info("test-only注入响应，不代表真实更新或真实双快照验收。")
    if st.button("刷新一次报价", key=prefix + "_refresh",
                 disabled=not enabled or (selectable and not selection)):
        with st.spinner("正在取得一次报价并保存观察快照"):
            st.session_state[prefix + "_attempt"] = controller.refresh(
                state["pool"], **({"selection": selection} if selectable else {}))
        attempt = st.session_state[prefix + "_attempt"]
        if attempt["status"] == "SUCCEEDED":
            st.session_state[prefix + "_snapshot"] = attempt["batch_id"]
        if live:
            st.rerun()
    contracts = (LIMITED_VERSION,) if live else (VERSION, SELECTION_VERSION) if controller.test_only else (VERSION,)
    snapshots = store.refresh_snapshots(contracts=contracts)
    attempt = st.session_state.get(prefix + "_attempt")
    if attempt and attempt["status"] == "SUCCEEDED" and attempt["batch_id"] not in {s["batch_id"] for s in snapshots}:
        attempt = {**attempt, "status": "UNCONFIRMED", "result_committed": False, "error": "completion_unconfirmed"}
        st.session_state[prefix + "_attempt"] = attempt
    if attempt:
        if attempt.get("selection"):
            st.caption("最近一次尝试的集合：" + "、".join(attempt["selection"]))
        if attempt["status"] == "SUCCEEDED":
            st.success("本次报价已保存；每次交互重新计算年龄，不持续监控。")
        elif attempt["status"] == "UNCONFIRMED":
            st.warning("完成状态未确认：" + attempt["error"] + "；不接纳成功快照，不补齐本次覆盖。")
        else:
            st.error("本次刷新失败/拒绝：" + attempt["error"] + "；旧快照仅供历史查看，不补齐本次覆盖。")
        st.caption("耗时（ms）：执行 {0}｜确认 {1}｜总计 {2}".format(
            *["UNKNOWN" if attempt.get(key) is None else f"{attempt[key]:.2f}"
              for key in ("execution_elapsed_ms", "confirmation_elapsed_ms", "total_elapsed_ms")]))
    return snapshots, attempt
