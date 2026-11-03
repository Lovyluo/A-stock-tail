"""Explicit test-only Streamlit harness using the real workspace and fixture worker."""
import json
import os
from pathlib import Path

import streamlit as st

from overnight_quant.data.offline_price_observation import ObservationStore
from overnight_quant.data.quote_observation_refresh import RefreshController
from overnight_quant.ui.offline_observation import render_offline_observation


st.set_page_config(page_title="观察刷新演练 · test-only", layout="wide", initial_sidebar_state="collapsed")
st.info("synthetic_test_only · 注入响应演练；零真实报价/NTP，不是来源验收或联网许可。")
root = os.environ.get("OBSERVATION_DEMO_ROOT")
paths = json.loads(os.environ.get("OBSERVATION_DEMO_FIXTURES", "[]"))
if not root or len(paths) != 2 or any(not Path(path).is_file() for path in paths):
    st.error("必须提供隔离演练目录和两份显式合成响应文件")
    st.stop()
selected = st.sidebar.selectbox("测试响应（非行情）", paths, format_func=lambda p: Path(p).stem)
st.session_state.setdefault("observation_scope", "测试材料 · test-only")
st.session_state.setdefault("observation_mode", "主动刷新观察")
controller = RefreshController(ObservationStore(root, "synthetic_test_only"), fixture=selected)
render_offline_observation(st, storage_root=root, refresh_controller=controller)
