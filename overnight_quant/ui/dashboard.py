from __future__ import annotations

import html
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from overnight_quant.data.market_calendar import TAIL_SESSION, get_session_state
from overnight_quant.ui.result_parser import (
    SimpleTable,
    find_latest_file,
    parse_after_close_report,
    parse_auction_report,
    parse_after_close_chip_volume_table,
    parse_after_close_risk_table,
    parse_dry_run_report,
    parse_intraday_report,
    parse_key_value_md,
    parse_live_quality_report,
    parse_news_briefing_report,
    parse_preflight_report,
    parse_sell_plan_table,
    parse_signals_csv,
    parse_watchlist_csv,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LANGUAGE = "zh"
DEFAULT_MODE = "live"
LANGUAGES = {"zh": "中文", "en": "English"}

TEXT = {
    "zh": {
        "app_title": "A股隔夜量化观察台",
        "app_subtitle": "本地实盘观察看板，聚合盘前、尾盘、盘后和次日观察结果。",
        "language": "语言",
        "mode": "模式",
        "date": "日期",
        "date_help": "第一版读取最新匹配的报告文件。",
        "run_action": "执行安全动作",
        "refresh": "刷新最新报告",
        "output_folders": "输出目录",
        "overview": "今日总览",
        "preflight_tab": "盘前 / 盘中检查",
        "tail_tab": "尾盘 Dry-run",
        "after_close_tab": "盘后观察池",
        "replay_tab": "早盘 Replay",
        "positions_tab": "持仓 / 卖出 / 复盘",
        "reference_status": "实盘参考状态",
        "candidate_source": "候选来源",
        "after_close": "盘后状态",
        "morning_replay": "早盘 Replay",
        "raw_details": "原始详情",
        "empty_table": "暂无可展示表格行。",
        "live_reference": "正式 live 观察参考",
        "demo_only": "演示 / 测试模式",
        "safe_actions_caption": "仅可运行白名单命令；正式 Live 带尾盘门禁。",
        "formal_live_help": "正式 Live 只在 14:25-14:55 尾盘窗口可运行；会生成人工核对票据，但不会自动下单。",
        "latest_action": "最近执行结果",
        "audit_artifacts": "审计文件",
        "audit_artifacts_help": "报告和 records 仍会保留用于复盘、追溯和排错；网页主界面直接展示解析后的结果。",
        "audit_details": "审计详情",
        "result_summary": "结果摘要",
        "row_count": "行",
    },
    "en": {
        "app_title": "A-Share Overnight Quant Dashboard",
        "app_subtitle": "Local live-observation console for preflight, tail dry-run, after-close, and morning replay results.",
        "language": "Language",
        "mode": "Mode",
        "date": "Date",
        "date_help": "Phase 4.2 reads the latest matching report files.",
        "run_action": "Run safe action",
        "refresh": "Refresh latest reports",
        "output_folders": "Output folders",
        "overview": "Overview",
        "preflight_tab": "Preflight / Intraday",
        "tail_tab": "Tail Dry-run",
        "after_close_tab": "After-Close Watchlist",
        "replay_tab": "Morning Replay",
        "positions_tab": "Positions / Sell / Review",
        "reference_status": "Live Reference Status",
        "candidate_source": "Candidate Source",
        "after_close": "After Close",
        "morning_replay": "Morning Replay",
        "raw_details": "Raw Details",
        "empty_table": "No table rows available.",
        "live_reference": "Live reference observation",
        "demo_only": "Demo / test mode",
        "safe_actions_caption": "Only whitelisted commands are available; Formal Live is tail-session gated.",
        "formal_live_help": "Formal Live is allowed only during the 14:25-14:55 tail window; it can generate a manual review ticket but never places orders.",
        "latest_action": "Latest Action Result",
        "audit_artifacts": "Audit Artifacts",
        "audit_artifacts_help": "Reports and records are still retained for review, traceability, and debugging; the main page shows parsed results directly.",
        "audit_details": "Audit Details",
        "result_summary": "Result Summary",
        "row_count": "rows",
    },
}

FIELD_LABELS = {
    "zh": {
        "status": "状态",
        "trade_date": "交易日期",
        "run_time": "运行时间",
        "session_state": "时段",
        "is_trade_day": "是否交易日",
        "candidate_source": "候选来源",
        "final_advice": "最终结论",
        "valid_for_trading_observation": "是否可作实盘观察",
        "market_gate": "市场门禁",
        "market_reason": "市场原因",
        "candidate_count": "候选数量",
        "live_candidate_count": "Live 候选数",
        "demo_candidate_count": "Demo 候选数",
        "fallback_to_demo": "是否 fallback demo",
        "a_count": "A 类数量",
        "b_count": "B 类数量",
        "c_count": "C 类数量",
        "mode": "模式",
        "candidate_source_status": "候选来源状态",
        "action": "建议动作",
        "decision": "决策",
        "code": "代码",
        "name": "名称",
        "return_pct": "收益率",
        "realized_pnl": "已实现盈亏",
        "path": "文件路径",
    },
    "en": {
        "status": "Status",
        "trade_date": "Trade Date",
        "run_time": "Run Time",
        "session_state": "Session",
        "is_trade_day": "Trade Day",
        "candidate_source": "Candidate Source",
        "final_advice": "Final Advice",
        "valid_for_trading_observation": "Valid For Trading Observation",
        "market_gate": "Market Gate",
        "market_reason": "Market Reason",
        "candidate_count": "Candidate Count",
        "live_candidate_count": "Live Candidate Count",
        "demo_candidate_count": "Demo Candidate Count",
        "fallback_to_demo": "Fallback To Demo",
        "a_count": "A Count",
        "b_count": "B Count",
        "c_count": "C Count",
        "mode": "Mode",
        "candidate_source_status": "Candidate Source Status",
        "action": "Action",
        "decision": "Decision",
        "code": "Code",
        "name": "Name",
        "return_pct": "Return %",
        "realized_pnl": "Realized PnL",
        "path": "File Path",
    },
}

TABLE_COLUMN_LABELS = {
    "zh": {
        "code": "代码",
        "name": "名称",
        "score": "评分",
        "reason": "观察理由",
        "invalid_conditions": "失效条件",
        "tomorrow_watch_plan": "次日观察条件",
        "category": "分类",
        "theme_tags": "题材",
        "close_price": "收盘价",
        "change_pct": "涨跌幅%",
        "turnover_pct": "换手率%",
        "amount_wan": "成交额(万)",
        "vol_ratio": "量比",
        "main_net": "主力净额",
        "main_net_source": "主力净额来源",
        "capital_score_source": "资金评分来源",
        "estimated_capital_flow": "资金是否估算",
        "risk_flags": "风险标记",
        "reasons": "理由",
        "decision": "决策",
        "total_score": "总分",
        "price": "现价",
        "float_mcap_yi": "流通市值(亿)",
        "time": "时间",
        "data_quality_flags": "数据质量标记",
        "order_id": "记录编号",
        "trade_date": "交易日期",
        "trade_time": "成交时间",
        "side": "方向",
        "qty": "数量",
        "amount": "成交额",
        "avg_buy_price": "持仓均价",
        "open_qty": "当前持股",
        "buy_qty": "累计买入",
        "sell_qty": "累计卖出",
        "buy_amount": "买入金额",
        "sell_amount": "卖出金额",
        "realized_pnl": "已实现盈亏",
        "stop_loss_price": "止损价",
        "last_buy_time": "最近买入时间",
        "last_sell_time": "最近卖出时间",
        "status": "状态",
        "strategy_name": "记录类型",
        "recorded_at": "记录时间",
        "notes": "备注",
        "buy_price": "买入价",
        "current_price": "当前价",
        "pnl_pct": "浮盈亏%",
        "stop_loss": "止损价",
        "take_profit_1": "第一止盈价",
        "vwap": "VWAP",
        "vwap_gap_pct": "偏离VWAP%",
        "plan": "执行计划",
        "sell_trigger": "卖出触发",
        "realtime_alert": "实时提醒",
        "realtime_trigger": "实时触发",
        "intraday_trend": "分时趋势",
        "minute_fund": "分钟资金",
        "composite_action": "综合建议",
        "context_score": "环境分",
        "market_context": "大盘环境",
        "theme_context": "题材环境",
        "fund_context": "多日资金",
        "today_main_fund": "当日主力",
        "volume_context": "量价趋势",
        "chip_peak_type": "筹码峰型",
        "chip_avg_cost_20d": "20日成本proxy",
        "chip_avg_cost_60d": "60日成本proxy",
        "current_vs_chip_cost_pct": "偏离成本%",
        "overhead_pressure_ratio": "上方压力",
        "downside_support_ratio": "下方支撑",
        "main_force_chip_proxy": "主力proxy",
        "volume_signal": "量能信号",
        "confidence_delta": "置信度变化",
        "chip_volume_reasons": "筹码/量价理由",
        "level": "级别",
    },
    "en": {},
}

SOURCE_LABELS_ZH = {
    "live": "实时数据",
    "demo": "演示数据",
    "demo_fallback": "演示数据回退",
    "live_previous_close_replay": "上个收盘日回放",
    "full_market_00_60": "00/60 全市场候选",
    "eastmoney_clist": "东财全市场列表",
    "eastmoney_after_close_universe": "东财盘后全市场",
    "easyquotation_sina_full_market": "新浪全市场备用源",
    "sina_money_flow_candidate_seeds": "新浪资金流候选池",
    "tencent_quote": "腾讯实时行情",
    "tencent_quote_after_close": "腾讯盘后行情",
    "tencent_current_quote": "腾讯当前行情",
    "mootdx_quote_fallback": "通达信行情备用源",
    "eastmoney_fund_flow_minute": "东财分钟资金流",
    "eastmoney_quote_fund_flow": "东财报价资金流",
    "eastmoney_fund_flow_daily": "东财日级资金流",
    "sina_money_flow_current": "新浪实时资金流",
    "sina_money_flow_history": "新浪历史资金流",
    "estimated_from_big_order_net": "由大单净额估算",
    "baidu_daily_kline": "百度日 K",
    "eastmoney_daily_kline": "东财日 K",
    "mootdx_daily_kline": "通达信日 K",
    "eastmoney_intraday_trends": "东财分时走势",
    "quote_vwap_proxy": "行情均价代理",
    "ths_hot_reason": "同花顺热点题材",
    "ths_hot_reason_recent": "同花顺近期热点",
    "baidu_concept_blocks": "百度概念板块",
    "industry_fallback": "行业备用题材",
    "tencent_indices": "腾讯指数行情",
    "ths_hsgt": "同花顺沪深股通",
    "unknown": "未知来源",
    "missing": "缺失",
}

SOURCE_FIELD_LABELS_ZH = {
    "f62": "主力净额",
    "f66": "超大单净额",
    "f72": "大单净额",
    "main_net": "主力净额",
    "large_net": "大单净额",
    "super_net": "超大单净额",
    "r0_net": "主力净额",
    "netamount": "净额",
    "estimate": "估算",
}

DECISION_LABELS_ZH = {
    "BUY_CANDIDATE": "买入候选",
    "REJECT": "风险排除",
    "NO_TRADE": "不交易",
    "DRY_RUN_ONLY": "仅演练",
}

VALUE_LABELS_ZH = {
    "True": "是",
    "False": "否",
    "true": "是",
    "false": "否",
    "YES": "是",
    "NO": "否",
    "None": "无",
    "none": "无",
    "nan": "无",
    "NaN": "无",
}

CHIP_PEAK_LABELS_ZH = {
    "accumulation": "建仓峰",
    "washout": "洗盘峰",
    "markup": "拉升峰",
    "distribution": "出货峰",
    "neutral": "中性",
}

VOLUME_SIGNAL_LABELS_ZH = {
    "today_confirmed": "当日放量确认",
    "prev_high_volume": "前日高量待确认",
    "weak_volume": "量能未确认",
    "volume_data_missing": "量能数据缺失",
    "chip_volume_disabled": "未启用",
}

SIDE_LABELS_ZH = {
    "BUY": "买入 / 加仓",
    "SELL": "卖出 / 减仓",
}

POSITION_STATUS_LABELS_ZH = {
    "OPEN": "持仓中",
    "PARTIALLY_CLOSED": "部分卖出",
    "CLOSED": "已清仓",
    "ERROR_OVER_SOLD": "卖出超额",
    "FILLED": "已记录",
}

REASON_LABELS_ZH = {
    "prev_day_high_volume": "前一交易日阶段高量",
    "today_volume_confirm": "当日放量确认",
    "volume_not_confirmed": "成交量未确认",
    "chip_peak_accumulation": "筹码proxy接近建仓峰",
    "chip_peak_washout": "筹码proxy接近洗盘峰",
    "chip_peak_markup": "筹码proxy接近拉升峰",
    "chip_peak_distribution": "筹码proxy接近出货峰",
    "chip_peak_neutral": "筹码proxy中性",
    "overhead_pressure_high": "上方压力偏高",
    "downside_support_visible": "下方支撑可见",
    "main_force_chip_proxy_positive": "主力proxy偏正",
    "main_force_chip_proxy_negative": "主力proxy偏负",
    "main_force_chip_proxy_neutral": "主力proxy中性",
    "main_force_from_candidate_fields": "使用候选行资金字段估算",
    "main_force_from_fund_flow": "使用资金流估算",
    "main_force_proxy_missing": "主力proxy缺失",
    "chip_history_short": "历史窗口不足",
    "chip_volume_data_missing": "筹码/量价数据缺失",
    "price_ok": "价格在策略范围内",
    "price_below_min": "价格低于下限",
    "price_above_max": "价格高于上限",
    "change_pct_ok": "涨跌幅在策略范围内",
    "change_pct_below_min": "涨幅低于下限",
    "change_pct_above_max": "涨幅高于上限",
    "vol_ratio_ok": "量比达标",
    "vol_ratio_below_min": "量比低于下限",
    "vol_ratio_strong": "量比强",
    "vol_ratio_acceptable": "量比可接受",
    "vol_ratio_weak": "量比偏弱",
    "turnover_pct_ok": "换手率达标",
    "turnover_pct_below_min": "换手率低于下限",
    "turnover_pct_above_max": "换手率高于上限",
    "turnover_ideal": "换手率理想",
    "turnover_high_but_usable": "换手偏高但可观察",
    "turnover_too_high": "换手过高",
    "turnover_too_low": "换手不足",
    "amount_wan_ok": "成交额达标",
    "amount_wan_below_min": "成交额低于下限",
    "amount_strong": "成交额强",
    "amount_acceptable": "成交额可接受",
    "amount_weak": "成交额偏弱",
    "float_mcap_yi_ok": "流通市值达标",
    "float_mcap_yi_below_min": "流通市值低于下限",
    "float_mcap_yi_above_max": "流通市值高于上限",
    "near_intraday_high": "接近日内高位",
    "not_near_intraday_high": "未接近日内高位",
    "tail_stable": "尾盘承接稳定",
    "tail_pullback": "尾盘回落",
    "tail_pullback_penalty": "尾盘回落扣分",
    "tail_pullback_too_large": "尾盘回落过大",
    "upper_shadow_acceptable": "上影线可接受",
    "upper_shadow_too_long": "上影线过长",
    "upper_shadow_risk": "上影线风险",
    "shadow_controlled": "影线受控",
    "kline_missing_neutral": "K线缺失，按中性处理",
    "ma_bullish_alignment": "均线多头排列",
    "above_key_mas": "站上关键均线",
    "below_key_mas": "低于关键均线",
    "five_day_gain_controlled": "5日涨幅受控",
    "five_day_gain_overheated": "5日涨幅过热",
    "ten_day_gain_controlled": "10日涨幅受控",
    "ten_day_gain_overheated": "10日涨幅过热",
    "theme_tags_present": "有题材标签",
    "no_theme_tags": "缺少题材标签",
    "theme_missing": "题材缺失",
    "theme_missing_risk": "题材缺失风险",
    "theme_unavailable_score_discount": "题材源缺失，评分折扣",
    "theme_rank_low": "题材排名靠后",
    "top1_theme": "题材排名第一",
    "top3_theme": "题材排名前三",
    "top5_theme": "题材排名前五",
    "theme_has_group_effect": "同题材有联动效应",
    "theme_external_fallback": "题材来自外部备用源",
    "theme_present": "题材明确",
    "theme_top1": "题材强度第一",
    "theme_top3": "题材强度前三",
    "theme_top5": "题材强度前五",
    "theme_breadth": "题材有扩散度",
    "theme_mainline_continuity": "主线题材延续",
    "theme_recent_continuity": "近期题材延续",
    "theme_one_day_risk": "一日游题材风险",
    "theme_unconfirmed_recent": "近期题材未确认",
    "theme_rotation_risk": "题材轮动风险",
    "big_order_net_missing": "大单净额缺失",
    "big_order_missing": "大单资金缺失",
    "big_order_positive": "大单资金净流入",
    "big_order_negative": "大单资金净流出",
    "main_net_missing": "主力净额缺失",
    "main_net_positive": "主力资金净流入",
    "main_net_negative": "主力资金净流出",
    "capital_flow_strong": "资金流强",
    "capital_outflow": "资金流出",
    "capital_outflow_risk": "资金流出风险",
    "capital_estimated_only": "资金仅为估算",
    "estimated_capital_flow": "资金为估算值",
    "base_risk_ok": "基础风险通过",
    "st_stock": "ST 股票",
    "suspended": "停牌或无有效报价",
    "new_stock": "上市未满60天",
    "bj_stock": "北交所股票",
    "code_prefix_not_allowed": "代码前缀不在允许范围",
    "limit_up_unavailable": "涨停或接近涨停，不追",
    "limit_up_chase_risk": "涨停追高风险",
    "limit_price_unknown": "涨跌停价未知",
    "st_status_unknown": "ST 状态未知",
    "suspended_status_unknown": "停牌状态未知",
    "bj_status_unknown": "北交所状态未知",
    "list_date_missing": "上市日期缺失",
    "list_date_missing_not_found": "未找到上市日期",
    "freshness_unknown": "数据新鲜度未知",
    "quote_stale": "行情数据过期",
    "timestamp_missing": "时间戳缺失",
    "non_trading_day": "非交易日",
    "outside_tail_session": "不在尾盘窗口",
    "replay_data_too_old": "回放数据过旧",
    "replay_data_from_observation_day": "回放数据来自观察日",
    "safety_unknown": "安全字段未知",
    "safety_field_unknown": "安全字段未知",
    "major_indices_positive": "主要指数多数上涨",
    "index_tail_stable": "指数尾盘稳定",
    "index_tail_dive": "指数尾盘跳水",
    "hot_themes_present": "热点题材活跃",
    "no_clear_hot_theme": "热点题材不清晰",
    "northbound_not_extreme_outflow": "北向未极端流出",
    "northbound_extreme_outflow": "北向资金极端流出",
    "limit_down_count_controlled": "跌停数量可控",
    "market_emotion_extreme": "市场情绪极端",
    "sse_drop_too_large": "上证跌幅过大",
    "chinext_drop_too_large": "创业板跌幅过大",
    "market_gate_pass": "市场门控通过",
    "market_gate_fail": "市场门控未通过",
    "index_not_weak": "指数不弱",
    "buy_window": "买点窗口",
    "watch_window": "观察窗口",
    "outside_buy_window": "不在买点窗口",
    "after_close_category_a": "盘后 A 类候选",
    "after_close_category_b": "盘后 B 类候选",
    "price_above_vwap": "价格站上 VWAP",
    "below_or_too_close_to_vwap": "低于或过近 VWAP",
    "price_too_far_above_vwap": "高于 VWAP 过多",
    "not_far_above_vwap": "未明显偏离 VWAP",
    "vwap_pullback_reclaim": "回踩后重新站上 VWAP",
    "quote_proxy_vwap_reclaim": "行情代理 VWAP 收复",
    "intraday_lows_lifting": "盘中低点抬高",
    "rebound_volume_expansion": "反弹放量",
    "range_position_ok": "日内位置达标",
    "too_close_to_limit_up": "距离涨停过近",
    "change_ideal": "涨幅理想",
    "change_extended": "涨幅偏大",
    "change_outside_watch_range": "涨幅不在观察区间",
    "amount_adequate": "成交额够用",
    "turnover_adequate": "换手率够用",
    "volume_ratio_strong": "量比强",
    "volume_ratio_adequate": "量比够用",
    "close_near_high": "收盘接近日内高位",
    "freshness_risk": "数据新鲜度风险",
}

INLINE_SECTION_FIELDS = {
    "preflight": ["status", "trade_date", "run_time", "session_state", "is_trade_day"],
    "intraday": [
        "status",
        "trade_date",
        "session_state",
        "intraday_window",
        "candidate_source",
        "valid_for_trading_observation",
        "signal_count",
        "buy_point_a_count",
        "buy_point_b_count",
        "buy_watch_count",
    ],
    "dry_run": [
        "candidate_source",
        "final_advice",
        "valid_for_trading_observation",
        "market_gate",
        "candidate_count",
        "live_candidate_count",
        "demo_candidate_count",
        "fallback_to_demo",
    ],
    "after_close": [
        "status",
        "trade_date",
        "session_state",
        "candidate_source",
        "valid_for_trading_observation",
        "a_count",
        "b_count",
        "c_count",
    ],
    "morning_replay": [
        "status",
        "trade_date",
        "session_state",
        "candidate_source",
        "valid_for_trading_observation",
        "a_count",
        "b_count",
        "c_count",
    ],
    "sell_plan": ["status", "trade_date", "code", "name", "action", "decision"],
    "lifecycle": ["status", "trade_date", "code", "name", "realized_pnl", "return_pct"],
    "trade_review": ["status", "trade_date", "code", "name", "realized_pnl", "return_pct"],
}

AUDIT_SECTION_TITLES = {
    "preflight": "Preflight",
    "intraday": "Intraday",
    "dry_run": "Live Dry-run",
    "quality": "Live Data Quality",
    "after_close": "After-Close",
    "morning_replay": "Morning Replay",
    "sell_plan": "Sell Plan",
    "lifecycle": "Lifecycle",
    "trade_review": "Trade Review",
}

TAIL_HARD_REJECT_TOKENS = {
    "limit_up_unavailable",
    "st_stock",
    "suspended",
    "new_stock",
    "bj_stock",
    "tail_pullback_too_large",
    "price_below_min",
    "price_above_max",
    "change_pct_below_min",
    "change_pct_above_max",
    "vol_ratio_below_min",
    "turnover_pct_below_min",
    "turnover_pct_above_max",
    "amount_wan_below_min",
    "float_mcap_yi_below_min",
    "float_mcap_yi_above_max",
    "theme_missing",
    "capital_outflow",
}

TAIL_DASHBOARD_HARD_EXCLUSION_TOKENS = {
    "st_stock",
    "suspended",
    "suspended_status_unknown",
    "new_stock",
    "is_new_stock",
    "list_date_missing",
    "bj_stock",
    "bj_status_unknown",
    "code_prefix_not_allowed",
    "limit_up_unavailable",
    "limit_price_unknown",
    "limit_up_chase_risk",
    "price_below_min",
    "amount_wan_below_min",
    "turnover_pct_below_min",
    "vol_ratio_below_min",
    "float_mcap_yi_below_min",
    "float_mcap_yi_above_max",
}

ACTION_TEXT = {
    "zh": {
        "preflight": "项目通路检测",
        "news_live": "盘前消息面",
        "auction_live": "集合竞价观察",
        "intraday_live": "盘中攻防",
        "live_dry_run": "Live Dry-run",
        "formal_live_scan": "尾盘策略",
        "after_close_live": "盘后观察池",
        "morning_replay_live": "早盘 Replay",
        "sell_plan_live": "卖出计划",
        "demo_after_close": "演示：盘后观察池",
        "demo_auction": "演示：集合竞价",
        "demo_news": "演示：消息面",
        "demo_scan": "演示：Demo Scan",
    },
    "en": {
        "preflight": "Project Health Check",
        "news_live": "News Briefing",
        "auction_live": "Auction Observation",
        "intraday_live": "Intraday VWAP",
        "live_dry_run": "Live Dry-run",
        "formal_live_scan": "Formal Live Tail Scan",
        "after_close_live": "After-Close Watchlist",
        "morning_replay_live": "Morning Replay",
        "sell_plan_live": "Sell Plan",
        "demo_after_close": "Demo: After-Close",
        "demo_intraday": "Demo: Intraday VWAP",
        "demo_scan": "Demo: Scan",
    },
}

DASHBOARD_CSS = """
<style>
:root {
  --oq-bg: #07111f;
  --oq-bg-2: #0b1b2f;
  --oq-panel: rgba(13, 28, 48, 0.78);
  --oq-panel-strong: rgba(16, 35, 59, 0.92);
  --oq-border: rgba(148, 163, 184, 0.22);
  --oq-text: #e5edf5;
  --oq-muted: #8ea3b5;
  --oq-lime: #b8ff3d;
  --oq-green: #22c55e;
  --oq-yellow: #facc15;
  --oq-red: #fb5b5b;
  --oq-gray: #64748b;
}

.stApp {
  background:
    radial-gradient(circle at 12% 0%, rgba(184, 255, 61, 0.12), transparent 26%),
    radial-gradient(circle at 78% 10%, rgba(56, 189, 248, 0.12), transparent 28%),
    linear-gradient(135deg, var(--oq-bg) 0%, #091827 52%, #030712 100%);
  color: var(--oq-text);
}
.block-container { padding-top: 1.35rem; max-width: 1480px; }
section[data-testid="stSidebar"] {
  background: linear-gradient(180deg, rgba(7, 17, 31, 0.98), rgba(9, 24, 39, 0.98));
  border-right: 1px solid var(--oq-border);
}
section[data-testid="stSidebar"] * { color: var(--oq-text); }
div[data-testid="stMarkdownContainer"] p,
div[data-testid="stCaptionContainer"] { color: var(--oq-muted); }

.oq-topbar {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.oq-topbar-item,
.oq-card,
.oq-hero,
.oq-risk {
  background: var(--oq-panel);
  border: 1px solid var(--oq-border);
  box-shadow: 0 18px 60px rgba(0, 0, 0, 0.26);
  backdrop-filter: blur(16px);
}
.oq-topbar-item {
  border-radius: 8px;
  padding: 10px 13px;
}
.oq-topbar-label {
  color: var(--oq-muted);
  font-size: 12px;
  margin-bottom: 5px;
}
.oq-topbar-value {
  color: var(--oq-text);
  font-size: 14px;
  font-weight: 800;
  overflow-wrap: anywhere;
}
.oq-hero {
  border-radius: 8px;
  padding: 24px 28px;
  margin-bottom: 16px;
  position: relative;
  overflow: hidden;
}
.oq-hero::before {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(115deg, rgba(184, 255, 61, 0.14), transparent 42%, rgba(56, 189, 248, 0.08));
  pointer-events: none;
}
.oq-hero h1,
.oq-hero p,
.oq-hero .oq-hero-meta { position: relative; }
.oq-hero h1 { margin: 0 0 8px 0; font-size: 34px; letter-spacing: 0; color: #f8fafc; }
.oq-hero p { margin: 0; color: #b7c8d8; font-size: 16px; }
.oq-hero-meta {
  margin-top: 15px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.oq-card {
  border-radius: 8px;
  padding: 16px 18px;
  min-height: 116px;
  transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease;
}
.oq-card:hover,
.oq-topbar-item:hover {
  transform: translateY(-1px);
  border-color: rgba(184, 255, 61, 0.54);
  box-shadow: 0 18px 60px rgba(184, 255, 61, 0.09);
}
.oq-card-title { font-size: 13px; color: var(--oq-muted); margin-bottom: 8px; }
.oq-card-value { font-size: 22px; font-weight: 800; color: #f8fafc; overflow-wrap: anywhere; }
.oq-card-caption { font-size: 12px; color: var(--oq-muted); margin-top: 8px; }
.oq-tone-green { border-left: 5px solid var(--oq-green); }
.oq-tone-yellow { border-left: 5px solid var(--oq-yellow); }
.oq-tone-red { border-left: 5px solid var(--oq-red); }
.oq-tone-gray { border-left: 5px solid var(--oq-gray); }
.oq-kv-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 10px 12px;
  margin: 10px 0 14px 0;
}
.oq-kv-item {
  min-height: 68px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 8px;
  padding: 10px 12px;
  background: rgba(15, 23, 42, 0.54);
}
.oq-kv-label {
  color: var(--oq-muted);
  font-size: 12px;
  margin-bottom: 5px;
}
.oq-kv-value {
  color: #e8f0f8;
  font-weight: 800;
  line-height: 1.45;
  overflow-wrap: anywhere;
}
.oq-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 26px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0;
  border: 1px solid var(--oq-border);
  color: var(--oq-text);
  background: rgba(15, 23, 42, 0.68);
}
.oq-badge-green { color: #dcfce7; border-color: rgba(34, 197, 94, 0.58); background: rgba(34, 197, 94, 0.16); }
.oq-badge-yellow { color: #fef9c3; border-color: rgba(250, 204, 21, 0.58); background: rgba(250, 204, 21, 0.14); }
.oq-badge-red { color: #fee2e2; border-color: rgba(251, 91, 91, 0.62); background: rgba(251, 91, 91, 0.16); }
.oq-badge-gray { color: #dbe4ee; border-color: rgba(148, 163, 184, 0.4); background: rgba(100, 116, 139, 0.16); }
.oq-action-grid div[data-testid="stButton"] button,
.oq-action-grid .stButton button,
div[data-testid="stButton"] > button[kind="secondary"] {
  border-radius: 8px;
  min-height: 44px;
  font-weight: 800;
  border: 1px solid rgba(184, 255, 61, 0.35) !important;
  background: linear-gradient(135deg, rgba(184, 255, 61, 0.96), rgba(91, 223, 138, 0.94)) !important;
  color: #07111f !important;
}
.oq-action-grid div[data-testid="stButton"] button:hover,
.oq-action-grid .stButton button:hover,
div[data-testid="stButton"] > button[kind="secondary"]:hover {
  border-color: var(--oq-lime);
  box-shadow: 0 0 0 3px rgba(184, 255, 61, 0.12);
  filter: brightness(1.06);
}
.oq-risk {
  border-radius: 8px;
  padding: 12px 14px;
  color: #fef2f2;
  border-color: rgba(251, 91, 91, 0.38);
  background: rgba(127, 29, 29, 0.28);
}
div[data-testid="stDataFrame"],
div[data-testid="stTable"] {
  border: 1px solid var(--oq-border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--oq-panel-strong);
}
.oq-sell-card {
  border: 1px solid var(--oq-border);
  border-radius: 8px;
  padding: 16px 18px;
  margin: 12px 0 16px 0;
  background: rgba(15, 23, 42, 0.78);
}
.oq-sell-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  flex-wrap: wrap;
  border-bottom: 1px solid rgba(148, 163, 184, 0.2);
  padding-bottom: 12px;
  margin-bottom: 12px;
}
.oq-sell-title {
  font-size: 20px;
  font-weight: 900;
  color: #f8fafc;
  overflow-wrap: anywhere;
}
.oq-sell-subtitle {
  color: var(--oq-muted);
  font-size: 12px;
  margin-top: 4px;
}
.oq-sell-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}
.oq-sell-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(118px, 1fr));
  gap: 10px 14px;
  margin: 12px 0;
}
.oq-sell-metric {
  min-width: 0;
  padding-bottom: 8px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.16);
}
.oq-sell-metric-label {
  color: var(--oq-muted);
  font-size: 12px;
  margin-bottom: 3px;
}
.oq-sell-metric-value {
  color: #f8fafc;
  font-size: 17px;
  font-weight: 850;
  overflow-wrap: anywhere;
}
.oq-sell-section-title {
  margin-top: 14px;
  margin-bottom: 6px;
  color: #dbeafe;
  font-size: 13px;
  font-weight: 900;
}
.oq-sell-text {
  color: #dbe4ee;
  line-height: 1.62;
  overflow-wrap: anywhere;
}
.oq-sell-context-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 8px 14px;
}
.oq-sell-context-item {
  border-left: 3px solid rgba(184, 255, 61, 0.58);
  padding-left: 10px;
  color: #dbe4ee;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.oq-sell-context-label {
  display: block;
  color: var(--oq-muted);
  font-size: 12px;
  margin-bottom: 2px;
}
button[role="tab"] {
  color: var(--oq-muted);
  font-weight: 800;
}
button[role="tab"][aria-selected="true"] {
  color: var(--oq-lime);
}
</style>
"""

APPROVED_ACTIONS = {
    "preflight": [sys.executable, "overnight_quant/scripts/run_preflight.py"],
    "news_live": [sys.executable, "overnight_quant/scripts/run_news_briefing.py", "--mode", "live"],
    "auction_live": [sys.executable, "overnight_quant/scripts/run_auction_observation.py", "--mode", "live"],
    "intraday_live": [sys.executable, "overnight_quant/scripts/run_intraday_observation.py", "--mode", "live"],
    "live_dry_run": [sys.executable, "overnight_quant/scripts/run_scan.py", "--mode", "live", "--dry-run"],
    "formal_live_scan": [sys.executable, "overnight_quant/scripts/run_scan.py", "--mode", "live"],
    "after_close_live": [sys.executable, "overnight_quant/scripts/run_after_close_analysis.py", "--mode", "live"],
    "morning_replay_live": [
        sys.executable,
        "overnight_quant/scripts/run_after_close_analysis.py",
        "--mode",
        "live",
        "--replay-previous-close",
    ],
    "sell_plan_live": [sys.executable, "overnight_quant/scripts/run_sell_plan.py", "--mode", "live"],
    "demo_intraday": [sys.executable, "overnight_quant/scripts/run_intraday_observation.py", "--mode", "demo"],
    "demo_auction": [sys.executable, "overnight_quant/scripts/run_auction_observation.py", "--mode", "demo"],
    "demo_news": [sys.executable, "overnight_quant/scripts/run_news_briefing.py", "--mode", "demo"],
    "demo_after_close": [sys.executable, "overnight_quant/scripts/run_after_close_analysis.py", "--mode", "demo"],
    "demo_scan": [sys.executable, "overnight_quant/scripts/run_scan.py", "--mode", "demo"],
}

POSITION_UPDATE_COMMAND = [sys.executable, "overnight_quant/scripts/run_record_order.py", "--position-update"]


def t(language: str, key: str) -> str:
    pack = TEXT.get(language) or TEXT[DEFAULT_LANGUAGE]
    return pack.get(key) or TEXT["en"].get(key) or key


def action_label(language: str, action: str) -> str:
    return (ACTION_TEXT.get(language) or ACTION_TEXT[DEFAULT_LANGUAGE]).get(action, action)


def safety_notice(language: str = DEFAULT_LANGUAGE) -> str:
    if language == "en":
        return (
            "This dashboard is for research observation only and is not investment advice. "
            "It does not place orders, does not click broker software, and does not call broker trading APIs. "
            "All trading decisions and operations remain manual."
        )
    return (
        "本看板只用于研究观察，不构成投资建议；不会自动下单，不会点击证券软件，"
        "不会调用券商交易 API。所有买卖必须由你独立判断并手动执行。"
    )


def status_badge(status: str) -> dict[str, str]:
    value = str(status or "MISSING")
    upper = value.upper()
    if upper in {
        "PASS",
        "READY_FOR_LIVE_SCAN",
        "WATCHLIST_READY",
        "MORNING_REPLAY_READY",
        "INTRADAY_SIGNAL_READY",
        "DEMO_INTRADAY_OBSERVATION",
        "PROJECT_HEALTHY",
        "AUCTION_OBSERVATION_READY",
        "DEMO_AUCTION_OBSERVATION",
        "NEWS_BRIEFING_READY",
    }:
        tone = "green"
    elif upper in {"MISSING", "UNKNOWN", "NO_OPEN_POSITION"}:
        tone = "gray"
    elif "FALLBACK" in upper or "FAIL" in upper or "BLOCKED" in upper or "STALE" in upper or "UNKNOWN" in upper:
        tone = "red"
    elif (
        upper.startswith("NOT_")
        or "OUTSIDE" in upper
        or "NO_TRADE" in upper
        or "NO_WATCHLIST" in upper
        or upper in {"INTRADAY_WATCH_ONLY", "INTRADAY_NO_SIGNAL", "NO_INTRADAY_CANDIDATES", "MARKET_BLOCKED"}
    ):
        tone = "yellow"
    else:
        tone = "gray"
    return {"status": value, "tone": tone}


def premium_tab_labels(language: str) -> list[str]:
    if language == "en":
        return ["Today", "News", "Auction", "Intraday", "Tail Strategy", "After-Close Watchlist", "Positions / Sell Plan", "Audit / Maintenance"]
    return ["今日总览", "消息面", "集合竞价", "盘中攻防", "尾盘策略", "盘后观察池", "持仓/卖出计划", "审计与维护"]


def primary_action_keys() -> list[str]:
    return ["news_live", "auction_live", "intraday_live", "formal_live_scan", "after_close_live", "sell_plan_live"]


def maintenance_action_keys() -> list[str]:
    return ["preflight", "live_dry_run", "demo_news", "demo_auction", "demo_intraday", "demo_after_close", "demo_scan"]


def render_badge_html(value: str) -> str:
    badge = status_badge(value)
    status = html.escape(str(badge["status"]))
    tone = html.escape(str(badge["tone"]))
    return f'<span class="oq-badge oq-badge-{tone}">{status}</span>'


def hero_conclusion(state: dict[str, Any], language: str) -> dict[str, str]:
    dry_run = state.get("dry_run", {})
    reference = state.get("reference_summary", {})
    return {
        "headline": str(state.get("conclusion") or ""),
        "candidate_source": str(dry_run.get("candidate_source") or "MISSING"),
        "validity": str(dry_run.get("valid_for_trading_observation") or "UNKNOWN"),
        "reference_reason": str(reference.get("reason") or "unknown"),
        "tone": str(reference.get("tone") or "gray"),
        "mode_label": t(language, "live_reference") if state.get("mode", "live") == "live" else t(language, "demo_only"),
    }


def label_for(language: str, key: str) -> str:
    labels = FIELD_LABELS.get(language) or FIELD_LABELS[DEFAULT_LANGUAGE]
    return labels.get(key) or FIELD_LABELS["en"].get(key) or key.replace("_", " ").title()


def localize_table_value(field: str, value: Any, language: str) -> Any:
    if language != "zh":
        return value
    if _is_blank_value(value):
        return "无"
    field_name = str(field)
    if field_name == "side":
        return SIDE_LABELS_ZH.get(str(value).upper(), str(value))
    if field_name == "status":
        return POSITION_STATUS_LABELS_ZH.get(str(value).upper(), str(value))
    if field_name in {"decision", "final_advice"}:
        return DECISION_LABELS_ZH.get(str(value), str(value))
    if field_name == "chip_peak_type":
        return CHIP_PEAK_LABELS_ZH.get(str(value), str(value))
    if field_name == "volume_signal":
        return VOLUME_SIGNAL_LABELS_ZH.get(str(value), str(value))
    if field_name in {"estimated_capital_flow", "is_trade_day", "fallback_to_demo", "valid_for_trading_observation"}:
        return VALUE_LABELS_ZH.get(str(value), str(value))
    if field_name in {
        "risk_flags",
        "reasons",
        "reason",
        "invalid_conditions",
        "data_quality_flags",
        "risk_reasons",
        "positive_reasons",
        "info_gap_reasons",
        "missing_reasons",
        "chip_volume_reasons",
        "market_reasons",
        "market_reject_reasons",
    }:
        return localize_reason_text(value)
    if field_name in {
        "main_net_source",
        "capital_score_source",
        "fund_flow_source",
        "intraday_source",
        "theme_source",
        "candidate_source",
        "source",
        "candidate_source_status",
    }:
        return localize_source_text(value)
    return VALUE_LABELS_ZH.get(str(value), value)


def localize_reason_text(value: Any) -> str:
    if _is_blank_value(value):
        return "无"
    tokens = _split_reason_value(value)
    if not tokens:
        return VALUE_LABELS_ZH.get(str(value), str(value))
    return "；".join(_localize_reason_token(token) for token in tokens)


def localize_source_text(value: Any) -> str:
    if _is_blank_value(value):
        return "无"
    text = str(value).strip()
    if text.startswith("capital_score_source:"):
        return f"资金评分来源：{localize_source_text(text.split(':', 1)[1])}"
    if text in SOURCE_LABELS_ZH:
        return SOURCE_LABELS_ZH[text]
    if "." in text:
        source, field = text.rsplit(".", 1)
        source_label = SOURCE_LABELS_ZH.get(source, source)
        field_label = SOURCE_FIELD_LABELS_ZH.get(field, field)
        return f"{source_label}：{field_label}"
    return SOURCE_LABELS_ZH.get(text, text)


def _localize_reason_token(token: str) -> str:
    token = token.strip()
    if not token:
        return ""
    if token.startswith("capital_score_source:"):
        return f"资金评分来源：{localize_source_text(token.split(':', 1)[1])}"
    if token.startswith("buy_window:"):
        return f"买点窗口：{_localize_window(token.split(':', 1)[1])}"
    if token.startswith("watch_window:"):
        return f"观察窗口：{_localize_window(token.split(':', 1)[1])}"
    if token.startswith("outside_buy_window:"):
        return f"不在买点窗口：{_localize_window(token.split(':', 1)[1])}"
    return REASON_LABELS_ZH.get(token, VALUE_LABELS_ZH.get(token, token))


def _localize_window(value: str) -> str:
    return {
        "PRIMARY_BUY": "首次买点",
        "SECONDARY_BUY": "二次买点",
        "AFTERNOON_RECLAIM": "下午收复",
        "OPEN_FILTER": "开盘过滤",
        "AUCTION_OBSERVE": "竞价观察",
        "NO_NEW_BUY": "不新增买点",
        "AFTER_CLOSE": "盘后",
    }.get(str(value), str(value))


def _split_reason_value(value: Any) -> list[str]:
    text = str(value).strip()
    if text in VALUE_LABELS_ZH:
        return [text]
    if "|" not in text and ";" not in text and "；" not in text:
        if "," in text or "，" in text:
            separator = "," if "," in text else "，"
            possible = [item.strip() for item in text.split(separator) if item.strip()]
            if possible and all(_looks_like_internal_token(item) for item in possible):
                return possible
        return [text]
    parts = []
    for raw in text.replace("；", ";").replace("|", ";").split(";"):
        for item in raw.split(","):
            token = item.strip()
            if token and token.lower() != "nan":
                parts.append(token)
    return parts


def _looks_like_internal_token(value: str) -> bool:
    token = value.strip()
    return token in REASON_LABELS_ZH or token in VALUE_LABELS_ZH or ":" in token or ("_" in token and " " not in token)


def _is_blank_value(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return not text or text.lower() in {"nan", "none", "null"}


def table_result_summary(table, language: str) -> str:
    if getattr(table, "empty", True):
        return "0 行" if language == "zh" else "0 rows"
    try:
        row_count = len(table.to_dict("records"))
    except Exception:
        try:
            row_count = len(table)
        except Exception:
            row_count = 0
    unit = t(language, "row_count")
    return f"{row_count} {unit}"


def split_tail_signal_rows(table) -> tuple[SimpleTable, SimpleTable]:
    rows = _table_records(table)
    raw_columns = getattr(table, "columns", [])
    columns = list(raw_columns) if raw_columns is not None else []
    if not columns and rows:
        columns = list(rows[0])
    observable: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        if _is_tail_observable_row(row):
            observable.append(row)
        else:
            rejected.append(row)
    return SimpleTable(observable, columns), SimpleTable(rejected, columns)


def split_tail_rejection_rows(table) -> tuple[SimpleTable, SimpleTable]:
    rows = _table_records(table)
    raw_columns = getattr(table, "columns", [])
    columns = list(raw_columns) if raw_columns is not None else []
    if not columns and rows:
        columns = list(rows[0])
    main_risk: list[dict[str, Any]] = []
    hard_excluded: list[dict[str, Any]] = []
    for row in rows:
        tokens = _split_tokens(row.get("risk_flags", "")) | _split_tokens(row.get("reasons", ""))
        if TAIL_DASHBOARD_HARD_EXCLUSION_TOKENS.intersection(tokens):
            hard_excluded.append(row)
        else:
            main_risk.append(row)
    return SimpleTable(main_risk, columns), SimpleTable(hard_excluded, columns)


def _table_records(table) -> list[dict[str, Any]]:
    if getattr(table, "empty", True):
        return []
    try:
        records = table.to_dict("records")
    except Exception:
        return []
    normalized: list[dict[str, Any]] = []
    for row in records:
        item = dict(row)
        if "code" in item:
            item["code"] = _normalize_stock_code(item.get("code"))
        normalized.append(item)
    return normalized


def _is_tail_observable_row(row: dict[str, Any]) -> bool:
    if str(row.get("decision", "")).strip().upper() != "BUY_CANDIDATE":
        return False
    risk_flags = _split_tokens(row.get("risk_flags", ""))
    reasons = _split_tokens(row.get("reasons", ""))
    return not risk_flags and not TAIL_HARD_REJECT_TOKENS.intersection(reasons)


def _split_tokens(value: Any) -> set[str]:
    if value in (None, ""):
        return set()
    return {part.strip() for part in str(value).split("|") if part.strip() and part.strip().lower() != "nan"}


def _normalize_stock_code(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return ""
    if text.endswith(".0"):
        text = text[:-2]
    if text.isdigit():
        return text.zfill(6)
    return text


def _as_float_value(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_int_value(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def inline_result_rows(section_key: str, data: dict[str, Any], language: str) -> list[tuple[str, str]]:
    fields = INLINE_SECTION_FIELDS.get(section_key, ["status"])
    rows: list[tuple[str, str]] = []
    for field in fields:
        value = data.get(field)
        if value in (None, ""):
            continue
        rows.append((label_for(language, field), str(localize_table_value(field, value, language))))
    if not rows:
        status = data.get("status", "MISSING")
        rows.append((label_for(language, "status"), str(status)))
    return rows


def build_inline_result_sections(state: dict[str, Any], language: str) -> dict[str, list[tuple[str, str]]]:
    return {
        "preflight": inline_result_rows("preflight", state.get("preflight", {}), language),
        "intraday": inline_result_rows("intraday", state.get("intraday", {}), language),
        "dry_run": inline_result_rows("dry_run", state.get("dry_run", {}), language),
        "after_close": inline_result_rows("after_close", state.get("after_close", {}), language),
        "morning_replay": inline_result_rows("morning_replay", state.get("morning_replay", {}), language),
        "sell_plan": inline_result_rows("sell_plan", state.get("sell_plan", {}), language),
        "lifecycle": inline_result_rows("lifecycle", state.get("lifecycle", {}), language),
        "trade_review": inline_result_rows("trade_review", state.get("trade_review", {}), language),
    }


def audit_file_rows(state: dict[str, Any], language: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for key, title in AUDIT_SECTION_TITLES.items():
        path = state.get(key, {}).get("path")
        if path:
            rows.append((title, str(path)))
    return rows


def live_reference_summary(state: dict[str, Any]) -> dict[str, Any]:
    mode = state.get("mode", DEFAULT_MODE)
    dry_run = state.get("dry_run", {})
    quality = state.get("quality", {})
    candidate_source = dry_run.get("candidate_source", "")
    valid_flag = dry_run.get("valid_for_trading_observation", "")
    quality_text = " ".join(str(value) for value in quality.values())
    if mode != "live":
        return {"valid": False, "tone": "yellow", "reason": "demo_only"}
    if candidate_source == "demo_fallback":
        return {"valid": False, "tone": "red", "reason": "demo_fallback"}
    if "freshness_unknown" in quality_text or "quote_stale" in quality_text:
        return {"valid": False, "tone": "red", "reason": "data_quality_blocked"}
    if valid_flag == "NO":
        return {"valid": False, "tone": "yellow", "reason": "dry_run_only"}
    return {"valid": True, "tone": "green", "reason": "live_reference"}


def run_dashboard_action(
    action: str,
    language: str = DEFAULT_LANGUAGE,
    now: datetime | None = None,
    timeout: int = 180,
) -> dict[str, Any]:
    if action == "formal_live_scan":
        session_state = get_session_state(now)
        if session_state != TAIL_SESSION:
            return {
                "ok": False,
                "command_ran": False,
                "action": action,
                "error": "FORMAL_LIVE_OUTSIDE_TAIL_SESSION",
                "session_state": session_state,
                "message": formal_live_block_message(language, session_state),
            }
    result = run_approved_action(action, timeout=timeout)
    return action_feedback(action, result, language)


def formal_live_block_message(language: str, session_state: str) -> str:
    if language == "en":
        return (
            f"Current session is {session_state}, not the 14:25-14:55 tail window. "
            "Formal Live cannot generate a manual ticket now. Use Live Dry-run for data quality, candidates, and rejection reasons."
        )
    return (
        f"当前不是尾盘窗口（当前时段：{session_state}），不能生成正式 live 买入票据。"
        "请在 14:25-14:55 再运行正式 Live；当前可用 Live Dry-run 查看数据质量、候选和拒绝原因。"
    )


def action_feedback(action: str, result: dict[str, Any], language: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
    ok = bool(result.get("ok"))
    if not ok:
        return {
            "ok": False,
            "command_ran": True,
            "action": action,
            "severity": "error",
            "error": result.get("error") or f"RETURN_CODE_{result.get('returncode', '')}",
            "message": _action_failure_message(action, result, language),
        }
    warning_message = _action_success_warning_message(action, result, language)
    return {
        "ok": True,
        "command_ran": True,
        "action": action,
        "severity": "warning" if warning_message else "success",
        "message": warning_message or _action_success_message(action, language),
    }


def _action_success_message(action: str, language: str) -> str:
    if language == "en":
        messages = {
            "preflight": "Preflight completed. The latest parsed status is shown below.",
            "intraday_live": "Intraday VWAP observation completed. Review the intraday signals below.",
            "live_dry_run": "Live Dry-run completed. Review Tail candidates, rejection audit, and live data quality below.",
            "formal_live_scan": "Formal Live scan completed. If a manual ticket was generated, the buy review details are shown below.",
            "after_close_live": "After-close analysis completed. Review the watchlist section below.",
            "morning_replay_live": "Morning replay completed. Review the replay watchlist below.",
            "sell_plan_live": "Sell plan completed. Review the sell-plan section below.",
            "demo_intraday": "Demo intraday VWAP observation completed.",
            "demo_after_close": "Demo after-close analysis completed.",
            "demo_scan": "Demo scan completed.",
        }
    else:
        messages = {
            "preflight": "盘前检查已完成，最新状态已在下方展示。",
            "live_dry_run": "Live Dry-run 已完成。请查看下方尾盘候选、拒绝审计和数据质量。",
            "formal_live_scan": "正式 Live 已执行。如生成了人工买入票据，买入核对信息已在下方展示。",
            "after_close_live": "盘后观察池已生成，请查看下方观察池区域。",
            "morning_replay_live": "早盘 Replay 已完成，请查看下方 Replay 观察池。",
            "sell_plan_live": "卖出计划已生成，请查看下方卖出计划区域。",
            "demo_after_close": "演示盘后观察池已生成。",
            "demo_scan": "演示扫描已完成。",
        }
    return messages.get(action, "Action completed." if language == "en" else "动作已完成。")


def _action_failure_message(action: str, result: dict[str, Any], language: str) -> str:
    code = result.get("error") or result.get("returncode") or "UNKNOWN"
    if language == "en":
        return f"{action_label(language, action)} did not complete successfully. Status: {code}. Check the audit artifacts below."
    return f"{action_label(language, action)} 未成功完成。状态：{code}。请查看下方审计文件。"


def _action_success_warning_message(action: str, result: dict[str, Any], language: str) -> str:
    status = _command_output_field(result.get("stdout", ""), "Status")
    if action == "after_close_live" and status in {"NOT_AFTER_CLOSE", "NOT_TRADING_DAY"}:
        session_state = _command_output_field(result.get("stdout", ""), "Session State") or "UNKNOWN"
        if language == "en":
            return (
                f"After-close command ran, but the formal watchlist was not generated. "
                f"Status: {status}; session: {session_state}. Run it from 14:50 onward."
            )
        return (
            "盘后观察池命令已运行，但本次没有生成正式观察池。"
            f"状态：{status}；当前时段：{session_state}。请在交易日 14:50 后再运行。"
        )
    if action == "morning_replay_live" and status == "NOT_REPLAY_WINDOW":
        session_state = _command_output_field(result.get("stdout", ""), "Session State") or "UNKNOWN"
        if language == "en":
            return (
                f"Morning replay command ran, but replay is outside its allowed window. "
                f"Status: {status}; session: {session_state}."
            )
        return f"早盘 Replay 命令已运行，但当前不在允许窗口。状态：{status}；当前时段：{session_state}。"
    if action == "intraday_live" and status in {"NOT_INTRADAY_WINDOW", "NO_INTRADAY_CANDIDATES", "MARKET_BLOCKED"}:
        session_state = _command_output_field(result.get("stdout", ""), "Session State") or "UNKNOWN"
        intraday_window = _command_output_field(result.get("stdout", ""), "Intraday Window") or "UNKNOWN"
        if language == "en":
            return (
                f"Intraday command ran, but no formal buy-point reminder was generated. "
                f"Status: {status}; session: {session_state}; window: {intraday_window}."
            )
        return (
            "Intraday VWAP command ran, but no buy-point reminder was generated. "
            f"Status: {status}; session: {session_state}; window: {intraday_window}."
        )
    if status in {"DATA_FALLBACK_DEMO", "DATA_QUALITY_BLOCKED", "REPLAY_DATA_FALLBACK_DEMO", "REPLAY_DATA_QUALITY_BLOCKED"}:
        if language == "en":
            return f"{action_label(language, action)} command ran, but formal output was blocked. Status: {status}."
        return f"{action_label(language, action)} 命令已运行，但正式输出被阻断。状态：{status}。"
    return ""


def _command_output_field(stdout: str, label: str) -> str:
    prefix = f"{label}:"
    for line in str(stdout or "").splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def run_approved_action(action: str, timeout: int = 180) -> dict[str, Any]:
    if action not in APPROVED_ACTIONS:
        return {"ok": False, "error": "ACTION_NOT_APPROVED", "action": action}
    try:
        completed = subprocess.run(
            APPROVED_ACTIONS[action],
            shell=False,
            timeout=timeout,
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "error": "ACTION_TIMEOUT", "stdout": exc.stdout or "", "stderr": exc.stderr or ""}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def run_position_update_action(
    code: str,
    name: str,
    side: str,
    price: float,
    qty: int,
    trade_time: str,
    notes: str = "",
    stop_loss_price: float | str = "",
    mode: str = DEFAULT_MODE,
    timeout: int = 45,
) -> dict[str, Any]:
    state_arg = "example" if mode == "demo" else "real"
    args = [
        *POSITION_UPDATE_COMMAND,
        "--state",
        state_arg,
        "--code",
        _normalize_stock_code(code),
        "--name",
        str(name or ""),
        "--side",
        str(side).upper(),
        "--price",
        str(price),
        "--qty",
        str(int(qty)),
        "--trade-time",
        str(trade_time),
        "--notes",
        str(notes or ""),
    ]
    if stop_loss_price not in ("", None, 0, 0.0):
        args.extend(["--stop-loss", str(stop_loss_price)])
    try:
        completed = subprocess.run(
            args,
            shell=False,
            timeout=timeout,
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "error": "ACTION_TIMEOUT", "stdout": exc.stdout or "", "stderr": exc.stderr or ""}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def position_update_feedback(result: dict[str, Any], language: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
    if result.get("ok"):
        return {
            "ok": True,
            "command_ran": True,
            "action": "position_update",
            "severity": "success",
            "message": "持仓更新已保存。" if language == "zh" else "Position update saved.",
        }
    reasons = _command_output_reasons(result.get("stdout", ""))
    reason_text = "、".join(reasons) if reasons else str(result.get("error") or result.get("returncode") or "UNKNOWN")
    message = (
        f"持仓更新未保存，原因：{reason_text}。"
        if language == "zh"
        else f"Position update was not saved. Reason: {reason_text}."
    )
    return {
        "ok": False,
        "command_ran": True,
        "action": "position_update",
        "severity": "error",
        "error": reason_text,
        "message": message,
    }


def _command_output_reasons(stdout: str) -> list[str]:
    reasons: list[str] = []
    in_reasons = False
    for line in str(stdout or "").splitlines():
        stripped = line.strip()
        if stripped == "Reasons:":
            in_reasons = True
            continue
        if in_reasons and stripped.startswith("- "):
            reasons.append(stripped[2:].strip())
        elif in_reasons and stripped:
            break
    return reasons


def build_position_summary_table(manual_orders) -> SimpleTable:
    rows = _table_records(manual_orders)
    positions: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = _normalize_stock_code(row.get("code"))
        if not code:
            continue
        side = str(row.get("side") or "BUY").upper()
        qty = _as_int_value(row.get("qty") or row.get("quantity"))
        price = _as_float_value(row.get("price") or row.get("buy_price"))
        amount = _as_float_value(row.get("amount")) or round(qty * price, 2)
        position = positions.setdefault(
            code,
            {
                "code": code,
                "name": row.get("name", ""),
                "status": "OPEN",
                "open_qty": 0,
                "avg_buy_price": 0.0,
                "buy_qty": 0,
                "sell_qty": 0,
                "buy_amount": 0.0,
                "sell_amount": 0.0,
                "realized_pnl": 0.0,
                "stop_loss_price": _as_float_value(row.get("stop_loss_price")),
                "last_buy_time": "",
                "last_sell_time": "",
            },
        )
        if row.get("name"):
            position["name"] = row.get("name")
        if side == "BUY":
            position["buy_qty"] += qty
            position["open_qty"] += qty
            position["buy_amount"] += amount
            position["last_buy_time"] = row.get("trade_time", "")
            position["stop_loss_price"] = _as_float_value(row.get("stop_loss_price")) or position["stop_loss_price"]
        elif side == "SELL":
            position["sell_qty"] += qty
            position["open_qty"] -= qty
            position["sell_amount"] += amount
            position["last_sell_time"] = row.get("trade_time", "")
    columns = [
        "code",
        "name",
        "status",
        "open_qty",
        "avg_buy_price",
        "buy_qty",
        "sell_qty",
        "buy_amount",
        "sell_amount",
        "realized_pnl",
        "stop_loss_price",
        "last_buy_time",
        "last_sell_time",
    ]
    summary_rows: list[dict[str, Any]] = []
    for position in positions.values():
        buy_qty = _as_int_value(position.get("buy_qty"))
        sell_qty = _as_int_value(position.get("sell_qty"))
        open_qty = _as_int_value(position.get("open_qty"))
        avg_buy = round(_as_float_value(position.get("buy_amount")) / buy_qty, 4) if buy_qty else 0.0
        position["avg_buy_price"] = avg_buy
        position["realized_pnl"] = round(_as_float_value(position.get("sell_amount")) - avg_buy * min(sell_qty, buy_qty), 2)
        position["status"] = _position_status_label(open_qty, buy_qty, sell_qty)
        summary_rows.append({column: position.get(column, "") for column in columns})
    return SimpleTable(summary_rows, columns)


def _position_status_label(open_qty: int, buy_qty: int, sell_qty: int) -> str:
    if open_qty < 0 or sell_qty > buy_qty:
        return "ERROR_OVER_SOLD"
    if buy_qty > 0 and sell_qty == 0 and open_qty > 0:
        return "OPEN"
    if buy_qty > 0 and sell_qty > 0 and open_qty > 0:
        return "PARTIALLY_CLOSED"
    if buy_qty > 0 and sell_qty == buy_qty and open_qty == 0:
        return "CLOSED"
    return "OPEN"


def load_dashboard_state(mode: str = DEFAULT_MODE, root: Path | None = None) -> dict[str, Any]:
    base = Path(root) if root else ROOT
    package = base / "overnight_quant"
    reports = package / ("examples/reports" if mode == "demo" else "reports")
    records = package / ("examples/records" if mode == "demo" else "records")

    preflight_path = find_latest_file("preflight_*.md", reports)
    dry_run_path = find_latest_file("dry_run_scan_*.md", reports)
    intraday_path = find_latest_file("intraday_observation_*.md", reports)
    auction_path = find_latest_file("auction_observation_*.md", reports)
    news_path = find_latest_file("news_briefing_*.md", reports)
    quality_path = find_latest_file("live_data_quality_*.md", reports)
    after_close_path = find_latest_file("after_close_analysis_*.md", reports)
    replay_path = find_latest_file("morning_replay_analysis_*.md", reports)
    watchlist_path = find_latest_file("next_morning_watchlist_*.csv", records)
    replay_watchlist_path = find_latest_file("morning_replay_watchlist_*.csv", records)
    intraday_signals_path = find_latest_file("intraday_buy_signals_*.csv", records)
    auction_rows_path = find_latest_file("auction_observation_*.csv", records)
    signals_path = records / "signals.csv"
    signal_rejections_path = records / "signal_rejections.csv"
    manual_orders_path = records / "manual_orders.csv"
    buy_ticket_path = find_latest_file("manual_order_" + "ticket_*.md", reports)
    sell_plan_path = find_latest_file("sell_plan_*.md", reports)
    lifecycle_path = find_latest_file("trade_lifecycle_*.md", reports)
    review_path = find_latest_file("trade_review_*.md", reports)

    state = {
        "mode": mode,
        "reports_dir": str(reports),
        "records_dir": str(records),
        "preflight": parse_preflight_report(preflight_path or reports / "preflight_missing.md"),
        "intraday": parse_intraday_report(intraday_path or reports / "intraday_missing.md"),
        "auction": parse_auction_report(auction_path or reports / "auction_missing.md"),
        "news_briefing": parse_news_briefing_report(news_path or reports / "news_briefing_missing.md"),
        "dry_run": parse_dry_run_report(dry_run_path or reports / "dry_run_missing.md"),
        "quality": parse_live_quality_report(quality_path or reports / "quality_missing.md"),
        "after_close": parse_after_close_report(after_close_path or reports / "after_close_missing.md"),
        "morning_replay": parse_after_close_report(replay_path or reports / "morning_replay_missing.md"),
        "watchlist": parse_watchlist_csv(watchlist_path or records / "next_morning_watchlist_missing.csv"),
        "after_close_risk_rows": parse_after_close_risk_table(after_close_path or reports / "after_close_missing.md"),
        "after_close_chip_volume_rows": parse_after_close_chip_volume_table(
            after_close_path or reports / "after_close_missing.md"
        ),
        "morning_replay_risk_rows": parse_after_close_risk_table(replay_path or reports / "morning_replay_missing.md"),
        "morning_replay_chip_volume_rows": parse_after_close_chip_volume_table(
            replay_path or reports / "morning_replay_missing.md"
        ),
        "morning_replay_watchlist": parse_watchlist_csv(
            replay_watchlist_path or records / "morning_replay_watchlist_missing.csv"
        ),
        "intraday_signals": parse_signals_csv(intraday_signals_path or records / "intraday_buy_signals_missing.csv"),
        "auction_rows": parse_signals_csv(auction_rows_path or records / "auction_observation_missing.csv"),
        "signals": parse_signals_csv(signals_path),
        "signal_rejections": parse_signals_csv(signal_rejections_path),
        "manual_orders": parse_signals_csv(manual_orders_path),
        "buy_ticket": parse_key_value_md(buy_ticket_path or reports / ("manual_order_" + "ticket_missing.md")),
        "sell_plan": parse_key_value_md(sell_plan_path or reports / "sell_plan_missing.md"),
        "sell_plan_rows": parse_sell_plan_table(sell_plan_path or reports / "sell_plan_missing.md"),
        "lifecycle": parse_key_value_md(lifecycle_path or reports / "lifecycle_missing.md"),
        "trade_review": parse_key_value_md(review_path or reports / "trade_review_missing.md"),
    }
    state["position_summary"] = build_position_summary_table(state["manual_orders"])
    state["reference_summary"] = live_reference_summary(state)
    state["conclusion"] = build_status_conclusion(state)
    return state


def load_sell_plan_state(mode: str = DEFAULT_MODE, root: Path | None = None) -> dict[str, Any]:
    base = Path(root) if root else ROOT
    package = base / "overnight_quant"
    reports = package / ("examples/reports" if mode == "demo" else "reports")

    sell_plan_path = find_latest_file("sell_plan_*.md", reports)
    lifecycle_path = find_latest_file("trade_lifecycle_*.md", reports)
    review_path = find_latest_file("trade_review_*.md", reports)

    return {
        "sell_plan": parse_key_value_md(sell_plan_path or reports / "sell_plan_missing.md"),
        "sell_plan_rows": parse_sell_plan_table(sell_plan_path or reports / "sell_plan_missing.md"),
        "lifecycle": parse_key_value_md(lifecycle_path or reports / "lifecycle_missing.md"),
        "trade_review": parse_key_value_md(review_path or reports / "trade_review_missing.md"),
    }


def formal_live_buy_plan_rows(state: dict[str, Any], language: str = DEFAULT_LANGUAGE) -> list[tuple[str, str]]:
    ticket = state.get("buy_ticket") or {}
    if ticket.get("status") == "MISSING":
        return []
    code = str(ticket.get("code", "")).zfill(6) if ticket.get("code") else ""
    suggested = str(ticket.get("suggested_price", ""))
    max_price = str(ticket.get("max_acceptable_price", ""))
    if language == "en":
        labels = {
            "code": "Stock Code",
            "name": "Name",
            "price_range": "Price Range",
            "amount": "Position Amount",
            "quantity": "Suggested Quantity",
            "stop_loss": "Stop Loss",
            "next_plan": "Next-Morning Sell Plan",
            "ticket": "Manual Ticket",
        }
    else:
        labels = {
            "code": "股票代码",
            "name": "股票名称",
            "price_range": "价格区间",
            "amount": "仓位金额",
            "quantity": "建议数量",
            "stop_loss": "止损价",
            "next_plan": "明早卖出计划",
            "ticket": "人工票据",
        }
    rows = [
        (labels["code"], code),
        (labels["name"], str(ticket.get("name", ""))),
        (labels["price_range"], f"{suggested} - {max_price}".strip(" -")),
        (labels["amount"], str(ticket.get("suggested_amount", ""))),
        (labels["quantity"], str(ticket.get("suggested_quantity", ""))),
        (labels["stop_loss"], str(ticket.get("stop_loss", ""))),
        (labels["next_plan"], str(ticket.get("next_day_plan", ""))),
    ]
    if ticket.get("path"):
        rows.append((labels["ticket"], str(ticket["path"])))
    return [(label, value) for label, value in rows if value]


def tail_usage_guidance(state: dict[str, Any], language: str = DEFAULT_LANGUAGE) -> list[str]:
    preflight = state.get("preflight") or {}
    session_state = str(preflight.get("session_state") or get_session_state())
    if language == "en":
        lines = [
            "Live Dry-run always rehearses with live data and never generates a manual ticket.",
            "Formal Live can generate a manual review ticket only in the 14:25-14:55 tail window.",
        ]
        if session_state != TAIL_SESSION:
            lines.append(f"Current session is {session_state}; Formal Live is blocked outside the tail window.")
        else:
            lines.append("Current session is TAIL_SESSION; Formal Live may generate a manual ticket if all gates pass.")
        lines.extend(
            [
                "Dry-run files: reports/dry_run_scan_YYYY-MM-DD.md, records/signals.csv, records/signal_rejections.csv, reports/live_data_quality_YYYY-MM-DD.md.",
                "Formal Live files: reports/live_scan_summary_YYYY-MM-DD.md, reports/manual_order_ticket_YYYY-MM-DD_CODE.md, records/signals.csv, records/signal_rejections.csv.",
            ]
        )
        return lines
    lines = [
        "Live Dry-run 永远只是用 live 数据演练，不会生成正式人工买入票据。",
        "正式 Live 只有在 14:25-14:55 尾盘窗口才允许运行，且只生成供你手动核对的票据。",
    ]
    if session_state != TAIL_SESSION:
        lines.append(f"当前时段为 {session_state}，不在尾盘窗口，正式 Live 已阻断。")
    else:
        lines.append("当前处于 TAIL_SESSION；正式 Live 只有在全部 gate 通过时才会生成票据。")
    lines.extend(
        [
            "Dry-run 看：reports/dry_run_scan_YYYY-MM-DD.md、records/signals.csv、records/signal_rejections.csv、reports/live_data_quality_YYYY-MM-DD.md。",
            "正式 Live 看：reports/live_scan_summary_YYYY-MM-DD.md、reports/manual_order_ticket_YYYY-MM-DD_CODE.md、records/signals.csv、records/signal_rejections.csv。",
        ]
    )
    return lines


def build_status_conclusion(state: dict[str, Any], language: str = DEFAULT_LANGUAGE) -> str:
    summary = live_reference_summary(state)
    dry_run = state.get("dry_run", {})
    preflight = state.get("preflight", {})
    if summary["reason"] == "demo_fallback":
        return (
            "当前候选来自 demo fallback，不能作为实盘参考。"
            if language == "zh"
            else "Current candidates come from demo fallback and are not valid for live reference."
        )
    if summary["reason"] == "demo_only":
        return (
            "当前为演示模式，只用于测试界面和流程。"
            if language == "zh"
            else "Current mode is demo only and is for UI/process testing."
        )
    if summary["reason"] == "data_quality_blocked":
        return (
            "当前数据存在 stale 或 freshness_unknown，不能作为实盘参考。"
            if language == "zh"
            else "Current data has stale or freshness_unknown flags and is not valid for live reference."
        )
    if dry_run.get("valid_for_trading_observation") == "NO":
        return (
            "当前不具备正式观察或交易意义，仅适合流程演练。"
            if language == "zh"
            else "Current output is dry-run only and not meaningful as a formal trading observation."
        )
    status = preflight.get("status")
    if status and status not in {"MISSING", "UNKNOWN", "READY_FOR_LIVE_SCAN"}:
        return (
            f"当前 preflight 状态为 {status}，请按风险提示处理。"
            if language == "zh"
            else f"Current preflight status is {status}; follow the risk warning."
        )
    return (
        "当前为 live 观察参考模式，但仍只提供观察信息，不产生任何委托或交易动作。"
        if language == "zh"
        else "Current mode is live reference observation only; no order or trading action is produced."
    )


def main() -> None:
    try:
        import streamlit as st
    except Exception:
        print("UI_DEPENDENCY_MISSING: please run pip install -r requirements-ui.txt")
        return

    st.set_page_config(page_title="Overnight Quant Dashboard", layout="wide", initial_sidebar_state="expanded")
    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)

    language_label = st.radio("语言 / Language", list(LANGUAGES.values()), horizontal=True, key="dashboard_language")
    language = "zh" if language_label == "中文" else "en"

    with st.sidebar:
        mode_options = [t(language, "live_reference"), t(language, "demo_only")]
        selected_mode = st.radio(t(language, "mode"), mode_options, index=0, horizontal=False)
        mode = "demo" if selected_mode == t(language, "demo_only") else "live"
        st.text_input(t(language, "date"), "latest", help=t(language, "date_help"))
        st.caption(t(language, "safe_actions_caption"))
        st.caption(t(language, "formal_live_help"))
        action = st.selectbox(t(language, "run_action"), primary_action_keys(), format_func=lambda item: action_label(language, item))
        st.markdown('<div class="oq-action-grid">', unsafe_allow_html=True)
        if st.button(action_label(language, action), use_container_width=True):
            st.session_state["last_action_feedback"] = run_dashboard_action(action, language)
            st.rerun()
        if st.button(t(language, "refresh"), use_container_width=True):
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
        with st.expander(t(language, "audit_artifacts")):
            st.caption(t(language, "audit_artifacts_help"))
            st.code(f"reports: {state_dir_for_mode(mode, 'reports')}\nrecords: {state_dir_for_mode(mode, 'records')}")

    state = load_dashboard_state(mode=mode)
    state["conclusion"] = build_status_conclusion(state, language=language)

    _render_top_status_bar(st, state, language)
    _render_premium_hero(st, state, language)
    st.markdown(f'<div class="oq-risk">{html.escape(safety_notice(language))}</div>', unsafe_allow_html=True)
    _render_action_feedback(st, st.session_state.get("last_action_feedback"), language)
    _render_action_grid(st, language)
    _render_overview(st, state, language)

    tabs = st.tabs(premium_tab_labels(language))
    with tabs[0]:
        _render_overview(st, state, language)
    with tabs[1]:
        _render_report_section(st, "盘前消息面" if language == "zh" else "News Briefing", state["news_briefing"], language, "news_briefing")
    with tabs[2]:
        _render_report_section(st, "集合竞价" if language == "zh" else "Auction", state["auction"], language, "auction")
        render_table_or_empty(st, state["auction_rows"], language, t(language, "empty_table"))
    with tabs[3]:
        _render_report_section(st, "盘中攻防" if language == "zh" else "Intraday Attack / Defence", state["intraday"], language, "intraday")
        render_table_or_empty(st, state["intraday_signals"], language, t(language, "empty_table"))
    with tabs[4]:
        st.caption("原尾盘策略窗口：交易日 14:25-14:55。" if language == "zh" else "Existing tail strategy window: trading days 14:25-14:55.")
        _render_report_section(st, "尾盘策略审计" if language == "zh" else "Tail Strategy Audit", state["dry_run"], language, "dry_run")
        st.markdown("#### 正式 Live 买入核对信息" if language == "zh" else "#### Formal Live Buy Review")
        plan_rows = formal_live_buy_plan_rows(state, language)
        if plan_rows:
            render_key_value_rows(st, plan_rows, language)
        else:
            st.caption("暂无正式 Live 人工买入票据。" if language == "zh" else "No formal Live manual ticket is available.")
        _render_guidance(st, tail_usage_guidance(state, language))
        observable, legacy_rejected = split_tail_signal_rows(state["signals"])
        raw_rejected = state["signal_rejections"] if not state["signal_rejections"].empty else legacy_rejected
        rejected, hard_excluded = split_tail_rejection_rows(raw_rejected)
        st.markdown("#### 尾盘可观察候选" if language == "zh" else "#### Tail Observable Candidates")
        render_table_or_empty(st, observable, language, t(language, "empty_table"))
        st.markdown("#### 风险排除 / 不观察" if language == "zh" else "#### Risk Exclusions / Do Not Observe")
        render_table_or_empty(st, rejected, language, t(language, "empty_table"))
        if not hard_excluded.empty:
            with st.expander("基础硬排除审计" if language == "zh" else "Base Hard-Exclusion Audit"):
                render_table_or_empty(st, hard_excluded, language, t(language, "empty_table"))
    with tabs[5]:
        st.caption("盘后观察池使用独立盘后分析策略，交易日 14:50 起可运行。" if language == "zh" else "The separate after-close analysis is available from 14:50 on trading days.")
        _render_report_section(st, "盘后观察池" if language == "zh" else "After-Close Watchlist", state["after_close"], language, "after_close")
        st.markdown("#### A/B 正式观察池" if language == "zh" else "#### A/B Formal Observation")
        render_table_or_empty(st, state["watchlist"], language, t(language, "empty_table"))
        st.markdown("#### 筹码与量价确认" if language == "zh" else "#### Chip / Volume Confirmation")
        render_table_or_empty(st, state["after_close_chip_volume_rows"], language, t(language, "empty_table"))
        st.markdown("#### C 类风险观察 / 不建议追" if language == "zh" else "#### C Class Risk Observation / Do Not Chase")
        render_table_or_empty(st, state["after_close_risk_rows"], language, t(language, "empty_table"))
    with tabs[6]:
        _render_position_update(st, state, language, mode)
        def _render_sell_plan_tab() -> None:
            _render_sell_plan_page(st, state, language, mode)
        fragment = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)
        if callable(fragment):
            try:
                fragment(run_every="60s")(_render_sell_plan_tab)()
            except TypeError:
                fragment(_render_sell_plan_tab)()
        else:
            _render_sell_plan_tab()
    with tabs[7]:
        st.markdown("#### " + ("维护动作" if language == "zh" else "Maintenance Actions"))
        maintenance = maintenance_action_keys()
        cols = st.columns(3)
        for index, action_key in enumerate(maintenance):
            if cols[index % 3].button(action_label(language, action_key), use_container_width=True, key=f"maintenance_{action_key}"):
                st.session_state["last_action_feedback"] = run_dashboard_action(action_key, language)
                st.rerun()
        _render_report_section(st, "项目通路检测" if language == "zh" else "Project Health Check", state["preflight"], language, "preflight")
        _render_report_section(st, "尾盘审计" if language == "zh" else "Tail Audit", state["dry_run"], language, "dry_run")
        with st.expander(t(language, "audit_artifacts")):
            render_key_value_rows(st, audit_file_rows(state, language), language)


def _render_top_status_bar(st, state: dict[str, Any], language: str) -> None:
    news = state.get("news_briefing", {})
    auction = state.get("auction", {})
    intraday = state.get("intraday", {})
    tail = state.get("dry_run", {})
    after_close = state.get("after_close", {})
    items = [
        ("mode", t(language, "mode"), hero_conclusion(state, language)["mode_label"]),
        ("status", "消息面" if language == "zh" else "News", news.get("status", "MISSING")),
        ("status", "集合竞价" if language == "zh" else "Auction", auction.get("market_auction_bias", auction.get("status", "MISSING"))),
        ("status", "盘中/尾盘策略" if language == "zh" else "Intraday / Tail", f"{intraday.get('status', 'MISSING')} / {tail.get('status', 'MISSING')}"),
        ("status", "盘后观察池" if language == "zh" else "After Close", after_close.get("status", "MISSING")),
    ]
    cells = []
    for field, label, value in items:
        cells.append(
            '<div class="oq-topbar-item">'
            f'<div class="oq-topbar-label">{html.escape(str(label))}</div>'
            f'<div class="oq-topbar-value">{html.escape(str(localize_table_value(field, value or "MISSING", language)))}</div>'
            "</div>"
        )
    st.markdown(f'<div class="oq-topbar">{"".join(cells)}</div>', unsafe_allow_html=True)


def _render_premium_hero(st, state: dict[str, Any], language: str) -> None:
    hero = hero_conclusion(state, language)
    st.markdown(
        '<div class="oq-hero">'
        f'<h1>{html.escape(t(language, "app_title"))}</h1>'
        f'<p>{html.escape(hero["headline"] or t(language, "app_subtitle"))}</p>'
        '<div class="oq-hero-meta">'
        f'{render_badge_html(hero["reference_reason"])}'
        f'{render_badge_html(hero["candidate_source"])}'
        f'{render_badge_html(hero["validity"])}'
        "</div></div>",
        unsafe_allow_html=True,
    )


def _render_action_grid(st, language: str) -> None:
    st.markdown('<div class="oq-action-grid">', unsafe_allow_html=True)
    action_keys = primary_action_keys()
    for start in range(0, len(action_keys), 3):
        row_actions = action_keys[start : start + 3]
        cols = st.columns(3)
        for col, action in zip(cols, row_actions):
            if col.button(action_label(language, action), use_container_width=True, key=f"main_{action}"):
                st.session_state["last_action_feedback"] = run_dashboard_action(action, language)
                st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def _render_overview(st, state: dict[str, Any], language: str) -> None:
    st.subheader(t(language, "overview"))
    st.info(state["conclusion"])
    first_row = st.columns(3)
    second_row = st.columns(3)
    render_status_card(
        first_row[0],
        "消息面方向" if language == "zh" else "News Direction",
        state["news_briefing"].get("status", "MISSING"),
        status_badge(state["news_briefing"].get("status", "MISSING"))["tone"],
    )
    render_status_card(
        first_row[1],
        "集合竞价方向" if language == "zh" else "Auction Direction",
        state["auction"].get("market_auction_bias", state["auction"].get("status", "MISSING")),
        status_badge(state["auction"].get("status", "MISSING"))["tone"],
    )
    render_status_card(
        first_row[2],
        "盘中攻防" if language == "zh" else "Intraday",
        state["intraday"].get("status", "MISSING"),
        status_badge(state["intraday"].get("status", "MISSING"))["tone"],
    )
    render_status_card(
        second_row[0],
        "尾盘策略" if language == "zh" else "Tail Strategy",
        state["dry_run"].get("status", "MISSING"),
        status_badge(state["dry_run"].get("status", "MISSING"))["tone"],
    )
    render_status_card(
        second_row[1],
        "盘后观察池" if language == "zh" else "After Close",
        state["after_close"].get("status", "MISSING"),
        status_badge(state["after_close"].get("status", "MISSING"))["tone"],
    )
    position_rows = _table_records(state.get("position_summary"))
    risk_status = state.get("sell_plan", {}).get("status", "NO_OPEN_POSITION" if not position_rows else "REVIEW_REQUIRED")
    render_status_card(second_row[2], "持仓风险" if language == "zh" else "Position Risk", risk_status, status_badge(risk_status)["tone"])


def _render_report_section(st, title: str, data: dict[str, Any], language: str, section_key: str) -> None:
    primary_value = data.get("status") or data.get("final_advice") or data.get("candidate_source") or "MISSING"
    badge = status_badge(primary_value)
    render_status_card(st, title, badge["status"], badge["tone"], t(language, "result_summary"))
    rows = inline_result_rows(section_key, data, language)
    if rows:
        render_key_value_rows(st, rows, language)
    with st.expander(t(language, "audit_details")):
        if data.get("path"):
            st.caption(f"{label_for(language, 'path')}: {data.get('path')}")
        st.json(data)


def _render_sell_plan_page(st, state: dict[str, Any], language: str, mode: str = DEFAULT_MODE) -> None:
    session_state = getattr(st, "session_state", {})
    override = session_state.get("sell_plan_state_override") if hasattr(session_state, "get") else None
    if isinstance(override, dict) and override.get("mode") == mode:
        state = {**state, **override}

    title = "卖出计划" if language == "zh" else "Sell Plan"
    st.markdown(f"#### {title}")

    controls = st.columns([1.1, 1.1, 3])
    refresh_label = "刷新实时卖出提醒" if language == "zh" else "Refresh Realtime Sell Alert"
    auto_label = "60秒自动刷新" if language == "zh" else "Auto-refresh 60s"
    refresh_clicked = controls[0].button(refresh_label, use_container_width=True, key="refresh_sell_plan_realtime")
    auto_refresh = controls[1].toggle(auto_label, value=False, key="sell_plan_auto_refresh")

    should_refresh = bool(refresh_clicked)
    if auto_refresh:
        now_ts = datetime.now().timestamp()
        last_ts = float(session_state.get("sell_plan_auto_refresh_at", 0) or 0) if hasattr(session_state, "get") else 0.0
        if now_ts - last_ts > 55:
            if hasattr(session_state, "__setitem__"):
                session_state["sell_plan_auto_refresh_at"] = now_ts
            should_refresh = True

    if should_refresh:
        feedback = run_dashboard_action("sell_plan_live", language)
        refreshed = load_sell_plan_state(mode=mode)
        refreshed["mode"] = mode
        if hasattr(session_state, "__setitem__"):
            session_state["last_action_feedback"] = feedback
            session_state["sell_plan_state_override"] = refreshed
        state = {**state, **refreshed}

    controls[2].caption(
        "只刷新卖出计划区域：重新拉取当前价、分时VWAP和分钟资金，只生成提醒与报告，不会下单。"
        if language == "zh"
        else "Only the sell-plan panel refreshes: it fetches current price, intraday VWAP, and minute fund flow. It only updates alerts and reports; it never places orders."
    )

    sell_plan = state.get("sell_plan", {})
    rows = _table_records(state.get("sell_plan_rows"))
    status = str(sell_plan.get("status") or "MISSING")
    status_tone = status_badge(status)["tone"]
    cols = st.columns(3)
    render_status_card(cols[0], "状态" if language == "zh" else "Status", status, status_tone)
    render_status_card(cols[1], "计划日期" if language == "zh" else "Plan Date", sell_plan.get("trade_date", "MISSING"), "gray")
    render_status_card(cols[2], "持仓数量" if language == "zh" else "Open Positions", str(len(rows)), "green" if rows else "gray")

    if not rows:
        st.info("当前没有需要生成卖出计划的持仓。" if language == "zh" else "No open positions need a sell plan.")
    else:
        for row in rows:
            _render_sell_plan_card(st, row, language)

    with st.expander("原始报告与审计数据" if language == "zh" else "Raw Reports And Audit Data"):
        if sell_plan.get("path"):
            st.caption(f"{label_for(language, 'path')}: {sell_plan.get('path')}")
        st.markdown("##### 原始卖出表" if language == "zh" else "##### Raw Sell Plan Table")
        render_table_or_empty(st, state.get("sell_plan_rows"), language, t(language, "empty_table"))
        _render_compact_report_details(st, "Lifecycle", state.get("lifecycle", {}), language, "lifecycle")
        _render_compact_report_details(st, "Trade Review", state.get("trade_review", {}), language, "trade_review")


def _render_sell_plan_card(st, row: dict[str, Any], language: str) -> None:
    code = _clean_display_text(row.get("code"))
    name = _clean_display_text(row.get("name")) or code
    action = _clean_display_text(row.get("action"))
    composite = _clean_display_text(row.get("composite_action"))
    level = _clean_display_text(row.get("level"))
    tone = _sell_plan_tone(row)
    badges = "".join(render_badge_html(value) for value in [action, f"级别 {level}" if level else "", composite] if value)
    metrics = [
        ("数量", row.get("qty")),
        ("买入价", row.get("buy_price")),
        ("现价", row.get("current_price")),
        ("浮盈亏%", row.get("pnl_pct")),
        ("止损价", row.get("stop_loss")),
        ("第一止盈", row.get("take_profit_1")),
        ("VWAP", row.get("vwap")),
        ("偏离VWAP%", row.get("vwap_gap_pct")),
    ]
    metric_html = "".join(_sell_metric_html(label, value) for label, value in metrics)
    plan = _clean_display_text(row.get("plan"))
    realtime_alert = _clean_display_text(row.get("realtime_alert")) or plan
    trigger = _clean_display_text(row.get("realtime_trigger")) or _clean_display_text(row.get("sell_trigger"))
    context_items = [
        ("分时", row.get("intraday_trend")),
        ("分钟资金", row.get("minute_fund")),
        ("大盘", row.get("market_context")),
        ("题材", row.get("theme_context")),
        ("多日资金", row.get("fund_context")),
        ("当日主力", row.get("today_main_fund")),
        ("量价", row.get("volume_context")),
    ]
    context_html = "".join(_sell_context_html(label, value) for label, value in context_items if _clean_display_text(value))
    st.markdown(
        f"""
        <div class="oq-sell-card oq-tone-{html.escape(tone)}">
          <div class="oq-sell-header">
            <div>
              <div class="oq-sell-title">{html.escape(code)} {html.escape(name)}</div>
              <div class="oq-sell-subtitle">环境分：{html.escape(_clean_display_text(row.get("context_score")) or "无")}</div>
            </div>
            <div class="oq-sell-badges">{badges}</div>
          </div>
          <div class="oq-sell-metrics">{metric_html}</div>
          <div class="oq-sell-section-title">实时提醒</div>
          <div class="oq-sell-text">{html.escape(realtime_alert or "暂无实时提醒")}</div>
          <div class="oq-sell-section-title">执行计划</div>
          <div class="oq-sell-text">{html.escape(plan or "暂无执行计划")}</div>
          <div class="oq-sell-section-title">实时卖出/减仓触发</div>
          <div class="oq-sell-text">{html.escape(trigger or "暂无触发条件")}</div>
          <div class="oq-sell-section-title">综合环境</div>
          <div class="oq-sell-context-grid">{context_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_compact_report_details(st, title: str, data: dict[str, Any], language: str, section_key: str) -> None:
    rows = inline_result_rows(section_key, data, language)
    if not rows and not data:
        return
    status = data.get("status") or data.get("final_advice") or data.get("candidate_source") or "UNKNOWN"
    st.markdown(f"##### {title}: {status}")
    summary = "；".join(f"{label}: {value}" for label, value in rows[:5])
    if summary:
        st.caption(summary)
    if data.get("path"):
        st.caption(f"{label_for(language, 'path')}: {data.get('path')}")
    st.json(data)


def _sell_metric_html(label: str, value: Any) -> str:
    text = _clean_display_text(value) or "无"
    return (
        '<div class="oq-sell-metric">'
        f'<div class="oq-sell-metric-label">{html.escape(label)}</div>'
        f'<div class="oq-sell-metric-value">{html.escape(text)}</div>'
        "</div>"
    )


def _sell_context_html(label: str, value: Any) -> str:
    text = _clean_display_text(value) or "无"
    return (
        '<div class="oq-sell-context-item">'
        f'<span class="oq-sell-context-label">{html.escape(label)}</span>'
        f'{html.escape(text)}'
        "</div>"
    )


def _sell_plan_tone(row: dict[str, Any]) -> str:
    action = str(row.get("action", ""))
    score = _as_float_value(row.get("context_score"))
    if "止损" in action or "卖出" in action or score <= -2:
        return "red"
    if "观察" in action or score >= 1:
        return "green"
    return "yellow"


def _clean_display_text(value: Any) -> str:
    if _is_blank_value(value):
        return ""
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") and text.replace(".", "", 1).isdigit() else text


def _render_position_update(st, state: dict[str, Any], language: str, mode: str) -> None:
    st.markdown("#### 持仓更新" if language == "zh" else "#### Position Update")
    st.caption(
        "只记录你手动输入的成交和持仓变化；不会自动下单，也不会改动选股策略。"
        if language == "zh"
        else "Records only manually entered fills and position changes; it does not place orders or change strategy logic."
    )
    with st.form("position_update_form"):
        col1, col2, col3 = st.columns(3)
        code = col1.text_input("股票代码" if language == "zh" else "Code", placeholder="000034", max_chars=6)
        name = col2.text_input("名称（可选）" if language == "zh" else "Name (optional)", placeholder="神州数码")
        side = col3.selectbox(
            "方向" if language == "zh" else "Side",
            ["BUY", "SELL"],
            format_func=lambda value: SIDE_LABELS_ZH.get(value, value) if language == "zh" else value,
        )
        col4, col5, col6 = st.columns(3)
        price = col4.number_input("成交价" if language == "zh" else "Fill Price", min_value=0.0, step=0.01, format="%.3f")
        qty = col5.number_input("数量（股）" if language == "zh" else "Quantity (shares)", min_value=0, step=100)
        stop_loss = col6.number_input("止损价（可选）" if language == "zh" else "Stop Loss (optional)", min_value=0.0, step=0.01, format="%.3f")
        trade_time = st.text_input(
            "成交时间" if language == "zh" else "Trade Time",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        notes = st.text_input("备注" if language == "zh" else "Notes", placeholder="已有持仓 / 尾盘买入 / 分批减仓")
        submitted = st.form_submit_button("保存持仓更新" if language == "zh" else "Save Position Update")
    if submitted:
        validation_error = _position_form_validation_error(code, price, qty, trade_time, language)
        if validation_error:
            st.session_state["position_update_feedback"] = {
                "ok": False,
                "command_ran": False,
                "severity": "error",
                "message": validation_error,
            }
        else:
            result = run_position_update_action(
                code=code,
                name=name,
                side=side,
                price=price,
                qty=int(qty),
                trade_time=trade_time,
                notes=notes,
                stop_loss_price=stop_loss,
                mode=mode,
            )
            st.session_state["position_update_feedback"] = position_update_feedback(result, language)
        st.rerun()
    _render_action_feedback(st, st.session_state.get("position_update_feedback"), language)
    st.markdown("#### 当前持仓" if language == "zh" else "#### Current Positions")
    render_table_or_empty(st, state["position_summary"], language, t(language, "empty_table"))
    st.markdown("#### 手工成交记录" if language == "zh" else "#### Manual Fill Records")
    render_table_or_empty(st, state["manual_orders"], language, t(language, "empty_table"))


def _position_form_validation_error(code: str, price: float, qty: int, trade_time: str, language: str) -> str:
    normalized_code = _normalize_stock_code(code)
    if not normalized_code.isdigit() or len(normalized_code) != 6:
        return "请输入 6 位股票代码。" if language == "zh" else "Enter a 6-digit stock code."
    if float(price) <= 0:
        return "成交价必须大于 0。" if language == "zh" else "Fill price must be greater than 0."
    if int(qty) <= 0 or int(qty) % 100 != 0:
        return "数量必须是大于 0 的 100 股整数倍。" if language == "zh" else "Quantity must be a positive multiple of 100 shares."
    try:
        datetime.strptime(str(trade_time), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return "成交时间格式应为 YYYY-MM-DD HH:MM:SS。" if language == "zh" else "Trade time must use YYYY-MM-DD HH:MM:SS."
    return ""


def _render_action_feedback(st, feedback: dict[str, Any] | None, language: str) -> None:
    if not feedback:
        return
    title = t(language, "latest_action")
    message = str(feedback.get("message") or "")
    if feedback.get("severity") == "warning":
        st.warning(f"{title}: {message}")
    elif feedback.get("ok"):
        st.success(f"{title}: {message}")
    elif feedback.get("severity") == "error":
        st.error(f"{title}: {message}")
    elif feedback.get("command_ran") is False:
        st.warning(f"{title}: {message}")
    else:
        st.error(f"{title}: {message}")


def _render_guidance(st, lines: list[str]) -> None:
    if not lines:
        return
    html_lines = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
    st.markdown(f'<div class="oq-card"><ul>{html_lines}</ul></div>', unsafe_allow_html=True)


def render_status_card(st, title: str, value: str, tone: str, caption: str = "") -> None:
    st.markdown(
        f"""
        <div class="oq-card oq-tone-{html.escape(tone)}">
          <div class="oq-card-title">{html.escape(str(title))}</div>
          <div class="oq-card-value">{html.escape(str(value or "MISSING"))}</div>
          <div class="oq-card-caption">{html.escape(str(caption or ""))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_key_value_rows(st, rows: list[tuple[str, str]], language: str) -> None:
    if not rows:
        st.caption(t(language, "empty_table"))
        return
    cells = []
    for label, value in rows:
        cells.append(
            '<div class="oq-kv-item">'
            f'<div class="oq-kv-label">{html.escape(str(label))}</div>'
            f'<div class="oq-kv-value">{html.escape(str(value))}</div>'
            "</div>"
        )
    st.markdown(f'<div class="oq-kv-grid">{"".join(cells)}</div>', unsafe_allow_html=True)


def render_table_or_empty(st, table, language: str, empty_text: str) -> None:
    if getattr(table, "empty", True):
        st.caption(empty_text)
    else:
        st.caption(table_result_summary(table, language))
        data = localized_table_records(table, language)
        st.dataframe(data, use_container_width=True)


def localized_table_records(table, language: str):
    records = _table_records(table)
    labels = TABLE_COLUMN_LABELS.get(language, {})
    if not labels:
        return records
    localized_rows = []
    for row in records:
        localized_rows.append({labels.get(key, key): localize_table_value(key, value, language) for key, value in row.items()})
    return localized_rows


def state_dir_for_mode(mode: str, kind: str) -> str:
    if mode == "demo":
        return str(ROOT / "overnight_quant" / "examples" / kind)
    return str(ROOT / "overnight_quant" / kind)


if __name__ == "__main__":
    main()
