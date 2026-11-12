"""Explicitly selected offline batches, separate from user settings and demo state."""
import json
import os
from pathlib import Path

from overnight_quant.data.concept_batch_explanation import explain_members, field_evidence
from overnight_quant.data.concept_batch_observation import VERSION, load_batch


CATALOG_ENV = "CONCEPT_BATCH_VERIFIED_INDEXES"


def _catalog():
    entries = {}
    ambiguous = set()
    invalid = 0
    keys = ("output_directory", "returned_report", "report_sha256", "confirmation_sha256")
    for filename in filter(None, os.environ.get(CATALOG_ENV, "").split(os.pathsep)):
        try:
            index = json.loads(Path(filename).read_text(encoding="utf-8-sig"))
            batch_id = index["batch_id"]
            entry = tuple(index[key] for key in keys)
            if (index["independent_verification"] != "VERIFIED_OFFLINE" or index["capture_status"] != "PASSED"
                    or not all(isinstance(value, str) and value.strip() for value in (batch_id, *entry))):
                raise ValueError("invalid_catalog_entry")
            if batch_id in entries and entries[batch_id] != entry:
                ambiguous.add(batch_id)
            entries[batch_id] = entry
        except (OSError, UnicodeError, ValueError, KeyError, TypeError):
            invalid += 1
    return {key: entries[key] for key in sorted(entries) if key not in ambiguous}, invalid + len(ambiguous)


def require_catalog_selection(inputs, catalog_id):
    if catalog_id is not None and _catalog()[0].get(catalog_id) != inputs:
        raise ValueError("catalog_batch_anchor_changed")


def _open(st, inputs, *, expected_id=None):
    st.session_state["concept_batch_selected"] = {"inputs": inputs, "catalog_id": expected_id}


