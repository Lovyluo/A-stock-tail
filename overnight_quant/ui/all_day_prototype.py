"""Offline, synthetic-only all-day observation prototype; no source or order access."""
import math

VERSION = "all_day_observation_demo_v1"
DEMO_TIME = "2026-01-09 14:20（虚构演示）"
THEMES = [
    {"name": "示例方向 A", "relative": 1.8, "breadth": 0.68, "today": 0.9,
     "coverage": "17/25", "leader": "示例甲 · 000001", "role": "趋势核心（演示假设）",
     "reason": "五日相对基准为正，样本中上涨成员较多", "price": 10.2,
     "highs": [10.1, 10.3, 10.4, 10.2, 10.5], "lows": [9.4, 9.5, 9.7, 9.6, 9.6]},
    {"name": "示例方向 B", "relative": 0.2, "breadth": 0.52, "today": -0.3,
     "coverage": "13/25", "leader": "示例乙 · 600000", "role": "活跃核心（演示假设）",
     "reason": "成员涨跌分布接近，方向尚不一致", "price": 8.6,
     "highs": [8.5, 8.6, 8.8, 8.7, 8.9], "lows": [7.9, 8.0, 8.1, 8.0, 8.0]},
    {"name": "示例方向 C", "relative": -1.5, "breadth": 0.32, "today": -1.1,
     "coverage": "8/25", "leader": "示例丙 · 000333", "role": "连板核心（演示假设，未验证）",
     "reason": "相对基准偏弱，样本上涨成员较少", "price": 12.0,
     "highs": [12.5, 12.8, 12.7, 12.6, 12.6], "lows": [11.4, 11.5, 11.3, 11.1, 11.2]},
]


def intraday_status(today, breadth):
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (today, breadth)):
        return "资料不足"
    if not 0 <= breadth <= 1:
        return "资料不足"
    if today >= 0 and breadth >= .6:
        return "走强"
    if today < 0 and breadth < .4:
        return "走弱"
    return "分化"


def system_reference(highs, lows, history_basis, quote_basis):
    """Derive illustrative levels only from five complete same-basis days."""
    if history_basis != "unadjusted_cny_per_share" or quote_basis != history_basis:
        return None
    if not isinstance(highs, (tuple, list)) or not isinstance(lows, (tuple, list)):
        return None
    if len(highs) != 5 or len(lows) != 5:
        return None
    values = [*highs, *lows]
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in values):
        return None
    if any(low > high for high, low in zip(highs, lows)):
        return None
    return {"prior_high": highs[-1], "prior_low": lows[-1],
            "five_high": max(highs), "five_low": min(lows)}


def theme_status(relative, breadth, today):
    values = (relative, breadth, today)
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        return "资料不足"
    if not 0 <= breadth <= 1:
        return "资料不足"
    if relative <= -1 or breadth < .4:
        return "走弱"
    if relative >= 1 and breadth >= .6 and today >= 0:
        return "走强"
    return "分化"


def observation_state(price, upper, invalid):
    values = (price, upper, invalid)
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in values):
        return "资料不足"
    if invalid >= upper:
        return "资料不足"
    if price <= invalid:
        return "条件失效"
    return "满足观察条件" if price >= upper else "等待确认"


