"""Historical source content, with no security action or passive network path."""
import os

from overnight_quant.data.ths_history_clues import load_history


def render(st):
    with st.expander("线索材料与维护详情"):
        path = st.text_input("已封存线索索引", value=os.environ.get("THS_HISTORY_INDEX", ""), key="ths_history_index")
        anchor = st.text_input("索引外部SHA-256", value=os.environ.get("THS_HISTORY_INDEX_SHA256", ""), key="ths_history_sha")
        st.caption("只读本地已核验材料；索引来自收样时的外部锚点，不以当前自算哈希代替来源证明。")
    if not path and not anchor:
        st.info("尚未选择封存的历史归因线索；在维护详情配置已有索引。不会联网补齐。")
        return
    try:
        history = load_history(path, anchor)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        st.error(f"历史线索拒绝读取：{type(exc).__name__}: {exc}")
        return

    st.subheader("题材标签与来源记录")
    st.write(f"来源行内日期：{'、'.join(history['row_dates_raw'])}｜实际采集：{history['response_completed_at']}")
    st.write(f"{history['received_records']}条样本｜去重后{history['unique_records']}条｜总覆盖未知")
    st.caption("仅展示来源提到的题材和股票，不是当前热点、市场排名或推荐。身份未核对不提供证券操作。")
    st.caption("原文中的‘龙头’等措辞仅为来源引用，不是系统确认。")
    tags = history["tags"]
    st.dataframe([{"题材原文标签": item["label"], "本样本提及记录数": item["mention_records"]} for item in tags],
                 hide_index=True, use_container_width=True)
    st.caption("完整原始记录完全一致才去重；每条记录对同一标签只计一次。不同标签可能对应同一记录，"
               "计数不能相加当作股票总数；不合并近义词，不按涨幅或成交额排序。")
    labels = [item["label"] for item in tags]
    chosen = st.selectbox("查看题材关联记录", ["全部标签", *labels], key="ths_history_tag")
    search = st.text_input("搜索代码、名称或归因原文", key="ths_history_search").strip().casefold()
    records = [item for item in history["records"] if (chosen == "全部标签" or chosen in item["tags"])
               and (not search or search in " ".join(str(item.get(key) or "") for key in
                    ("code_raw", "name_raw", "reason_raw")).casefold())]
    if not records:
        st.info("本次筛选没有关联记录；不是市场没有相关题材。")
        return
    st.dataframe([{"来源代码": item["code_raw"], "来源名称": item["name_raw"], "行内日期原值": item["row_date_raw"],
                   "身份核对": "未核对（UNKNOWN）" if item["identity_check"]["status"] == "UNKNOWN" else item["identity_check"]["status"],
                   "完整归因原文": item["reason_raw"]} for item in records], hide_index=True, use_container_width=True)
    selected = st.selectbox("查看完整原文与日期依据", [item["record_id"] for item in records],
                            format_func=lambda value: next(f"{item['code_raw']} · {item['name_raw']}" for item in records
                                                           if item["record_id"] == value), key="ths_history_record")
    record = next(item for item in records if item["record_id"] == selected)
    st.text(record["reason_raw"] if isinstance(record["reason_raw"], str) else "归因原值不是文本")
    st.write(f"来源代码 {record['code_raw']}｜来源名称 {record['name_raw']}｜market原值 {record['market_raw']}")
    st.write(f"身份核对：{record['identity_check']['status']}；规范市场/代码："
             f"{record['identity_check']['canonical_identity'] or 'UNKNOWN'}")
    st.caption("来源market枚举含义未证明；本机目录缓存未有可信锚点，不作为身份确认依据。")
    st.write(f"请求目标日期：{history['request_target_date']}｜行内date：{record['row_date_raw']}")
    st.write(f"顶层日期原值：{history['source_top_level_time_raw']}｜采集时间：{history['response_completed_at']}")
    st.caption("顶层日期不作为事件发生日；本版本是之后采集的历史返回，不证明目标日当时已经可见。"
               "来源时间、覆盖、单位及归因真实性仍有未知；风险和可交易性未评估。")
    with st.expander("标签拆分与证据详情"):
        st.json({"batch_id": history["batch_id"], "record_id": record["record_id"], "source_row": record["source_row"],
                 "tag_trace": record["tag_trace"], "source_use_basis": "UNKNOWN",
                 "transport": "明文HTTP，非TLS认证原件", "index_sha256": history["index_sha256"]})
