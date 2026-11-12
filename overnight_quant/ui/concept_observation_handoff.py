"""Explicit, offline navigation; no concept values become quote records."""
import re

from overnight_quant.data.concept_batch_explanation import field_evidence
from overnight_quant.data.concept_batch_observation import load_batch
from overnight_quant.data.offline_price_observation import normalize_pool
from overnight_quant.data.quote_observation_refresh import IDENTITIES
from overnight_quant.ui.concept_batch_observation import require_catalog_selection


HANDOFF = "concept_observation_handoff"


def member_choices(batch):
    coverage = batch["member_coverage"]
    rows = batch["member_raw"]
    if (not field_evidence(batch).membership or not coverage["complete"]
            or len(rows) != coverage["received"] or len(rows) != coverage["total"]):
        raise ValueError("concept_membership_or_coverage_unavailable")
    result = {}
    for row in rows:
        market, code = row.get("f13"), row.get("f12")
        if type(market) is not int or market not in (0, 1):
            raise ValueError("member_market_unsupported")
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
            raise ValueError("member_code_invalid")
        identity = normalize_pool([("SH" if market == 1 else "SZ") + code])[0]
        if identity in result:
            raise ValueError("member_identity_duplicate")
        result[identity] = str(row.get("f14") or identity)
    return result


def verified_selection(inputs, identity, *, expected_id, synthetic, catalog_id=None):
    require_catalog_selection(inputs, catalog_id)
    batch = load_batch(*inputs)
    if batch["batch_id"] != expected_id or batch["synthetic_test_only"] is not synthetic:
        raise ValueError("concept_handoff_identity_or_scope_mismatch")
    choices = member_choices(batch)
    if identity not in choices:
        raise ValueError("selected_security_not_in_batch")
    return batch, choices[identity]


def _navigate(st, selected, identity):
    st.session_state.pop(HANDOFF, None)
    st.session_state.pop("concept_handoff_error", None)
    try:
        result = selected["result"]
        verified_selection(selected["inputs"], identity, expected_id=result["batch_id"],
                           synthetic=result["synthetic_test_only"], catalog_id=selected["catalog_id"])
        st.session_state[HANDOFF] = {
            "inputs": selected["inputs"], "identity": identity,
            "batch_id": result["batch_id"], "synthetic": result["synthetic_test_only"],
            "catalog_id": selected["catalog_id"],
        }
        st.session_state["workspace_mode"] = "历史/离线观察"
        st.session_state["observation_mode"] = "历史快照"
        st.session_state["observation_scope"] = "测试材料 · test-only" if result["synthetic_test_only"] else "真实历史材料"
    except (ValueError, KeyError, TypeError, OSError, AttributeError) as exc:
        st.session_state["concept_handoff_error"] = str(exc)


def render_selection(st, selected):
    st.subheader("选择关注股票")
    st.caption("仅按证券身份导航，不按涨跌或成交额推荐；加入自选不触发行情请求。")
    try:
        choices = member_choices(selected["result"])
    except (ValueError, KeyError, TypeError) as exc:
        st.info(f"当前批次不能衔接自选：{exc}")
        return
    identity = st.selectbox("关注的成分股", list(choices), index=None,
                            format_func=lambda value: f"{choices[value]} · {value}",
                            key="concept_member_" + selected["result"]["batch_id"])
    st.button("前往观察工作台", disabled=identity is None,
              on_click=_navigate, args=(st, selected, identity))


def render_pending(st, store, state, revisions):
    pending = st.session_state.get(HANDOFF)
    if not pending:
        return None
    expected_scope = "synthetic_test_only" if pending["synthetic"] else "historical"
    if store.scope != expected_scope:
        st.session_state.pop(HANDOFF)
        st.info("概念选择属于另一材料空间，未写入当前自选池。")
        return None
    try:
        batch, name = verified_selection(pending["inputs"], pending["identity"],
                                        expected_id=pending["batch_id"], synthetic=pending["synthetic"],
                                        catalog_id=pending.get("catalog_id"))
    except (ValueError, KeyError, TypeError, OSError, AttributeError) as exc:
        st.session_state.pop(HANDOFF)
        st.error(f"来源批次不可用，不能加入自选：{exc}")
        return None
    identity = pending["identity"]
    if pending.get("saved_revision") and identity not in state["pool"]:
        st.session_state.pop(HANDOFF)
        st.info(f"当前自选池未关注 {identity}。")
        return None
    st.subheader(f"从概念查看进入 · {identity}")
    st.text(f"{name} · {batch['selected_name']}")
    st.caption(f"东方财富来源归类：{batch['selected_bk']}；批次 {batch['batch_id']}；"
               f"本地采集完成 {batch['capture_completed_at']}，源事件时点未确认。不是当前归属或推荐。")
    st.caption("报价另选独立快照，以其来源和时间为准；不把概念原值作为价格，不合并成同时点快照。")
    if identity not in IDENTITIES:
        st.info("尚未接入报价：超出现有手动报价固定范围。可保存关注和条件，不扩大请求范围。")
    if pending.get("saved_revision"):
        st.info(f"已关注 {identity}；在下方选择报价快照、设置条件并加入跟踪。")
        return None
    base = st.selectbox("加入前采用的设置", ["当前会话设置", *revisions], index=None,
                        format_func=lambda value: value if value == "当前会话设置" else "已保存版本 " + value[:16],
                        key="concept_base_" + batch["batch_id"] + identity)
    if st.button("取消本次加入"):
        st.session_state.pop(HANDOFF)
        st.rerun()
    if st.button("确认加入自选池", disabled=base is None):
        try:
            source = state if base == "当前会话设置" else revisions[base]
            updated = {"pool": normalize_pool([*source["pool"], identity]),
                       "rules": dict(source["rules"]), "tracking": list(source["tracking"])}
            revision = store.save_settings(**updated)
            st.session_state[HANDOFF] = {**pending, "saved_revision": revision}
            return updated
        except (ValueError, KeyError, TypeError, OSError) as exc:
            st.error(f"自选保存失败，未完成加入：{exc}")
    return None