def render(st):
    st.markdown("""<style>
    .stMainBlockContainer {max-width: 1200px; padding-top: 3rem;}
    h1 {font-size: 2rem !important; letter-spacing: 0;}
    h3 {font-size: 1.25rem !important; letter-spacing: 0;}
    </style>""", unsafe_allow_html=True)
    st.title("全天短线分析观察台")
    st.warning("演示数据 · 非当前行情。方向、角色、价格和条件均为虚构样例，不是买卖建议。")
    st.caption(f"演示时点：{DEMO_TIME} ｜ 风险与可交易性未核实 ｜ 不自动刷新、不连接交易")
    st.session_state.setdefault("all_day_demo_tracking", [])
    tabs = st.tabs(["今日机会", "竞价观察", "热门题材", "核心个股", "我的跟踪"])
    with tabs[0]:
        st.subheader("市场环境")
        st.info("资料不足：示例未提供全市场宽度和基准同步数据，不能判断真实市场强弱。")
        st.subheader("方向与观察对象")
        st.caption("以下仅用于体验分析流程，真实方向与个股均尚未验证。")
        for t in THEMES:
            ref = system_reference(t["highs"], t["lows"], "unadjusted_cny_per_share", "unadjusted_cny_per_share")
            with st.container(border=True):
                cols = st.columns([3, 1])
                cols[0].markdown(f"**{t['name']}** · 当日 {intraday_status(t['today'], t['breadth'])}")
                cols[1].caption("演示")
                st.write(f"{t['leader']}｜{t['role']}")
                st.write(f"当日表现 {t['today']:+.1f}%｜上涨成员 {t['coverage']}。近期持续性：{theme_status(t['relative'], t['breadth'], t['today'])}，五日相对基准 {t['relative']:+.1f}个百分点。")
                st.write(f"关注理由：{t['reason']}。")
                if ref:
                    st.write(f"系统参考条件（合成日线）：前日高 {ref['prior_high']:.2f}，前日低 {ref['prior_low']:.2f}；五日区间 {ref['five_low']:.2f}–{ref['five_high']:.2f}。")
                    st.caption(f"演示报价 {t['price']:.2f}：{observation_state(t['price'], ref['prior_high'], ref['prior_low'])}；单个价位不证明突破。来源：合成演示｜{DEMO_TIME}")
                else:
                    st.caption("系统参考条件：资料不足，不生成参考价。")
                st.caption("用户自定义条件与此独立，请在原历史/离线观察工作区设置。")
                st.caption("风险缺口：公告、停牌、涨跌停与可交易性均未审核。")
                if st.button("加入演示跟踪", key="watch_" + t["name"]):
                    if t["name"] not in st.session_state["all_day_demo_tracking"]:
                        st.session_state["all_day_demo_tracking"].append(t["name"])
                    st.success("已加入本次演示跟踪，不写入真实设置。")
    with tabs[1]:
        st.subheader("竞价观察")
        st.info("资料不足：竞价价格、数量、匹配时点和历史口径尚未验证。此模块单独暂停，不影响历史观察。")
    with tabs[2]:
        st.subheader("热门题材 · 演示")
        st.caption("行业不等于题材；当前成分不能代替历史成分。")
        st.dataframe([{"方向": t["name"], "当日状态": intraday_status(t["today"], t["breadth"]),
                       "近期持续性": theme_status(t["relative"], t["breadth"], t["today"]),
                       "五日相对基准（百分点）": t["relative"], "上涨成员": t["coverage"],
                       "风险缺口": "公告、成分变动及可交易性未知"} for t in THEMES], hide_index=True, use_container_width=True)
    with tabs[3]:
        st.subheader("核心个股 · 演示")
        selected = st.selectbox("选择演示对象", [t["leader"] for t in THEMES])
        t = next(item for item in THEMES if item["leader"] == selected)
        ref = system_reference(t["highs"], t["lows"], "unadjusted_cny_per_share", "unadjusted_cny_per_share")
        st.write(f"**关注理由**：{t['reason']}")
        st.write(f"**题材角色**：{t['role']}；不是自动认定的龙头。")
        if ref:
            st.write(f"**系统参考条件（合成日线）**：前日高 {ref['prior_high']:.2f}；达到后仍需连续轨迹确认。")
            st.write(f"**失效观察**：不高于前日低 {ref['prior_low']:.2f} 时取消该演示计划。")
        else:
            st.write("**系统参考条件**：资料不足，不生成参考价。")
        st.caption("用户自定义条件独立保存在原历史/离线观察工作区。")
        st.write("**回踩确认**：资料不足，缺少连续路径，无法确认回踩或重新站稳。")
        st.caption(f"风险缺口：公告、停牌、涨跌停、可交易性未审核｜合成演示｜{DEMO_TIME}")
        if st.button("跟踪此演示对象"):
            if t["name"] not in st.session_state["all_day_demo_tracking"]:
                st.session_state["all_day_demo_tracking"].append(t["name"])
            st.success("已加入本次演示跟踪。")
    with tabs[4]:
        st.subheader("我的跟踪 · 本次演示")
        watched = [t for t in THEMES if t["name"] in st.session_state["all_day_demo_tracking"]]
        if not watched:
            st.info("尚未添加演示对象。")
        for t in watched:
            ref = system_reference(t["highs"], t["lows"], "unadjusted_cny_per_share", "unadjusted_cny_per_share")
            state = observation_state(t['price'], ref['prior_high'], ref['prior_low']) if ref else "资料不足"
            st.write(f"**{t['leader']}**｜{state}")
            st.caption(f"系统参考：等待 ≥ {ref['prior_high']:.2f}；失效 ≤ {ref['prior_low']:.2f}｜{DEMO_TIME}" if ref else "系统参考条件：资料不足。")
        st.subheader("持仓退出观察")
        holding = st.radio("演示持仓状态", ["未提供", "今日买入", "可卖持仓"], horizontal=True)
        if holding == "今日买入":
            st.warning("仅为用户自述，未核验交易规则或持仓；不得据此生成当日卖出指令。")
        elif holding == "可卖持仓":
            st.warning("仅为用户自述；失效价、可卖数量及风险均需人工核验，不生成卖出指令。")
        else:
            st.info("未提供持仓与可卖信息，退出条件无法判断。")
        st.text_area("演示备注（仅当前会话，不写入用户备注）", key="all_day_demo_note")
        st.caption("真实条件跟踪和人工备注仍在原“历史/离线观察”工作区，用户记录由系统标为未核实。")
