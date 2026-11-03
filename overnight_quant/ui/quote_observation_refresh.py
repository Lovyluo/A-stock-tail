"""Manual refresh controls inside the existing observation workspace."""
import os

from overnight_quant.data.quote_observation_refresh import NETWORK_SWITCH, RefreshController


def render_refresh(st, store, state, prefix, *, controller=None):
    controller = controller or RefreshController(store, network_enabled=os.environ.get(NETWORK_SWITCH) == "1")
    if controller.store.root.resolve() != store.root.resolve():
        raise ValueError("refresh_controller_storage_scope_mismatch")
    st.caption("一次点击最多一条五股批量报价；无轮询。报价年龄60秒仅为观察规则，不是成交或来源时效保证。")
    st.caption("腾讯用途依据UNKNOWN；开发或执行批准不替代上游用途依据。")
    st.caption("执行预算10秒；确认持久化另计。结果已提交不等于此前调用方已收到返回。")
    if not controller.enabled:
        st.info("真实联网默认关闭；腾讯用途依据UNKNOWN。本轮开发不产生联网许可。")
    if store.scope == "synthetic_test_only":
        st.info("test-only注入响应，不代表真实更新或真实双快照验收。")
    if st.button("刷新一次报价", key=prefix + "_refresh", disabled=not controller.enabled):
        with st.spinner("正在取得一次报价并保存观察快照"):
            st.session_state[prefix + "_attempt"] = controller.refresh(state["pool"])
        attempt = st.session_state[prefix + "_attempt"]
        if attempt["status"] == "SUCCEEDED":
            st.session_state[prefix + "_snapshot"] = attempt["batch_id"]
    snapshots = store.refresh_snapshots()
    attempt = st.session_state.get(prefix + "_attempt")
    if attempt and attempt["status"] == "SUCCEEDED" and attempt["batch_id"] not in {s["batch_id"] for s in snapshots}:
        attempt = {**attempt, "status": "UNCONFIRMED", "result_committed": False, "error": "completion_unconfirmed"}
        st.session_state[prefix + "_attempt"] = attempt
    if attempt:
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
