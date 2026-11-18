"""Offline evidence-first homepage. No discovery, refresh, ranking or store writes."""
from overnight_quant.data.concept_batch_explanation import field_evidence
from overnight_quant.data.concept_batch_observation import load_batch
from overnight_quant.ui.concept_batch_observation import _catalog, require_catalog_selection


def homepage_state():
    # No verified event/attribution contract is connected. History cannot fill this gap.
    return {
        "event_date": "UNKNOWN", "hotspot_coverage": "UNKNOWN", "hotspot_events": [],
        "core_stock_observation_candidates": [], "persistence": "INSUFFICIENT_EVIDENCE",
        "qualification": "0/3", "data_ready": False, "hard_gate_authorized": False,
        "records": [], "candidates": [], "tickets": [], "orders": [],
    }


def historical_structure(batch_id, inputs):
    """Revalidate an explicitly selected immutable catalog anchor on every interaction."""
    require_catalog_selection(inputs, batch_id)
    batch = load_batch(*inputs)
    require_catalog_selection(inputs, batch_id)
    if batch["batch_id"] != batch_id or batch["synthetic_test_only"] is not False:
        raise ValueError("real_historical_batch_required")
    if not field_evidence(batch).membership:
        raise ValueError("historical_membership_unproven")
    # Only structural fields are projected; f3/f6 and alleged current candidates are not consumed.
    return {
        "batch_id": batch_id, "captured_at": batch["capture_completed_at"],
        "concept_code": batch["selected_bk"], "concept_name": batch["selected_name"],
        "list_coverage": dict(batch["list_coverage"]),
        "member_coverage": dict(batch["member_coverage"]),
        "members": [{"代码原值": row["f12"], "市场原值": row["f13"], "名称原值": row.get("f14")}
                    for row in batch["member_raw"]],
    }


def render(st):
    state = homepage_state()
    st.markdown("""<style>
    .stMainBlockContainer {max-width: 1200px; padding-top: 3rem;}
    h1 {font-size: 2rem; letter-spacing: 0;}
    h2 {font-size: 1.25rem; letter-spacing: 0;}
    [data-testid="stMarkdownContainer"], [data-testid="stCaptionContainer"] {
        overflow-wrap: anywhere;
    }
    </style>""", unsafe_allow_html=True)
    st.title("热点与核心股")
    st.caption("离线研究 · 尚无已核验热点或核心股候选")

    st.subheader("数据日期与覆盖")
    st.write("热点事件日期：未核验（UNKNOWN）｜热点覆盖：UNKNOWN｜已核验事件：0")
    st.caption("没有当前行情；本地采集时间不能替代来源事件时间。")

    st.subheader("题材与证据")
    st.info("暂无已核验热点。尚缺可追溯的主题事件、直接归因及对应日期/覆盖证据。")
    st.write("同花顺强势股归因仅有接口线索，尚无本项目核验通过的真实样本。")

    st.subheader("核心股研究候选与理由")
    st.info("暂无已核验核心股研究候选。历史成分归属不能证明当前强势、核心地位或龙头。")
    st.caption("候选理由尚缺：直接主题归因、规范证券身份、同日价格口径和完整观察范围。")

    st.subheader("持续性与不确定性")
    st.write("单日表现：资料不足。多日持续性：资料不足，未接入可比的连续日期证据。")
    st.warning("风险及可交易性未评估；不提供买入/卖出条件或交易建议。上游数据用途依据 UNKNOWN。")

    with st.expander("历史结构归属 · 非热点"):
        catalog, invalid = _catalog()
        selected = st.selectbox("历史结构批次", list(catalog), index=None,
                                placeholder="未选择", key="hotspot_history_batch")
        if not catalog:
            st.info("没有配置可信历史索引；不自动扫描或选择最新缓存。")
        if invalid:
            st.warning("存在无效或冲突的历史索引，已排除。")
        if selected is not None:
            try:
                history = historical_structure(selected, catalog[selected])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                st.error(f"历史材料拒绝：{type(exc).__name__}: {exc}")
            else:
                st.write(f"{history['concept_code']} {history['concept_name']}｜历史结构归属样本，不是热点")
                st.write(f"本地采集完成：{history['captured_at']}｜来源事件日期：UNKNOWN")
                listing, members = history["list_coverage"], history["member_coverage"]
                st.write(f"历史概念列表 {listing['received']}/{listing['total']}（非全市场热点覆盖）；"
                         f"本批成分 {members['received']}/{members['total']}（按来源声明总数）")
                st.caption("仅为来源历史归类；规范身份、价格口径及时点仍未确认。"
                           "不计算涨跌分布、成交额排名、活跃代表股或当前候选；用途 UNKNOWN，不公开传播。")
                st.dataframe(history["members"], hide_index=True, use_container_width=True)

    with st.expander("证据与维护详情"):
        st.write("文档声明不等于真实样本；结构核验不等于来源资格；开源代码许可不等于数据使用权。")
        st.write("研究候选独立保存于 core_stock_observation_candidates；本页尚未接入候选生成或持久化。")
        st.json(state)
