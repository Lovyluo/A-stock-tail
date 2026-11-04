"""Local change history and user notes within the existing workbench."""
from overnight_quant.data import observation_tracking as tracking


def render_tracking(st, store, state, prefix, comparison_args):
    st.subheader("条件变化记录与人工风险备注")
    st.caption("历史两点比较不是当前提醒，不证明连续路径、突破或成交机会。用户记录，系统未核实；风险/可交易性 NOT_ASSESSED。")
    if st.session_state.pop(prefix + "_note_saved", False):
        st.success("用户记录，系统未核实；已追加新版本，不改变系统风险状态。")
    try:
        identity, first_id, second_id, left_rule, right_rule = comparison_args
        available = {s["batch_id"]: s for s in [*store.snapshots(), *store.refresh_snapshots()]}
        preview = tracking.change(available.get(first_id), available.get(second_id), identity, left_rule, right_rule)
        st.write("所选历史比较：" + " / ".join(t["label"] for t in preview["transitions"].values()))
        if st.button("保存所选比较记录", key=prefix + "_save_change"):
            try:
                tracking.save_change(store, first_id, second_id, identity, left_rule, right_rule)
                st.success("比较记录已保存；重复操作不新增事件。")
            except (ValueError, OSError, KeyError, TypeError) as exc:
                st.error(f"比较记录拒绝：{exc}")
        saved = tracking.events(store)
        changed_only = st.checkbox("仅查看变化项", key=prefix + "_changes_only")
        rows = []
        for security in state["tracking"]:
            history = [e for e in saved if e["security_identity"] == security]
            latest = history[-1] if history else None
            memo = tracking.notes(store, security)
            c = latest["comparison"] if latest else None
            labels = "/".join(t["label"] for t in c["transitions"].values()) if c else "尚无已保存比较"
            if changed_only and (not c or all(t["state"] == "UNCHANGED" for t in c["transitions"].values())):
                continue
            rule = state["rules"].get(security, {})
            rows.append({"证券": security, "所设条件": f"下沿 {rule.get('lower') or '未设置'} / 上沿 {rule.get('upper') or '未设置'}",
                         "最近已确认观察": c["after"]["batch_id"] if c else "未记录",
                         "变化说明": labels, "数据时间": c["after"]["quote"]["event_time"] if c else "缺失",
                         "人工备注状态": tracking.note_summary(memo)})
        st.dataframe(rows, use_container_width=True, hide_index=True)
        for entry in reversed(saved):
            if entry["security_identity"] != identity:
                continue
            c = entry["comparison"]
            with st.expander(f"历史变化 {entry['recorded_at']} · {entry['revision'][:12]}"):
                st.write({side: t["label"] for side, t in c["transitions"].items()})
                for side in ("before", "after"):
                    result = c[side]
                    st.write({"位置": side, "价格": result["quote"]["price"], "事件时间": result["quote"]["event_time"],
                              "快照": result["batch_id"], "规则版本": result["rule_version"],
                              "条件状态": {k: v["label"] for k, v in result["conditions"].items()},
                              "证据SHA": result["evidence_sha256"]})
        st.caption("备注依据仅保存文本，不访问链接；复核到期时间由用户填写，不自动赋予有效期。")
        revisions = tracking.notes(store, identity)
        by_id = {n["revision"]: n for n in revisions}
        previous = st.selectbox("备注版本（不覆盖原件）", [None, *reversed(by_id)],
                                format_func=lambda k: "新增检查事项" if k is None else by_id[k]["item"] + " · " + k[:12],
                                key=prefix + "_note_version")
        old = by_id.get(previous, {})
        with st.form(prefix + "_note_" + str(previous)):
            item = st.text_input("检查事项", old.get("item", ""))
            conclusion = st.text_area("结论说明", old.get("conclusion", ""))
            evidence = st.text_area("依据文本或链接（不自动访问）", old.get("evidence", ""))
            due = st.text_input("复核到期时间（可选，含时区ISO格式）", old.get("due_at") or "")
            review = st.checkbox("标记待复核", old.get("review_requested", False))
            save = st.form_submit_button("保存人工备注新版本")
        if save:
            tracking.save_note(store, identity, item, conclusion, evidence, due_at=due or None,
                               review=review, previous=previous)
            st.session_state[prefix + "_note_saved"] = True
            st.rerun()
        for note in reversed(revisions):
            with st.expander(f"{tracking.note_status(note)} · {note['item']} · {note['revision'][:12]}"):
                st.text(tracking.DISCLAIMER)
                st.text(note["conclusion"])
                st.text(note["evidence"])
                st.text(f"实际记录时间 {note['recorded_at']}｜复核到期 {note['due_at'] or '未设置'}")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        st.error(f"本地变化/备注拒绝：{exc}")