def render(st):
    st.title("概念与成分 · 捕获批次观察")
    if error := st.session_state.pop("concept_handoff_error", None):
        st.error("批次重新核验失败：" + error)
    st.warning("离线查看指定批次；源数据时点未确认，不代表实时行情、当前强势、最终收盘或买卖建议。")
    st.caption("风险和可交易性未评估。")
    catalog, invalid = _catalog()
    if catalog:
        chosen = st.selectbox("已核验批次", list(catalog), index=None, placeholder="选择一个历史批次",
                              on_change=lambda: st.session_state.pop("concept_batch_selected", None))
        if st.button("打开所选批次", disabled=chosen is None):
            _open(st, catalog[chosen], expected_id=chosen)
    else:
        st.info("没有配置已核验批次。")
    with st.expander("维护详情"):
        if invalid:
            st.warning(f"{invalid} 个批次索引无效或冲突，未列入选择。")
        directory = st.text_input("批次目录", key="concept_batch_path")
        report = st.text_input("命令返回报告文件", key="concept_batch_report")
        anchor = st.text_input("返回报告外部 SHA-256", key="concept_batch_anchor")
        confirmation = st.text_input("最终确认外部 SHA-256", key="concept_batch_confirmation")
        manual_inputs = (directory, report, anchor, confirmation)
        if st.button("打开指定批次"):
            _open(st, manual_inputs)
        selected = st.session_state.get("concept_batch_selected")
        if selected and ("catalog_id" not in selected or
                         (selected["catalog_id"] is None and selected["inputs"] != manual_inputs)):
            st.session_state.pop("concept_batch_selected")
            selected = None
        if selected:
            st.caption(f"所选材料：{selected['inputs'][0]}；返回报告：{selected['inputs'][1]}")
    if not selected:
        st.info("尚未选择有效批次。")
        return
    try:
        require_catalog_selection(selected["inputs"], selected["catalog_id"])
        result = load_batch(*selected["inputs"])
        if selected["catalog_id"] is not None and (result["batch_id"] != selected["catalog_id"]
                                                   or result["synthetic_test_only"]):
            raise ValueError("catalog_batch_identity_mismatch")
    except Exception as exc:
        st.session_state.pop("concept_batch_selected", None)
        st.error(f"批次不可用：{type(exc).__name__}: {exc}")
        st.info("尚未选择有效批次。")
        return
    synthetic = result["synthetic_test_only"]
    if synthetic:
        st.warning("测试数据 · synthetic_test_only；不是实际行情。")
    if result.get("experiment_permission") is not None:
        st.warning("上游用途依据 UNKNOWN；用户仅批准本次个人内部有限结构验证，并已知悉该未知状态。"
                   "不是上游授权或法律合规保证，不得公开传播。")
    listing, members = result["list_coverage"], result["member_coverage"]
    evidence = field_evidence(result)
    summary = explain_members(result, evidence)

    st.subheader("覆盖与时点")
    st.write(f"批次 {result['batch_id']}｜采集完成 {result['capture_completed_at']}｜原生事件时点未确认")
    scope = "列表完整性按返回总数核对，不代表全市场" if listing["complete"] else "有限覆盖范围，不代表全市场排名"
    st.write(f"概念列表 {listing['received']}/{listing['total']}｜{scope}")
    st.caption(f"列表涨跌原值可读 {result['list_valid_numeric_rows']}｜无效/缺失 {result['list_invalid_numeric_rows']}")
    st.write(f"成分 {members['received']}/{members['total']}｜按本批返回总数核对")
    st.caption("采集完成是本地收到材料后的时间；原始时点字段未证明是这组数值的共同事件时间。")

    st.subheader("固定结构验证样本与成分")
    selection_scope = "降序分页所得有限集合" if result["schema_version"] == VERSION else "v2 原批次覆盖集合"
    st.text(result["selected_name"] or "名称未确认")
    st.write(f"{result['selected_bk']}｜固定结构验证样本："
             f"{selection_scope}内代码字典序最小，不代表全市场代码最小，非强势候选")
    if synthetic:
        st.caption("概念和成分归属仅为测试设定。")
    elif evidence.membership:
        st.write("来源返回的概念及成分")
        st.caption("东方财富页面确认概念分类；本批成分请求绑定所选 BK。仅反映来源归类，不代表客观或唯一题材归属。")
    else:
        st.info("归属依据不足：只展示原值，不确认概念归属。")

    from overnight_quant.ui.concept_observation_handoff import render_selection
    render_selection(st, {**selected, "result": result})

    st.markdown("**批次成分分布**")
    counts = summary["counts"]
    if counts is not None:
        st.write(f"上涨 {counts['up']}｜下跌 {counts['down']}｜平盘 {counts['flat']}｜上涨占比 {counts['up_share']:.1%}")
        st.caption(f"按涨跌字段正、负、零计数，以上涨数除以有效数；基准：{evidence.change_reference}，"
                   f"单位：{evidence.change_unit}。不代表盘中走势。")
    elif not summary["complete"] or not evidence.membership:
        st.info("成分覆盖或归属不足，未计算分布。")
    elif not evidence.change_reference or not evidence.change_unit:
        st.info("来源页面将 f3 标为涨跌幅；本批比较基准或单位证据不足，未计算上涨、下跌、平盘或上涨占比。")
    else:
        st.info("无有效涨跌原值，未计算分布。")
    st.caption(f"成分涨跌原值：有效 {summary['valid']}｜无效/缺失 {summary['invalid']}｜总记录 {summary['total']}。"
               "有效仅表示数值可读，无效不计为平盘。")

    st.markdown("**批次成交活跃代表股**")
    if summary["leaders"] is not None:
        st.caption(f"在完整成分中按成交额降序取前 3 名，同值按证券身份排序。"
                   f"单位：{evidence.amount_unit}；统计期间：{evidence.amount_period}。不是龙头。")
        st.dataframe([{"证券": row["code"], "名称": row["name"], "成交额": row["amount"]}
                      for row in summary["leaders"]], hide_index=True, use_container_width=True)
    elif not summary["complete"] or not evidence.membership:
        st.info("成分覆盖或归属不足，未计算代表股。")
    elif not evidence.amount_unit or not evidence.amount_period:
        st.info("来源页面将 f6 标为成交额；本批原值单位或统计期间未证实，未计算代表股。")
    else:
        st.info("有成分成交额无效或缺失，不能确定完整集合内的前 3 名。")

    with st.expander("来源原值"):
        st.caption("原值不构成强弱排序；源数据时点未确认。")
        st.dataframe([{"代码原值": row.get("f12"), "名称原值": row.get("f14"), "涨跌原值": row.get("f3")}
                      for row in result["list_raw"]], hide_index=True, use_container_width=True)
        st.dataframe([{"代码原值": row.get("f12"), "市场原值": row.get("f13"), "名称原值": row.get("f14"),
                       "涨跌原值": row.get("f3"), "成交额原值": row.get("f6"), "时点字段原值": row.get("f124")}
                      for row in result["member_raw"]], hide_index=True, use_container_width=True)
