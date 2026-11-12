"""The history workspace has no refresh, strategy, position or trading actions."""
from __future__ import annotations

from pathlib import Path

from overnight_quant.data.offline_price_observation import (
    INITIAL_POOL, SCOPES, STATES, ObservationStore,
    compare, evaluate, make_rule, normalize_pool,
)
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.quote_observation_refresh import IDENTITIES, evaluate_current, utc_now
from overnight_quant.ui.concept_observation_handoff import HANDOFF, render_pending
from overnight_quant.ui.quote_observation_refresh import render_refresh

DEFAULT_STORE = Path(__file__).resolve().parents[1] / "data" / "cache" / "offline_observation"


def render_offline_observation(st, *, storage_root=None, refresh_controller=None, clock=None):
    st.markdown("""<style>
    .stMainBlockContainer {max-width: 1400px; padding-top: 4rem;}
    h1 {font-size: 2rem; letter-spacing: 0;}
    h2 {font-size: 1.35rem; letter-spacing: 0;}
    [data-testid="stCaptionContainer"] {overflow-wrap: anywhere;}
    </style>""", unsafe_allow_html=True)
    st.title("自选观察工作台")
    st.warning("观察工作台：满足条件不等于买入、卖出或确认突破；风险及可交易性未评估。")
    active = st.radio("观察方式", ["历史快照", "主动刷新观察"], horizontal=True,
                      key="observation_mode") == "主动刷新观察"
    if not active:
        st.caption("历史/离线观察，实时更新尚未接通。历史结果不表示当前触发。")
    scope_label = st.radio("材料空间", ["真实历史材料", "测试材料 · test-only"], horizontal=True,
                           key="observation_scope")
    scope = SCOPES[scope_label == "测试材料 · test-only"]
    if scope == SCOPES[1]:
        st.info("synthetic_test_only：以下价格、条件及变化仅为测试，不是实际行情。")
    settings_prefix = "offline_" + scope
    prefix = settings_prefix + ("_manual_quote" if active else "")
    store = ObservationStore(storage_root or DEFAULT_STORE, scope)
    state_key = settings_prefix + "_state"
    if state_key not in st.session_state:
        st.session_state[state_key] = {"pool": normalize_pool(INITIAL_POOL), "rules": {}, "tracking": []}
    state = st.session_state[state_key]
    try:
        snapshots, revisions = store.snapshots(), store.settings()
    except (ValueError, KeyError, TypeError, OSError) as exc:
        st.error(f"本地材料拒绝：{exc}")
        return
    with st.expander("导入明确历史批次", expanded=not snapshots and not active):
        upload = st.file_uploader("历史批次文件（JSON）", type=["json"], key=prefix + "_upload")
        anchor = st.text_input("可信外部SHA-256", key=prefix + "_anchor")
        st.caption("仅接收显式test-only材料，不进入真实历史空间。" if scope == SCOPES[1] else
                   "真实空间仅接收既有腾讯session原件；不接收前复权日线。SHA来自原封存索引，不以当前文件自算值证明来源。")
        if st.button("导入历史批次", key=prefix + "_import"):
            try:
                if upload is None:
                    raise ValueError("historical_file_required")
                store.import_bytes(upload.getvalue(), anchor)
                st.success("批次已导入；重复导入不覆盖原件。")
                snapshots = store.snapshots()
            except (ValueError, TypeError, KeyError, OSError) as exc:
                st.error(f"导入拒绝：{exc}")

    with st.expander("自选池与本地设置", expanded=True):
        with st.form(prefix + "_pool_form"):
            codes = st.text_area("证券代码（每行一个）", "\n".join(state["pool"]),
                                 key=prefix + "_pool_text_" + stable_hash(state["pool"]))
            save_pool = st.form_submit_button("保存自选池")
        if save_pool:
            try:
                pool = normalize_pool([value.strip() for value in codes.splitlines() if value.strip()])
                updated = {"pool": pool, "rules": {key: value for key, value in state["rules"].items() if key in pool},
                           "tracking": [key for key in state["tracking"] if key in pool]}
                revision = store.save_settings(**updated)
                st.session_state[state_key] = state = updated
                revisions = store.settings()
                st.success(f"自选池已保存 · 设置版本 {revision[:12]}")
            except (ValueError, OSError) as exc:
                st.error(f"自选池拒绝：{exc}")
        chosen_revision = st.selectbox("已保存设置版本", [None, *revisions],
                                       format_func=lambda value: "不加载其他版本" if value is None else value[:16],
                                       key=prefix + "_revision")
        if st.button("加载所选设置", key=prefix + "_load", disabled=chosen_revision is None):
            st.session_state[state_key] = {key: revisions[chosen_revision][key] for key in ("pool", "rules", "tracking")}
            st.rerun()

    if active:
        st.session_state.pop(HANDOFF, None)
    else:
        handoff_state = render_pending(st, store, state, revisions)
        pending = st.session_state.get(HANDOFF)
        if handoff_state is not None:
            st.session_state[state_key] = handoff_state
            st.session_state[prefix + "_identity"] = pending["identity"]
            if pending["identity"] in handoff_state["tracking"]:
                st.session_state[prefix + "_tracked_identity"] = pending["identity"]
            st.rerun()
        if pending and not pending.get("saved_revision"):
            return

    attempt = None
    if active:
        try:
            snapshots, attempt = render_refresh(st, store, state, prefix, controller=refresh_controller)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            st.error(f"刷新存储拒绝：{exc}")
            return
    by_batch = {item["batch_id"]: item for item in snapshots}
    def batch_label(value):
        if value is None:
            return "未选择历史快照"
        item = by_batch[value]
        return f"{value if len(value) <= 24 else value[:12]} | {item['captured_at']}"
    selected = st.selectbox("观察快照", [None, *by_batch], format_func=batch_label, key=prefix + "_snapshot")
    snapshot = by_batch.get(selected)
    st.caption("风险/可交易性未评估 · NOT_ASSESSED｜不是买卖建议；页面无后台持续监控。" if active else
               "风险未评估 · NOT_ASSESSED｜仅针对所选历史快照，不表示当前触发。")
    if snapshot:
        st.caption(f"所选批次 {snapshot['batch_id']}")
        st.caption(f"来源 {snapshot['source_version']} · 文件SHA {snapshot['file_sha256']}")

    st.subheader("价格与条件状态")
    state_filter = st.selectbox("条件状态筛选", ["全部", *STATES.values()], key=prefix + "_filter")
    results, table = {}, []
    for identity in state["pool"]:
        rule = state["rules"].get(identity, make_rule(identity))
        result = (evaluate_current(snapshot, identity, rule, now=(clock or utc_now)(), attempt=attempt)
                  if active else evaluate(snapshot, identity, rule))
        results[identity] = result
        row = snapshot["rows"].get(identity, {}) if snapshot else {}
        if state_filter != "全部" and not any(STATES[value["state"]] == state_filter for value in result["conditions"].values()):
            continue
        table.append({"证券身份": identity, "比较价格": result["conditions"]["upper"]["comparison_price"] or "缺失/不可用",
                      "上沿状态": result["conditions"]["upper"]["label"],
                      "下沿状态": result["conditions"]["lower"]["label"],
                      "价格口径": row.get("price_basis", "缺失"), "来源": row.get("source_version", "缺失"),
                      "原生事件时间": row.get("event_time", "缺失"),
                      "实际获取时间": row.get("observed_at", "缺失"), "实际可用时间": row.get("available_at", "缺失"),
                      "历史批次": selected or "未选择", "风险状态": "NOT_ASSESSED",
                      "缺失/拒绝原因": ("尚未接入报价" if identity not in IDENTITIES and not row else
                                        result["reason"] or "无；仅历史价格比较")})
        if active:
            table[-1].update({"所选快照价格（失败时仅历史）": row.get("price", "缺失"),
                              "报价年龄（秒）": result["quote_age_seconds"],
                              "年龄状态": result["age_status"],
                              "请求状态": (attempt or {}).get("status", "尚未刷新")})
    st.dataframe(table, use_container_width=True, hide_index=True)

    st.subheader("绝对价格观察条件")
    identity = st.selectbox("设置条件的证券", state["pool"], key=prefix + "_identity")
    if identity not in IDENTITIES and not (snapshot and identity in snapshot["rows"]):
        st.info(f"{identity} 尚未接入报价。")
    current_rule = state["rules"].get(identity, make_rule(identity))
    rule_key = prefix + identity + current_rule["rule_version"]
    with st.form(rule_key):
        cols = st.columns(2)
        lower = cols[0].text_input("下沿：价格达到或低于", current_rule["lower"] or "", key=rule_key + "_lower")
        upper = cols[1].text_input("上沿：价格达到或高于", current_rule["upper"] or "", key=rule_key + "_upper")
        save_rule = st.form_submit_button("保存条件")
    if save_rule:
        try:
            rule = make_rule(identity, lower=lower, upper=upper)
            updated = {**state, "rules": {**state["rules"], identity: rule}}
            store.save_settings(**updated)
            st.session_state[state_key] = updated
            st.rerun()
        except (ValueError, OSError) as exc:
            st.error(f"条件拒绝：{exc}")
    st.caption(f"条件版本 {current_rule['rule_version']} · 空白为未设置，无默认观察价。")
    result = results[identity]
    for side, label in (("upper", "价格 ≥ 上沿"), ("lower", "价格 ≤ 下沿")):
        condition = result["conditions"][side]
        st.write(f"{label}：{condition['label']}｜比较价格 {condition['comparison_price'] or '不可用'}｜阈值 {condition['threshold'] or '未设置'}")
    if st.button("移出跟踪" if identity in state["tracking"] else "加入跟踪", key=prefix + "_track"):
        try:
            tracking = set(state["tracking"])
            tracking.symmetric_difference_update({identity})
            updated = {**state, "tracking": sorted(tracking)}
            store.save_settings(**updated)
            st.session_state[state_key] = updated
            if identity in tracking and (st.session_state.get(HANDOFF) or {}).get("identity") == identity:
                st.session_state[prefix + "_tracked_identity"] = identity
            st.rerun()
        except (ValueError, OSError) as exc:
            st.error(f"跟踪保存失败：{exc}")

    st.subheader("两次快照变化跟踪")
    if active:
        st.caption("以下明确比较已保存快照的历史变化，不把旧快照年龄重置，也不证明当前触发。")
        if len(snapshots) < 2:
            st.info("尚无可比较快照")
    if not state["tracking"]:
        st.info("跟踪清单为空。")
        return
    tracked = st.selectbox("跟踪证券", state["tracking"], key=prefix + "_tracked_identity")
    second_id = st.selectbox("对比快照", [None, *by_batch], format_func=batch_label, key=prefix + "_second_snapshot")
    rule_options = {}
    for revision in store.settings().values():
        saved = revision["rules"].get(tracked)
        if saved:
            rule_options[saved["rule_version"]] = saved
    active = state["rules"].get(tracked, make_rule(tracked))
    rule_options[active["rule_version"]] = active
    options = [active["rule_version"], *(key for key in rule_options if key != active["rule_version"])]
    def rule_label(key):
        rule = rule_options[key]
        return f"{key[:12]} | 下沿 {rule['lower'] or '未设置'} / 上沿 {rule['upper'] or '未设置'}"
    cols = st.columns(2)
    left_rule = cols[0].selectbox("观察快照条件版本", options, format_func=rule_label, key=prefix + "_rule_left")
    right_rule = cols[1].selectbox("对比快照条件版本", options, format_func=rule_label, key=prefix + "_rule_right")
    comparison = compare(snapshot, by_batch.get(second_id), tracked, rule_options[left_rule], rule_options[right_rule])
    st.info(comparison["message"])
    if comparison["comparable"]:
        st.metric("两次历史快照价格变化（元/股）", comparison["price_change"])
        changes = []
        for key, label in (("before", "观察快照"), ("after", "对比快照")):
            item = comparison[key]
            changes.append({"快照": label, "证券": tracked, "历史批次": item["batch_id"],
                            "价格": item["conditions"]["upper"]["comparison_price"],
                            "上沿": item["conditions"]["upper"]["label"], "下沿": item["conditions"]["lower"]["label"],
                            "条件版本": item["rule_version"], "来源": item["quote"]["source_version"],
                            "原生事件时间": item["quote"]["event_time"],
                            "实际获取时间": item["quote"]["observed_at"], "证据SHA": item["evidence_sha256"],
                            "原响应hash": item["quote"]["raw_hash"]})
        st.dataframe(changes, use_container_width=True, hide_index=True)
    from overnight_quant.ui.observation_tracking import render_tracking
    render_tracking(st, store, state, prefix,
                    (tracked, selected, second_id, rule_options[left_rule], rule_options[right_rule]))
