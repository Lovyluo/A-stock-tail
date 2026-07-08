from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import logging
from pathlib import Path
import urllib.parse
import urllib.request

from overnight_quant.data.demo_data import (
    demo_current_price,
    demo_daily_kline,
    demo_intraday_bars,
    demo_market_snapshot,
    demo_quotes,
)
from overnight_quant.data.live_data_quality import (
    LiveDataQualityReport,
    normalize_live_stock,
    validate_stock_fields,
)
from overnight_quant.data.market_calendar import (
    CN_TZ,
    NON_TRADING_DAY,
    TAIL_SESSION,
    get_session_state,
    is_likely_cn_trade_day,
)


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
DATA_CONTEXT_CURRENT = "current_live"
DATA_CONTEXT_PREVIOUS_CLOSE_REPLAY = "previous_close_replay"
LIVE_DATA_SOURCE_PRIORITY = {
    "quote": ["tencent_quote", "mootdx_quote_fallback"],
    "daily_kline": ["baidu_daily_kline", "eastmoney_daily_kline", "mootdx_daily_kline"],
    "intraday_bars": ["eastmoney_intraday_trends"],
    "fund_flow": [
        "eastmoney_fund_flow_minute",
        "eastmoney_quote_fund_flow",
        "sina_money_flow_current",
        "eastmoney_fund_flow_daily",
        "sina_money_flow_history",
    ],
    "theme": ["ths_hot_reason_recent", "baidu_concept_blocks", "eastmoney_core_conception", "industry_fallback"],
    "market": ["tencent_indices", "ths_hsgt"],
}
LIVE_SCAN_DEFAULT_MAX_CANDIDATES = 100
EASTMONEY_CLIST_PAGE_SIZE = 100
EASTMONEY_CLIST_MAX_PAGES = 5


class AStockClient:
    def __init__(
        self,
        mode: str = "demo",
        logger: logging.Logger | None = None,
        now: datetime | None = None,
        allow_outside_session: bool = False,
        cache_dir: str | Path | None = None,
        data_context: str = DATA_CONTEXT_CURRENT,
        expected_data_date: str | None = None,
    ):
        self.mode = mode
        self.logger = logger or logging.getLogger(__name__)
        self.fallback_messages: list[str] = []
        self.now = self._coerce_now(now)
        self.allow_outside_session = allow_outside_session
        self.cache_dir = Path(cache_dir) if cache_dir else Path(__file__).resolve().parent / "cache"
        self.data_context = data_context
        self.expected_data_date = expected_data_date or ""
        if self.data_context == DATA_CONTEXT_PREVIOUS_CLOSE_REPLAY and not self.expected_data_date:
            raise ValueError("expected_data_date required for previous_close_replay")
        self._list_date_cache: dict[str, dict] | None = None
        self._stock_info_errors: dict[str, str] = {}
        self._fund_flow_errors: dict[str, str] = {}
        self._kline_freshness_reasons: dict[str, list[str]] = {}
        self._recent_hot_theme_context_cache: dict | None = None
        self._sina_money_flow_current_cache: dict[str, dict] | None = None
        self._eastmoney_board_quote_cache: dict[str, dict] = {}
        self.after_close_candidate_source = ""
        self.quality_report = LiveDataQualityReport(
            mode=mode,
            trade_date=self.now.date().isoformat(),
            run_time=self.now.isoformat(),
        )
        if self.data_context == DATA_CONTEXT_PREVIOUS_CLOSE_REPLAY:
            self.quality_report.set_replay_context(self.expected_data_date, "previous_close_expected")

    def get_market_snapshot(self) -> dict:
        if self.mode == "demo":
            return demo_market_snapshot()
        if self.data_context == DATA_CONTEXT_PREVIOUS_CLOSE_REPLAY:
            return self._replay_market_snapshot()
        try:
            snapshot = demo_market_snapshot()
            self._apply_session_context(snapshot)
            indices = self._tencent_quotes(["sh000001", "sh000300", "sz399006"])
            if indices:
                snapshot["indices"] = {
                    "sh000001": {"name": "SSE Composite", "change_pct": indices.get("000001", {}).get("change_pct", 0)},
                    "sh000300": {"name": "CSI 300", "change_pct": indices.get("000300", {}).get("change_pct", 0)},
                    "sz399006": {"name": "ChiNext", "change_pct": indices.get("399006", {}).get("change_pct", 0)},
                }
                self.quality_report.record_source("tencent_indices", True, len(indices))
            northbound = self._hsgt_realtime()
            if northbound:
                last = northbound[-1]
                snapshot["northbound_net_yi"] = float(last.get("hgt_yi", 0) or 0) + float(last.get("sgt_yi", 0) or 0)
                self.quality_report.record_source("ths_hsgt", True, len(northbound))
            return snapshot
        except Exception as exc:
            self.quality_report.record_source("live_market_snapshot", False, 0, str(exc))
            snapshot = self._fallback(f"market snapshot live failed, fallback to demo market: {type(exc).__name__}: {exc}", demo_market_snapshot)
            self._apply_session_context(snapshot)
            return snapshot

    def get_candidate_quotes(self) -> list[dict]:
        if self.mode == "demo":
            return demo_quotes()
        try:
            seeds = self._fetch_live_candidate_seeds()
            if not seeds:
                raise RuntimeError("all live candidate sources returned empty")
            live = self._build_live_candidates(seeds)
            if not live:
                raise RuntimeError("empty live quote response")
            return live
        except Exception as exc:
            message = f"live data failed, fallback to demo: {type(exc).__name__}: {exc}"
            self.quality_report.mark_fallback(message)
            return self._fallback(message, demo_quotes)

    def get_after_close_universe_quotes(self) -> list[dict]:
        if self.mode == "demo":
            return [row for row in demo_quotes() if self._is_allowed_after_close_code(row.get("code", ""))]
        try:
            source, seeds = self._after_close_universe_seeds()
            try:
                return self._build_after_close_universe_from_seeds(source, seeds)
            except Exception as primary_exc:
                if source != "eastmoney_after_close_universe":
                    raise
                try:
                    secondary = self._easyquotation_after_close_universe_seeds()
                    if not secondary:
                        raise RuntimeError("easyquotation_sina_full_market: empty")
                    self._record_source_if_missing("easyquotation_sina_full_market", True, len(secondary))
                    return self._build_after_close_universe_from_seeds("easyquotation_sina_full_market", secondary)
                except Exception as secondary_exc:
                    raise RuntimeError(f"{primary_exc}; secondary fallback failed: {secondary_exc}") from secondary_exc
        except Exception as exc:
            message = f"after-close universe live data failed, fallback to demo: {type(exc).__name__}: {exc}"
            self.quality_report.mark_fallback(message)
            return self._fallback(message, lambda: [row for row in demo_quotes() if self._is_allowed_after_close_code(row.get("code", ""))])

    def _build_after_close_universe_from_seeds(self, source: str, seeds: list[dict]) -> list[dict]:
        self.after_close_candidate_source = source
        prefiltered = [seed for seed in seeds if self._after_close_prefilter_candidate_passes(seed)]
        if not prefiltered:
            raise RuntimeError(f"after-close {source} returned no pre-qualified 00/60 rows")
        prefiltered = self._merge_after_close_hot_themes(prefiltered)
        live = self._build_after_close_candidates(prefiltered)
        if not live:
            raise RuntimeError(f"after-close {source} enrichment returned empty")
        filtered = [row for row in live if self._after_close_base_candidate_passes(row)]
        if not filtered:
            raise RuntimeError(f"after-close {source} returned no base-qualified rows after enrichment")
        return filtered

    def get_daily_kline(self, code: str, lookback: int = 120) -> list[dict]:
        if self.mode == "live":
            normalized_code = str(code).zfill(6)
            self._kline_freshness_reasons[normalized_code] = []
            errors: list[str] = []
            try:
                rows = self._baidu_daily_kline(code, lookback)
                if rows:
                    freshness = self._freshness_for_date(
                        "baidu_daily_kline",
                        self._normalize_data_date(rows[-1].get("date", "")),
                    )
                    self.quality_report.record_source("baidu_daily_kline", True, len(rows), freshness=freshness)
                    self._kline_freshness_reasons[normalized_code] = self._freshness_rejection_reasons(freshness)
                    return rows
                raise RuntimeError("empty baidu kline response")
            except Exception as exc:
                errors.append(f"baidu_daily_kline:{exc}")
                freshness = {
                    "source": "baidu_daily_kline",
                    "data_date": "",
                    "data_time": "",
                    "is_stale": False,
                    "stale_reason": "freshness_unknown",
                }
                self.quality_report.record_source("baidu_daily_kline", False, 0, str(exc), freshness=freshness)
            for source_name, fetcher in (
                ("eastmoney_daily_kline", self._eastmoney_daily_kline),
                ("mootdx_daily_kline", self._mootdx_daily_kline),
            ):
                try:
                    rows = fetcher(normalized_code, lookback)
                    if rows:
                        freshness = self._freshness_for_date(
                            source_name,
                            self._normalize_data_date(rows[-1].get("date", "")),
                        )
                        self.quality_report.record_source(source_name, True, len(rows), freshness=freshness)
                        self._kline_freshness_reasons[normalized_code] = self._freshness_rejection_reasons(freshness)
                        return rows
                    raise RuntimeError(f"empty {source_name} response")
                except Exception as exc:
                    errors.append(f"{source_name}:{exc}")
                    self.quality_report.record_source(source_name, False, 0, f"{normalized_code}: {exc}")
            freshness = {
                "source": "daily_kline_fallback_chain",
                "data_date": "",
                "data_time": "",
                "is_stale": False,
                "stale_reason": "freshness_unknown",
            }
            self.quality_report.record_source(
                "daily_kline_fallback_chain",
                False,
                0,
                "; ".join(errors[:3]),
                freshness=freshness,
            )
            self._kline_freshness_reasons[normalized_code] = self._freshness_rejection_reasons(freshness)
            self._note(f"live kline failed, using demo kline: {'; '.join(errors[:3])}")
        return demo_daily_kline(code, lookback)

    def get_kline_freshness_reasons(self, code: str) -> list[str]:
        return list(self._kline_freshness_reasons.get(str(code).zfill(6), []))

    def get_recent_hot_theme_summary(self, limit: int = 10) -> list[dict]:
        context = self._recent_hot_theme_context_cache or {}
        themes = context.get("themes") or {}
        rows = []
        for theme, item in themes.items():
            active_days = int(item.get("active_days", 0) or 0)
            if active_days >= 3:
                trend = "mainline"
            elif active_days >= 2:
                trend = "continuing"
            elif item.get("latest_date") == context.get("latest_date"):
                trend = "new_or_one_day"
            else:
                trend = "fading"
            rows.append(
                {
                    "theme": theme,
                    "active_days": active_days,
                    "count": int(item.get("count", 0) or 0),
                    "latest_date": item.get("latest_date", ""),
                    "trend": trend,
                }
            )
        return sorted(rows, key=lambda row: (row["active_days"], row["count"]), reverse=True)[:limit]

    def get_current_price(self, code: str) -> dict:
        if self.mode == "demo":
            return demo_current_price(code)
        try:
            quote = self._preferred_quote_map([code], "tencent_current_quote").get(str(code).zfill(6))
            if not quote:
                raise RuntimeError("current price missing")
            last_close = quote.get("last_close", 0.0)
            open_price = quote.get("open", 0.0)
            open_change_pct = self._pct_gap(open_price, last_close) if last_close and open_price else quote.get("change_pct", 0.0)
            return {
                "code": code,
                "name": quote.get("name", ""),
                "price": quote.get("price", 0.0),
                "open_price": open_price,
                "open_change_pct": open_change_pct,
                "change_pct": quote.get("change_pct", 0.0),
                "current_change_pct": quote.get("change_pct", 0.0),
                "last_close": last_close,
                "high": quote.get("high", 0.0),
                "low": quote.get("low", 0.0),
                "amount_wan": quote.get("amount_wan", 0.0),
                "volume": quote.get("volume", 0.0),
                "turnover_pct": quote.get("turnover_pct", 0.0),
                "vol_ratio": quote.get("vol_ratio", 0.0),
                "limit_up": quote.get("limit_up", 0.0),
                "limit_down": quote.get("limit_down", 0.0),
                "limit_up_gap_pct": self._pct_gap(quote.get("limit_up", 0.0), quote.get("price", 0.0)),
                "vwap": self._quote_vwap(quote),
                "is_limit_up": quote.get("price", 0.0) >= quote.get("limit_up", 999999),
                "is_limit_down": quote.get("price", 0.0) <= quote.get("limit_down", -1),
            }
        except Exception as exc:
            return self._fallback(f"live current price failed, fallback to demo: {type(exc).__name__}: {exc}", lambda: demo_current_price(code))

    def get_intraday_bars(self, code: str) -> list[dict]:
        if self.mode == "demo":
            return demo_intraday_bars(code)
        try:
            rows = self._eastmoney_intraday_trends(str(code).zfill(6))
            self.quality_report.record_source("eastmoney_intraday_trends", True, len(rows))
            return rows
        except Exception as exc:
            self.quality_report.record_source("eastmoney_intraday_trends", False, 0, f"{code}: {exc}")
            self._note(f"live intraday trends failed: {type(exc).__name__}: {exc}")
            return []

    def get_cached_list_date(self, code: str) -> dict | None:
        cache = self._load_list_date_cache()
        item = cache.get(str(code).zfill(6))
        if not item or not item.get("list_date"):
            return None
        return item

    def update_list_date_cache(self, code: str, list_date: str, source: str) -> bool:
        parsed = self._parse_list_date(list_date)
        if parsed is None:
            return False
        cache = self._load_list_date_cache()
        cache[str(code).zfill(6)] = {
            "list_date": parsed.isoformat(),
            "source": source,
            "updated_at": self.now.date().isoformat(),
        }
        self._save_list_date_cache(cache)
        return True

    def _live_quotes_for_demo_universe(self) -> list[dict]:
        demo_rows = demo_quotes()
        by_code = {row["code"]: row for row in demo_rows}
        live_quotes = self._tencent_quotes(list(by_code))
        rows = []
        for code, quote in live_quotes.items():
            base = dict(by_code.get(code, {}))
            if not base:
                continue
            base.update(quote)
            rows.append(base)
        if len(rows) < len(demo_rows):
            raise RuntimeError(f"incomplete live quotes for MVP universe: {len(rows)}/{len(demo_rows)}")
        return rows

    def _fetch_live_candidate_seeds(self) -> list[dict]:
        hot_candidates: list[dict] = []
        full_market_candidates: list[dict] = []
        try:
            full_market_candidates = self._eastmoney_clist_candidates()
            self.quality_report.record_source("eastmoney_clist", True, len(full_market_candidates))
        except Exception as exc:
            self.quality_report.record_source("eastmoney_clist", False, 0, str(exc))
            try:
                full_market_candidates = self._sina_money_flow_candidate_seeds()
                self.quality_report.record_source("sina_money_flow_candidate_seeds", True, len(full_market_candidates))
            except Exception as fallback_exc:
                self.quality_report.record_source("sina_money_flow_candidate_seeds", False, 0, str(fallback_exc))
        try:
            hot_candidates = self._ths_hot_reason_candidates()
        except Exception as exc:
            self.quality_report.record_source("ths_hot_reason", False, 0, str(exc))

        tradeable = [
            self._mark_missing_theme_allowed(seed)
            for seed in full_market_candidates
            if self._live_tradeable_candidate_seed_passes(seed)
        ]
        if tradeable or hot_candidates:
            seeds = self._dedupe_candidate_seeds(tradeable + hot_candidates)
            if len(seeds) < LIVE_SCAN_DEFAULT_MAX_CANDIDATES:
                filler = [
                    self._mark_missing_theme_allowed(seed)
                    for seed in full_market_candidates
                    if self._live_broad_candidate_seed_passes(seed)
                ]
                seeds = self._dedupe_candidate_seeds(seeds + filler)
            return seeds
        return full_market_candidates

    def _build_live_candidates(self, seeds: list[dict], limit: int | None = LIVE_SCAN_DEFAULT_MAX_CANDIDATES) -> list[dict]:
        raw_count = len(seeds)
        limited = seeds if limit is None else seeds[:limit]
        codes = [str(seed.get("code", "")).zfill(6) for seed in limited if seed.get("code")]
        quote_map = self._preferred_quote_map(codes, "tencent_quote")
        meta_codes = [code for code in codes if not self.get_cached_list_date(code)]
        meta_map = self._safe_quote_meta(meta_codes) if meta_codes else {}
        rows: list[dict] = []
        dropped = 0
        for seed in limited:
            code = str(seed.get("code", "")).zfill(6)
            if not code:
                dropped += 1
                continue
            merged = normalize_live_stock(seed, "candidate_seed")
            quote = quote_map.get(code) or {}
            if not quote:
                merged["_quote_missing"] = True
            merged = self._merge_standard_fields(merged, quote, "tencent_quote")
            merged = self._merge_list_date_from_seed_or_cache(merged)
            if not merged.get("list_date"):
                merged = self._merge_quote_meta(merged, meta_map.get(code) or {})
            if not merged.get("list_date"):
                info = self._safe_stock_info(code)
                merged = self._merge_stock_info(merged, info)
            merged = self._ensure_list_date_status(merged, code)
            if self.data_context == DATA_CONTEXT_PREVIOUS_CLOSE_REPLAY:
                fund_rows, fund_source, fund_error = self._safe_replay_fund_flow(code)
            else:
                fund_rows, fund_source, fund_error = self._safe_fund_flow(code)
            merged = self._merge_fund_flow(merged, fund_rows, fund_source, fund_error)
            merged = self._derive_live_safety_and_shape(merged)
            merged = self._derive_freshness(merged)
            merged = validate_stock_fields(normalize_live_stock(merged, "live"))
            if not merged.get("code") or not merged.get("price"):
                dropped += 1
                continue
            rows.append(merged)
        self.quality_report.record_candidates(raw_count, rows, dropped)
        return rows

    def _build_after_close_candidates(self, seeds: list[dict]) -> list[dict]:
        raw_count = len(seeds)
        codes = [str(seed.get("code", "")).zfill(6) for seed in seeds if seed.get("code")]
        quote_map = self._preferred_quote_map(codes, "tencent_quote_after_close", batched=True)
        meta_codes = [code for code in codes if not self.get_cached_list_date(code)]
        meta_map = self._safe_quote_meta(meta_codes) if meta_codes else {}
        rows: list[dict] = []
        dropped = 0
        for seed in seeds:
            code = str(seed.get("code", "")).zfill(6)
            if not code:
                dropped += 1
                continue
            merged = normalize_live_stock(seed, "after_close_universe_seed")
            quote = quote_map.get(code) or {}
            if not quote:
                merged["_quote_missing"] = True
            merged = self._merge_standard_fields(merged, quote, "tencent_quote")
            merged = self._merge_list_date_from_seed_or_cache(merged)
            if not merged.get("list_date"):
                merged = self._merge_quote_meta(merged, meta_map.get(code) or {})
            merged = self._ensure_list_date_status(merged, code)
            if self.data_context == DATA_CONTEXT_PREVIOUS_CLOSE_REPLAY:
                fund_rows, fund_source, fund_error = self._safe_replay_fund_flow(code)
            else:
                fund_rows, fund_source, fund_error = self._safe_fund_flow(code)
            merged = self._merge_fund_flow(merged, fund_rows, fund_source, fund_error)
            if not merged.get("theme_tags") or merged.get("theme_block_change_pct") in (None, ""):
                merged = self._merge_baidu_theme_fallback(merged, self._safe_baidu_concept_blocks(code))
            merged = self._merge_industry_theme_fallback(merged)
            merged = self._apply_recent_theme_trend(merged)
            merged = self._derive_live_safety_and_shape(merged)
            merged = self._derive_freshness(merged)
            merged = validate_stock_fields(normalize_live_stock(merged, "live"))
            if not merged.get("code") or not merged.get("price"):
                dropped += 1
                continue
            rows.append(merged)
        self.quality_report.record_candidates(raw_count, rows, dropped)
        return rows

    def _merge_after_close_hot_themes(self, seeds: list[dict]) -> list[dict]:
        if not seeds:
            return seeds
        context = self._recent_hot_theme_context()
        by_code = context.get("by_code", {})
        if not by_code and not context.get("themes"):
            return seeds
        return [
            self._apply_recent_theme_trend(
                self._merge_theme_fields(seed, by_code.get(str(seed.get("code", "")).zfill(6)))
            )
            for seed in seeds
        ]

    @staticmethod
    def _merge_theme_fields(seed: dict, theme_row: dict | None) -> dict:
        if not theme_row:
            return seed
        row = dict(seed)
        sources = dict(row.get("_sources") or {})
        theme_sources = dict(theme_row.get("_sources") or {})
        for field in ("theme_tags", "theme_rank", "same_theme_strong_count"):
            value = theme_row.get(field)
            if value not in (None, "", []):
                row[field] = value
                sources[field] = theme_sources.get(field, "ths_hot_reason")
        row["theme_source"] = "ths_hot_reason"
        sources["theme_source"] = "ths_hot_reason"
        row["_sources"] = sources
        return row

    def _recent_hot_theme_context(self, days: int = 5) -> dict:
        if self._recent_hot_theme_context_cache is not None:
            return self._recent_hot_theme_context_cache
        query_dates = self._recent_theme_query_dates(days)
        by_code: dict[str, dict] = {}
        themes: dict[str, dict] = {}
        total_rows = 0
        errors: list[str] = []
        for query_date in query_dates:
            try:
                rows = self._ths_hot_reason_candidates_for_date(query_date, record_source=False)
            except Exception as exc:
                errors.append(f"{query_date}:{exc}")
                continue
            total_rows += len(rows)
            for row in rows:
                code = str(row.get("code", "")).zfill(6)
                if code and code not in by_code and row.get("theme_tags"):
                    by_code[code] = row
                for tag in row.get("theme_tags") or []:
                    item = themes.setdefault(tag, {"dates": set(), "count": 0, "latest_date": "", "first_date": ""})
                    item["dates"].add(query_date)
                    item["count"] += 1
                    if not item["latest_date"] or query_date > item["latest_date"]:
                        item["latest_date"] = query_date
                    if not item["first_date"] or query_date < item["first_date"]:
                        item["first_date"] = query_date
        normalized_themes = {
            tag: {
                "active_days": len(item["dates"]),
                "count": item["count"],
                "latest_date": item["latest_date"],
                "first_date": item["first_date"],
            }
            for tag, item in themes.items()
        }
        if total_rows:
            self.quality_report.record_source("ths_hot_reason_recent", True, total_rows)
        elif errors:
            self.quality_report.record_source("ths_hot_reason_recent", False, 0, "; ".join(errors[:3]))
        self._recent_hot_theme_context_cache = {
            "by_code": by_code,
            "themes": normalized_themes,
            "latest_date": query_dates[0] if query_dates else "",
        }
        return self._recent_hot_theme_context_cache

    def _recent_theme_query_dates(self, days: int) -> list[str]:
        if self.data_context == DATA_CONTEXT_PREVIOUS_CLOSE_REPLAY:
            end_date = self._parse_list_date(self.expected_data_date) or self.now.date()
        else:
            end_date = self.now.date()
        return [(end_date - timedelta(days=offset)).isoformat() for offset in range(days)]

    def _apply_recent_theme_trend(self, stock: dict) -> dict:
        context = self._recent_hot_theme_context_cache or {}
        themes = context.get("themes") or {}
        tags = stock.get("theme_tags") or []
        if not tags or not themes:
            return stock
        matched = [themes[tag] for tag in tags if tag in themes]
        if not matched:
            stock.setdefault("theme_rotation_state", "unconfirmed")
            stock.setdefault("theme_active_days", 0)
            return stock
        best = max(matched, key=lambda item: (int(item.get("active_days", 0)), int(item.get("count", 0))))
        active_days = int(best.get("active_days", 0) or 0)
        stock["theme_active_days"] = active_days
        stock["theme_recent_count"] = int(best.get("count", 0) or 0)
        stock["theme_latest_date"] = best.get("latest_date", "")
        if active_days >= 3:
            stock["theme_rotation_state"] = "mainline"
        elif active_days >= 2:
            stock["theme_rotation_state"] = "continuing"
        elif best.get("latest_date") == context.get("latest_date"):
            stock["theme_rotation_state"] = "new_or_one_day"
        else:
            stock["theme_rotation_state"] = "fading"
        return stock

    def _after_close_universe_seeds(self) -> tuple[str, list[dict]]:
        errors: list[str] = []
        try:
            seeds = self._eastmoney_after_close_universe_seeds()
            if seeds:
                self._record_source_if_missing("eastmoney_after_close_universe", True, len(seeds))
                return "eastmoney_after_close_universe", seeds
            errors.append("eastmoney_after_close_universe: empty")
        except Exception as exc:
            self.quality_report.record_source("eastmoney_after_close_universe", False, 0, str(exc))
            errors.append(f"eastmoney_after_close_universe: {type(exc).__name__}: {exc}")
        try:
            seeds = self._easyquotation_after_close_universe_seeds()
            if seeds:
                self._record_source_if_missing("easyquotation_sina_full_market", True, len(seeds))
                return "easyquotation_sina_full_market", seeds
            errors.append("easyquotation_sina_full_market: empty")
        except Exception as exc:
            self.quality_report.record_source("easyquotation_sina_full_market", False, 0, str(exc))
            errors.append(f"easyquotation_sina_full_market: {type(exc).__name__}: {exc}")
        raise RuntimeError("; ".join(errors) if errors else "after-close universe sources returned empty")

    def _record_source_if_missing(self, source: str, ok: bool, count: int, error: str = "") -> None:
        for item in self.quality_report.source_status:
            if item.get("source") == source and bool(item.get("ok")) == ok:
                return
        self.quality_report.record_source(source, ok, count, error)

    def _ths_hot_reason_candidates(self) -> list[dict]:
        query_dates = (
            [self.expected_data_date]
            if self.data_context == DATA_CONTEXT_PREVIOUS_CLOSE_REPLAY
            else [(self.now.date() - timedelta(days=offset)).isoformat() for offset in range(0, 12)]
        )
        for query_date in query_dates:
            candidates = self._ths_hot_reason_candidates_for_date(query_date, record_source=True)
            if candidates:
                return candidates
        return []

    def _ths_hot_reason_candidates_for_date(self, query_date: str, record_source: bool = True) -> list[dict]:
        url = (
            "http://zx.10jqka.com.cn/event/api/getharden/"
            f"date/{query_date}/orderby/date/orderway/desc/charset/GBK/"
        )
        data = self._get_json(url, headers={"User-Agent": UA}, timeout=10)
        if data.get("errocode", 0) != 0:
            return []
        rows = data.get("data") or []
        candidates = []
        for row in rows:
            reason = str(row.get("reason") or "")
            tags = [part.strip() for part in reason.split("+") if part.strip()]
            code = str(row.get("code", "")).zfill(6)
            candidates.append(
                normalize_live_stock(
                    {
                        "code": code,
                        "name": row.get("name", ""),
                        "price": self._as_float(row.get("close"), None),
                        "change_pct": self._as_float(row.get("zhangfu"), None),
                        "turnover_pct": self._as_float(row.get("huanshou"), None),
                        "amount_wan": self._amount_to_wan(row.get("chengjiaoe")),
                        "theme_tags": tags,
                        "theme_rank": 1 if tags else None,
                        "same_theme_strong_count": 2 if tags else 0,
                        "big_order_net": self._as_float(row.get("ddejingliang"), None),
                        "_sources": {
                            "code": "ths_hot_reason.code",
                            "name": "ths_hot_reason.name",
                            "price": "ths_hot_reason.close",
                            "change_pct": "ths_hot_reason.zhangfu",
                            "turnover_pct": "ths_hot_reason.huanshou",
                            "amount_wan": "ths_hot_reason.chengjiaoe",
                            "theme_tags": "ths_hot_reason.reason",
                            "theme_rank": "ths_hot_reason.reason",
                            "same_theme_strong_count": "ths_hot_reason.reason",
                            "big_order_net": "ths_hot_reason.ddejingliang",
                        },
                        "_freshness": {
                            "ths_hot_reason": self._freshness_for_date(
                                "ths_hot_reason",
                                query_date,
                                "",
                                stale_reason_if_today="non_trading_day_cache_possible"
                                if not is_likely_cn_trade_day(self.now)
                                else "",
                            )
                        },
                    },
                    "ths_hot_reason",
                )
            )
        if record_source and candidates:
            self.quality_report.record_source(
                "ths_hot_reason",
                True,
                len(candidates),
                freshness=candidates[0].get("_freshness", {}).get("ths_hot_reason") if candidates else None,
            )
        return candidates

    def _eastmoney_clist_candidates(self) -> list[dict]:
        rows = []
        for page in range(1, EASTMONEY_CLIST_MAX_PAGES + 1):
            data = self._get_json(
                "https://push2.eastmoney.com/api/qt/clist/get",
                params={
                    "pn": str(page),
                    "pz": str(EASTMONEY_CLIST_PAGE_SIZE),
                    "po": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
                    "fields": "f2,f3,f6,f8,f10,f12,f14,f15,f16,f17,f21,f23,f62,f66,f72,f124",
                },
                headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
                timeout=15,
            )
            page_rows = (data.get("data") or {}).get("diff") or []
            rows.extend(page_rows)
            if len(page_rows) < EASTMONEY_CLIST_PAGE_SIZE:
                break
        result = []
        for item in rows:
            result.append(
                normalize_live_stock(
                    {
                        "code": str(item.get("f12", "")).zfill(6),
                        "name": item.get("f14", ""),
                        "price": self._as_float(item.get("f2"), None),
                        "change_pct": self._as_float(item.get("f3"), None),
                        "amount_wan": self._as_float(item.get("f6"), None, scale=1 / 10000),
                        "turnover_pct": self._as_float(item.get("f8"), None),
                        "vol_ratio": self._as_float(item.get("f10"), None),
                        "high": self._as_float(item.get("f15"), None),
                        "low": self._as_float(item.get("f16"), None),
                        "open": self._as_float(item.get("f17"), None),
                        "float_mcap_yi": self._as_float(item.get("f21"), None, scale=1 / 100000000),
                        "main_net": self._as_float(item.get("f62"), None),
                        "big_order_net": self._as_float(item.get("f72"), None),
                        "super_order_net": self._as_float(item.get("f66"), None),
                        "fund_flow_source": "eastmoney_clist",
                        "_freshness": {
                            "eastmoney_clist": self._freshness_from_eastmoney_timestamp(item.get("f124"))
                        },
                        "_sources": {
                            "code": "eastmoney_clist.f12",
                            "name": "eastmoney_clist.f14",
                            "price": "eastmoney_clist.f2",
                            "change_pct": "eastmoney_clist.f3",
                            "amount_wan": "eastmoney_clist.f6",
                            "turnover_pct": "eastmoney_clist.f8",
                            "vol_ratio": "eastmoney_clist.f10",
                            "high": "eastmoney_clist.f15",
                            "low": "eastmoney_clist.f16",
                            "open": "eastmoney_clist.f17",
                            "float_mcap_yi": "eastmoney_clist.f21",
                            "main_net": "eastmoney_clist.f62",
                            "big_order_net": "eastmoney_clist.f72",
                            "super_order_net": "eastmoney_clist.f66",
                            "fund_flow_source": "eastmoney_clist",
                        },
                    },
                    "eastmoney_clist",
                )
            )
        return result

    def _sina_money_flow_candidate_seeds(self, limit: int = LIVE_SCAN_DEFAULT_MAX_CANDIDATES * 3) -> list[dict]:
        self._sina_money_flow_current([])
        cache = self._sina_money_flow_current_cache or {}
        ranked = sorted(
            cache.items(),
            key=lambda item: (
                self._as_float(item[1].get("main_net"), 0.0),
                self._as_float(item[1].get("large_net"), 0.0),
            ),
            reverse=True,
        )
        result = []
        for code, flow in ranked:
            if not self._is_allowed_after_close_code(code):
                continue
            result.append(
                normalize_live_stock(
                    {
                        "code": code,
                        "name": flow.get("name", ""),
                        "main_net": flow.get("main_net"),
                        "big_order_net": flow.get("large_net"),
                        "fund_flow_source": "sina_money_flow_current",
                        "_quote_deferred": True,
                        "_sources": {
                            "code": "sina_money_flow_current.symbol",
                            "name": "sina_money_flow_current.name",
                            "main_net": "sina_money_flow_current.r0_net",
                            "big_order_net": "sina_money_flow_current.netamount",
                            "fund_flow_source": "sina_money_flow_current",
                        },
                    },
                    "sina_money_flow_current",
                )
            )
            if len(result) >= limit:
                break
        return result

    def _live_tradeable_candidate_seed_passes(self, stock: dict) -> bool:
        if not self._is_allowed_after_close_code(stock.get("code", "")):
            return False
        name = str(stock.get("name", ""))
        if self._is_st_or_delisting_name(name):
            return False
        if stock.get("_quote_deferred"):
            return True
        price = self._as_float(stock.get("price"), 0.0)
        change = self._as_float(stock.get("change_pct"), 0.0)
        amount = self._as_float(stock.get("amount_wan"), 0.0)
        turnover = self._as_float(stock.get("turnover_pct"), 0.0)
        ratio = self._as_float(stock.get("vol_ratio"), 0.0)
        return (
            3 <= price <= 80
            and 3 <= change <= 7
            and amount >= 15000
            and 3 <= turnover <= 18
            and ratio >= 1
        )

    def _live_broad_candidate_seed_passes(self, stock: dict) -> bool:
        if not self._is_allowed_after_close_code(stock.get("code", "")):
            return False
        name = str(stock.get("name", ""))
        if self._is_st_or_delisting_name(name):
            return False
        price = self._as_float(stock.get("price"), 0.0)
        change = self._as_float(stock.get("change_pct"), 0.0)
        amount = self._as_float(stock.get("amount_wan"), 0.0)
        turnover = self._as_float(stock.get("turnover_pct"), 0.0)
        ratio = self._as_float(stock.get("vol_ratio"), 0.0)
        return 3 <= price <= 80 and 0 <= change < 9.7 and amount >= 15000 and turnover >= 3 and ratio >= 0.8

    @staticmethod
    def _mark_missing_theme_allowed(seed: dict) -> dict:
        row = dict(seed)
        if not row.get("theme_tags"):
            row["_allow_missing_theme"] = True
        return row

    @staticmethod
    def _dedupe_candidate_seeds(seeds: list[dict]) -> list[dict]:
        result: list[dict] = []
        seen: set[str] = set()
        for seed in seeds:
            code = str(seed.get("code", "")).zfill(6)
            if not code or code in seen:
                continue
            seen.add(code)
            result.append(seed)
        return result

    def _eastmoney_after_close_universe_seeds(self) -> list[dict]:
        data = self._get_json(
            "https://push2.eastmoney.com/api/qt/clist/get",
            params={
                "pn": "1",
                "pz": "5000",
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fs": "m:0+t:6,m:1+t:2",
                "fields": "f2,f3,f6,f8,f10,f12,f14,f15,f16,f17,f21,f62,f66,f72,f124",
            },
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
            timeout=20,
        )
        rows = (data.get("data") or {}).get("diff") or []
        result = []
        for item in rows:
            code = str(item.get("f12", "")).zfill(6)
            if not self._is_allowed_after_close_code(code):
                continue
            price = self._as_float(item.get("f2"), None)
            high = self._as_float(item.get("f15"), None)
            low = self._as_float(item.get("f16"), None)
            amount_wan = self._as_float(item.get("f6"), None, scale=1 / 10000)
            seed = normalize_live_stock(
                {
                    "code": code,
                    "name": item.get("f14", ""),
                    "price": price,
                    "change_pct": self._as_float(item.get("f3"), None),
                    "amount_wan": amount_wan,
                    "turnover_pct": self._as_float(item.get("f8"), None),
                    "vol_ratio": self._as_float(item.get("f10"), None),
                    "high": high,
                    "low": low,
                    "open": self._as_float(item.get("f17"), None),
                    "float_mcap_yi": self._as_float(item.get("f21"), None, scale=1 / 100000000),
                    "main_net": self._as_float(item.get("f62"), None),
                    "big_order_net": self._as_float(item.get("f72"), None),
                    "super_order_net": self._as_float(item.get("f66"), None),
                    "fund_flow_source": "eastmoney_after_close_universe",
                    "_sources": {
                        "code": "eastmoney_after_close_universe.f12",
                        "name": "eastmoney_after_close_universe.f14",
                        "price": "eastmoney_after_close_universe.f2",
                        "change_pct": "eastmoney_after_close_universe.f3",
                        "amount_wan": "eastmoney_after_close_universe.f6",
                        "turnover_pct": "eastmoney_after_close_universe.f8",
                        "vol_ratio": "eastmoney_after_close_universe.f10",
                        "high": "eastmoney_after_close_universe.f15",
                        "low": "eastmoney_after_close_universe.f16",
                        "open": "eastmoney_after_close_universe.f17",
                        "float_mcap_yi": "eastmoney_after_close_universe.f21",
                        "main_net": "eastmoney_after_close_universe.f62",
                        "big_order_net": "eastmoney_after_close_universe.f72",
                        "super_order_net": "eastmoney_after_close_universe.f66",
                        "fund_flow_source": "eastmoney_after_close_universe",
                    },
                    "_freshness": {
                        "eastmoney_after_close_universe": self._freshness_from_eastmoney_timestamp(item.get("f124"))
                    },
                },
                "eastmoney_after_close_universe",
            )
            result.append(seed)
        self.quality_report.record_source("eastmoney_after_close_universe", True, len(result))
        return result

    def _easyquotation_after_close_universe_seeds(self) -> list[dict]:
        try:
            import easyquotation  # type: ignore
        except ImportError as exc:
            raise RuntimeError("easyquotation_not_installed") from exc

        quotation = easyquotation.use("sina")
        snapshot = quotation.market_snapshot(prefix=True)
        result = []
        for raw_code, item in (snapshot or {}).items():
            if not isinstance(item, dict):
                continue
            code = self._normalize_prefixed_code(raw_code) or self._normalize_prefixed_code(item.get("code", ""))
            if not self._is_allowed_after_close_code(code):
                continue
            name = str(item.get("name") or item.get("名称") or "")
            price = self._first_float(item, ("now", "price", "current", "最新价", "现价"))
            previous_close = self._first_float(item, ("close", "last_close", "昨收"))
            change_pct = self._first_float(item, ("change_pct", "涨跌幅"), None)
            if change_pct is None and price and previous_close:
                change_pct = (price - previous_close) / previous_close * 100
            seed = normalize_live_stock(
                {
                    "code": code,
                    "name": name,
                    "price": price,
                    "change_pct": change_pct,
                    "amount_wan": self._easyquotation_amount_wan(item),
                    "high": self._first_float(item, ("high", "最高")),
                    "low": self._first_float(item, ("low", "最低")),
                    "open": self._first_float(item, ("open", "今开")),
                    "_sources": {
                        "code": "easyquotation_sina_full_market.symbol",
                        "name": "easyquotation_sina_full_market.name",
                        "price": "easyquotation_sina_full_market.now",
                        "change_pct": "easyquotation_sina_full_market.computed_from_now_close",
                        "amount_wan": "easyquotation_sina_full_market.turnover",
                        "high": "easyquotation_sina_full_market.high",
                        "low": "easyquotation_sina_full_market.low",
                        "open": "easyquotation_sina_full_market.open",
                    },
                    "_freshness": {
                        "easyquotation_sina_full_market": self._freshness_from_easyquotation(item),
                    },
                },
                "easyquotation_sina_full_market",
            )
            result.append(seed)
        self.quality_report.record_source("easyquotation_sina_full_market", True, len(result))
        return result

    def _after_close_base_candidate_passes(self, stock: dict) -> bool:
        if not self._is_allowed_after_close_code(stock.get("code", "")):
            return False
        name = str(stock.get("name", ""))
        if self._is_st_or_delisting_name(name):
            return False
        price = self._as_float(stock.get("price"), 0.0)
        change = self._as_float(stock.get("change_pct"), 0.0)
        amount = self._as_float(stock.get("amount_wan"), 0.0)
        turnover = self._as_float(stock.get("turnover_pct"), 0.0)
        ratio = self._as_float(stock.get("vol_ratio"), 0.0)
        if price < 3:
            return False
        if not (3 <= change <= 10):
            return False
        if amount < 15000:
            return False
        if turnover < 3:
            return False
        if ratio < 1:
            return False
        if change >= 9.8:
            return False
        return True

    def _after_close_prefilter_candidate_passes(self, stock: dict) -> bool:
        if not self._is_allowed_after_close_code(stock.get("code", "")):
            return False
        name = str(stock.get("name", ""))
        if self._is_st_or_delisting_name(name):
            return False
        price = self._as_float(stock.get("price"), 0.0)
        change = self._as_float(stock.get("change_pct"), 0.0)
        amount = self._as_float(stock.get("amount_wan"), 0.0)
        return price >= 3 and 3 <= change < 9.8 and amount >= 15000

    @staticmethod
    def _is_allowed_after_close_code(code: str) -> bool:
        normalized = str(code or "").zfill(6)
        return normalized.startswith(("00", "60"))

    @staticmethod
    def _normalize_prefixed_code(value) -> str:
        text = str(value or "").strip().lower()
        if text.startswith(("sh", "sz", "bj")):
            text = text[2:]
        if "." in text:
            text = text.split(".", 1)[0]
        return text.zfill(6) if text.isdigit() else ""

    def _tencent_quotes_batched(self, codes: list[str], batch_size: int = 80) -> dict[str, dict]:
        result: dict[str, dict] = {}
        unique_codes = []
        seen = set()
        for code in codes:
            normalized = str(code).zfill(6)
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique_codes.append(normalized)
        for index in range(0, len(unique_codes), batch_size):
            batch = unique_codes[index : index + batch_size]
            if batch:
                result.update(self._tencent_quotes(batch))
        return result

    def _preferred_quote_map(self, codes: list[str], source_name: str, batched: bool = False) -> dict[str, dict]:
        normalized_codes = [str(code).zfill(6) for code in codes if str(code).strip()]
        if not normalized_codes:
            return {}
        result: dict[str, dict] = {}
        try:
            result = self._tencent_quotes_batched(normalized_codes) if batched else self._tencent_quotes(normalized_codes)
            self.quality_report.record_source(source_name, True, len(result))
        except Exception as exc:
            self.quality_report.record_source(source_name, False, 0, str(exc))
            result = {}
        missing = [code for code in normalized_codes if code not in result]
        if missing:
            try:
                fallback = self._mootdx_quotes(missing)
                self.quality_report.record_source("mootdx_quote_fallback", True, len(fallback))
                for code, quote in fallback.items():
                    result.setdefault(code, quote)
            except Exception as exc:
                self.quality_report.record_source("mootdx_quote_fallback", False, 0, str(exc))
        return result

    def _tencent_quotes(self, codes: list[str]) -> dict[str, dict]:
        prefixed = []
        for code in codes:
            raw_code = str(code).lower()
            if raw_code.startswith(("sh", "sz", "bj")):
                prefixed.append(raw_code)
            elif raw_code.startswith(("6", "9")):
                prefixed.append(f"sh{raw_code}")
            elif raw_code.startswith(("8", "4")):
                prefixed.append(f"bj{raw_code}")
            else:
                prefixed.append(f"sz{raw_code}")
        url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
        req = urllib.request.Request(url)
        req.add_header("User-Agent", UA)
        data = urllib.request.urlopen(req, timeout=8).read().decode("gbk", errors="ignore")
        result: dict[str, dict] = {}
        for line in data.strip().split(";"):
            parsed = self._parse_tencent_line(line)
            if parsed:
                code, quote = parsed
                result[code] = quote
        return result

    @staticmethod
    def _parse_tencent_line(line: str) -> tuple[str, dict] | None:
        if not line.strip() or "=" not in line or '"' not in line:
            return None
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            return None
        code = key[2:]

        def as_float(value: str) -> float:
            try:
                return float(value) if value else 0.0
            except ValueError:
                return 0.0

        return code, {
            "code": code,
            "name": vals[1],
            "price": as_float(vals[3]),
            "last_close": as_float(vals[4]),
            "open": as_float(vals[5]),
            "change_pct": as_float(vals[32]),
            "high": as_float(vals[33]),
            "low": as_float(vals[34]),
            "amount_wan": as_float(vals[37]),
            "volume": as_float(vals[36]) if len(vals) > 36 else 0.0,
            "turnover_pct": as_float(vals[38]),
            "float_mcap_yi": as_float(vals[45]),
            "limit_up": as_float(vals[47]),
            "limit_down": as_float(vals[48]),
            "vol_ratio": as_float(vals[49]),
            "_sources": {
                "code": "tencent_quote.symbol",
                "name": "tencent_quote.vals[1]",
                "price": "tencent_quote.vals[3]",
                "open": "tencent_quote.vals[5]",
                "change_pct": "tencent_quote.vals[32]",
                "high": "tencent_quote.vals[33]",
                "low": "tencent_quote.vals[34]",
                "amount_wan": "tencent_quote.vals[37]",
                "volume": "tencent_quote.vals[36]",
                "turnover_pct": "tencent_quote.vals[38]",
                "float_mcap_yi": "tencent_quote.vals[45]",
                "limit_up": "tencent_quote.vals[47]",
                "limit_down": "tencent_quote.vals[48]",
                "vol_ratio": "tencent_quote.vals[49]",
            },
            "_freshness": {
                "tencent_quote": AStockClient._freshness_from_tencent(vals[30] if len(vals) > 30 else "")
            },
        }

    def _mootdx_quotes(self, codes: list[str]) -> dict[str, dict]:
        from mootdx.quotes import Quotes  # type: ignore

        normalized = [str(code).lower().removeprefix("sh").removeprefix("sz").removeprefix("bj").zfill(6) for code in codes]
        client = Quotes.factory(market="std")
        frame = client.quotes(symbol=normalized)
        if frame is None or getattr(frame, "empty", False):
            return {}
        result: dict[str, dict] = {}
        for _, item in frame.iterrows():
            code = str(item.get("code", "")).zfill(6)
            price = self._as_float(item.get("price"), 0.0)
            last_close = self._as_float(item.get("last_close"), 0.0)
            amount = self._as_float(item.get("amount"), 0.0)
            volume = self._as_float(item.get("vol", item.get("volume")), 0.0)
            change_pct = (price / last_close - 1) * 100 if price and last_close else 0.0
            servertime = str(item.get("servertime") or "")
            result[code] = {
                "code": code,
                "name": str(item.get("name") or ""),
                "price": price,
                "last_close": last_close,
                "open": self._as_float(item.get("open"), 0.0),
                "change_pct": change_pct,
                "high": self._as_float(item.get("high"), 0.0),
                "low": self._as_float(item.get("low"), 0.0),
                "amount_wan": amount / 10000 if amount else 0.0,
                "volume": volume,
                "_sources": {
                    "code": "mootdx_quote.code",
                    "price": "mootdx_quote.price",
                    "last_close": "mootdx_quote.last_close",
                    "open": "mootdx_quote.open",
                    "change_pct": "mootdx_quote.derived_from_price_last_close",
                    "high": "mootdx_quote.high",
                    "low": "mootdx_quote.low",
                    "amount_wan": "mootdx_quote.amount",
                    "volume": "mootdx_quote.vol",
                },
                "_freshness": {
                    "mootdx_quote": {
                        "source": "mootdx_quote",
                        "data_date": self.now.date().isoformat(),
                        "data_time": servertime,
                        "is_stale": False,
                        "stale_reason": "" if servertime else "timestamp_missing",
                    }
                },
            }
        return result

    def _eastmoney_stock_info(self, code: str) -> dict:
        market_code = 1 if code.startswith("6") else 0
        data = self._get_json(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={
                "fltt": "2",
                "invt": "2",
                "fields": "f57,f58,f84,f85,f127,f116,f117,f189,f43",
                "secid": f"{market_code}.{code}",
            },
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
            timeout=10,
        )
        return data.get("data") or {}

    def _eastmoney_fund_flow_minute(self, code: str) -> list[dict]:
        market_code = 1 if code.startswith("6") else 0
        data = self._get_json(
            "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get",
            params={
                "secid": f"{market_code}.{code}",
                "klt": 1,
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
            },
            headers={"User-Agent": UA},
            timeout=10,
        )
        rows = []
        for line in ((data.get("data") or {}).get("klines") or []):
            parts = line.split(",")
            if len(parts) >= 6:
                rows.append(
                    {
                        "time": parts[0],
                        "main_net": self._as_float(parts[1], 0.0),
                        "small_net": self._as_float(parts[2], 0.0),
                        "mid_net": self._as_float(parts[3], 0.0),
                        "large_net": self._as_float(parts[4], 0.0),
                        "super_net": self._as_float(parts[5], 0.0),
                    }
                )
        return rows

    def _parse_eastmoney_fund_flow_lines(self, lines: list[str]) -> list[dict]:
        rows = []
        for line in lines or []:
            parts = line.split(",")
            if len(parts) >= 6:
                rows.append(
                    {
                        "time": parts[0],
                        "main_net": self._as_float(parts[1], 0.0),
                        "small_net": self._as_float(parts[2], 0.0),
                        "mid_net": self._as_float(parts[3], 0.0),
                        "large_net": self._as_float(parts[4], 0.0),
                        "super_net": self._as_float(parts[5], 0.0),
                    }
                )
        return rows

    def _eastmoney_fund_flow_kline_daily(self, code: str, limit: int = 10) -> list[dict]:
        market_code = 1 if code.startswith("6") else 0
        data = self._get_json_resilient(
            "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get",
            params={
                "secid": f"{market_code}.{code}",
                "klt": 101,
                "lmt": max(1, int(limit)),
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
            },
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
            timeout=10,
        )
        return self._parse_eastmoney_fund_flow_lines(((data.get("data") or {}).get("klines") or []))

    def _eastmoney_intraday_trends(self, code: str) -> list[dict]:
        market_code = 1 if code.startswith("6") else 0
        data = self._get_json(
            "https://push2.eastmoney.com/api/qt/stock/trends2/get",
            params={
                "secid": f"{market_code}.{code}",
                "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                "iscr": "0",
                "iscca": "0",
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            },
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
            timeout=10,
        )
        rows = []
        for line in ((data.get("data") or {}).get("trends") or []):
            parts = line.split(",")
            if len(parts) >= 8:
                rows.append(
                    {
                        "time": parts[0],
                        "open": self._as_float(parts[1], 0.0),
                        "close": self._as_float(parts[2], 0.0),
                        "high": self._as_float(parts[3], 0.0),
                        "low": self._as_float(parts[4], 0.0),
                        "volume": self._as_float(parts[5], 0.0),
                        "amount": self._as_float(parts[6], 0.0),
                        "vwap": self._as_float(parts[7], 0.0),
                    }
                )
        if not rows:
            raise RuntimeError("empty intraday trends response")
        return rows

    def _eastmoney_fund_flow_daily(self, code: str, limit: int = 5) -> list[dict]:
        errors = []
        try:
            rows = self._eastmoney_fund_flow_kline_daily(code, limit=limit)
            if rows:
                return rows
            errors.append("kline_daily_empty")
        except Exception as exc:
            errors.append(f"kline_daily_failed:{exc}")
        market_code = 1 if code.startswith("6") else 0
        try:
            data = self._get_json_resilient(
                "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
                params={
                    "secid": f"{market_code}.{code}",
                    "lmt": max(1, int(limit)),
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                },
                headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
                timeout=10,
            )
            return self._parse_eastmoney_fund_flow_lines(((data.get("data") or {}).get("klines") or []))
        except Exception as exc:
            errors.append(f"daykline_failed:{exc}")
            raise RuntimeError("; ".join(errors)) from exc

    def _eastmoney_quote_fund_flow(self, codes: list[str]) -> dict[str, dict]:
        if not codes:
            return {}
        secids = []
        for raw_code in codes:
            code = str(raw_code).zfill(6)
            market_code = 1 if code.startswith("6") else 0
            secids.append(f"{market_code}.{code}")
        data = self._get_json(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            params={
                "fltt": "2",
                "invt": "2",
                "fields": "f12,f14,f62,f66,f72",
                "secids": ",".join(secids),
            },
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
            timeout=10,
        )
        result: dict[str, dict] = {}
        for item in ((data.get("data") or {}).get("diff") or []):
            code = str(item.get("f12", "")).zfill(6)
            if not code:
                continue
            main_net = self._as_float(item.get("f62"), None)
            super_net = self._as_float(item.get("f66"), None)
            large_net = self._as_float(item.get("f72"), None)
            if main_net is None and super_net is not None and large_net is not None:
                main_net = super_net + large_net
            if large_net is None and main_net is not None and super_net is not None:
                large_net = main_net - super_net
            if super_net is None and main_net is not None and large_net is not None:
                super_net = main_net - large_net
            if main_net is None and large_net is None:
                continue
            result[code] = {
                "time": "quote",
                "main_net": main_net,
                "large_net": large_net,
                "super_net": super_net,
                "_sources": {
                    "main_net": "eastmoney_quote_fund_flow.f62",
                    "large_net": "eastmoney_quote_fund_flow.f72",
                    "super_net": "eastmoney_quote_fund_flow.f66",
                },
            }
        return result

    def _sina_money_flow_current(self, codes: list[str]) -> dict[str, dict]:
        if self._sina_money_flow_current_cache is None:
            data = self._get_json(
                "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_ssggzj",
                params={
                    "page": 1,
                    "num": 6000,
                    "sort": "symbol",
                    "asc": 1,
                    "bankuai": "",
                    "shichang": "",
                },
                headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"},
                timeout=20,
            )
            rows = data if isinstance(data, list) else []
            cache: dict[str, dict] = {}
            for item in rows:
                symbol = str(item.get("symbol", ""))
                code = symbol[-6:].zfill(6)
                if not code.strip("0"):
                    continue
                net_amount = self._as_float(item.get("netamount"), None)
                r0_net = self._as_float(item.get("r0_net"), None)
                if net_amount is None and r0_net is None:
                    continue
                cache[code] = {
                    "time": "sina_current",
                    "name": item.get("name", ""),
                    "main_net": r0_net,
                    "large_net": net_amount,
                    "net_amount": net_amount,
                    "r0_net": r0_net,
                    "trade": self._as_float(item.get("trade"), None),
                    "_sources": {
                        "main_net": "sina_money_flow_current.r0_net",
                        "large_net": "sina_money_flow_current.netamount",
                        "net_amount": "sina_money_flow_current.netamount",
                    },
                }
            self._sina_money_flow_current_cache = cache
        wanted = {str(code).zfill(6) for code in codes}
        return {code: row for code, row in self._sina_money_flow_current_cache.items() if code in wanted}

    def _sina_money_flow_history(self, code: str) -> list[dict]:
        normalized = str(code).zfill(6)
        symbol = f"sh{normalized}" if normalized.startswith(("6", "9")) else f"sz{normalized}"
        data = self._get_json(
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_qsfx_zjlrqs",
            params={"daima": symbol},
            headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"},
            timeout=15,
        )
        rows = []
        for item in data if isinstance(data, list) else []:
            net_amount = self._as_float(item.get("netamount"), None)
            r0_net = self._as_float(item.get("r0_net"), None)
            if net_amount is None and r0_net is None:
                continue
            rows.append(
                {
                    "time": item.get("opendate", ""),
                    "main_net": r0_net,
                    "large_net": net_amount,
                    "net_amount": net_amount,
                    "r0_net": r0_net,
                    "_sources": {
                        "main_net": "sina_money_flow_history.r0_net",
                        "large_net": "sina_money_flow_history.netamount",
                        "net_amount": "sina_money_flow_history.netamount",
                    },
                }
            )
        return rows

    def _eastmoney_quote_meta(self, codes: list[str]) -> dict[str, dict]:
        if not codes:
            return {}
        secids = []
        for code in codes:
            market_code = 1 if str(code).startswith("6") else 0
            secids.append(f"{market_code}.{str(code).zfill(6)}")
        data = self._get_json(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            params={
                "fltt": "2",
                "invt": "2",
                "fields": "f12,f14,f26,f100",
                "secids": ",".join(secids),
            },
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
            timeout=10,
        )
        result: dict[str, dict] = {}
        for item in ((data.get("data") or {}).get("diff") or []):
            code = str(item.get("f12", "")).zfill(6)
            if code:
                result[code] = item
        return result

    def _hsgt_realtime(self) -> list[dict]:
        data = self._get_json(
            "https://data.hexin.cn/market/hsgtApi/method/dayChart/",
            headers={"User-Agent": UA, "Host": "data.hexin.cn", "Referer": "https://data.hexin.cn/"},
            timeout=10,
        )
        times = data.get("time") or []
        hgt = data.get("hgt") or []
        sgt = data.get("sgt") or []
        rows = []
        for idx, item_time in enumerate(times):
            rows.append(
                {
                    "time": item_time,
                    "hgt_yi": hgt[idx] if idx < len(hgt) else None,
                    "sgt_yi": sgt[idx] if idx < len(sgt) else None,
                }
            )
        return rows

    def _baidu_daily_kline(self, code: str, lookback: int) -> list[dict]:
        data = self._get_json(
            "https://finance.pae.baidu.com/selfselect/getstockquotation",
            params={
                "all": "1",
                "isIndex": "false",
                "isBk": "false",
                "isBlock": "false",
                "isFutures": "false",
                "isStock": "true",
                "newFormat": "1",
                "group": "quotation_kline_ab",
                "finClientType": "pc",
                "code": code,
                "ktype": "1",
            },
            headers={
                "User-Agent": UA,
                "Accept": "application/vnd.finance-web.v1+json",
                "Origin": "https://gushitong.baidu.com",
                "Referer": "https://gushitong.baidu.com/",
            },
            timeout=10,
        )
        md = ((data.get("Result") or {}).get("newMarketData") or {})
        keys = md.get("keys") or []
        raw_rows = (md.get("marketData") or "").split(";") if md.get("marketData") else []
        rows = []
        for line in raw_rows[-lookback:]:
            values = line.split(",")
            item = dict(zip(keys, values))
            rows.append(
                {
                    "date": item.get("time") or item.get("timestamp") or "",
                    "open": self._as_float(item.get("open"), 0.0),
                    "close": self._as_float(item.get("close"), 0.0),
                    "high": self._as_float(item.get("high"), 0.0),
                    "low": self._as_float(item.get("low"), 0.0),
                    "volume": self._as_float(item.get("volume"), 0.0),
                    "amount": self._as_float(item.get("amount"), 0.0),
                    "ma5": self._as_float(item.get("ma5avgprice"), None),
                    "ma10": self._as_float(item.get("ma10avgprice"), None),
                    "ma20": self._as_float(item.get("ma20avgprice"), None),
                }
            )
        self._fill_missing_mas(rows)
        return rows

    def _eastmoney_daily_kline(self, code: str, lookback: int) -> list[dict]:
        market_code = 1 if str(code).startswith("6") else 0
        data = self._get_json(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            params={
                "secid": f"{market_code}.{str(code).zfill(6)}",
                "klt": "101",
                "fqt": "1",
                "lmt": str(lookback),
                "end": "20500101",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            },
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
            timeout=10,
        )
        rows = []
        for line in ((data.get("data") or {}).get("klines") or [])[-lookback:]:
            parts = line.split(",")
            if len(parts) >= 7:
                rows.append(
                    {
                        "date": parts[0],
                        "open": self._as_float(parts[1], 0.0),
                        "close": self._as_float(parts[2], 0.0),
                        "high": self._as_float(parts[3], 0.0),
                        "low": self._as_float(parts[4], 0.0),
                        "volume": self._as_float(parts[5], 0.0),
                        "amount": self._as_float(parts[6], 0.0),
                    }
                )
        if not rows:
            raise RuntimeError("empty eastmoney daily kline response")
        self._fill_missing_mas(rows)
        return rows

    def _mootdx_daily_kline(self, code: str, lookback: int) -> list[dict]:
        from mootdx.quotes import Quotes  # type: ignore

        client = Quotes.factory(market="std")
        frame = client.bars(symbol=str(code).zfill(6), category=4, offset=lookback)
        if frame is None or getattr(frame, "empty", False):
            return []
        rows = []
        for _, item in frame.tail(lookback).iterrows():
            raw_date = str(item.get("datetime") or "")
            rows.append(
                {
                    "date": self._normalize_data_date(raw_date),
                    "open": self._as_float(item.get("open"), 0.0),
                    "close": self._as_float(item.get("close"), 0.0),
                    "high": self._as_float(item.get("high"), 0.0),
                    "low": self._as_float(item.get("low"), 0.0),
                    "volume": self._as_float(item.get("vol", item.get("volume")), 0.0),
                    "amount": self._as_float(item.get("amount"), 0.0),
                }
            )
        self._fill_missing_mas(rows)
        return rows

    def _safe_baidu_concept_blocks(self, code: str) -> dict:
        try:
            blocks = self._baidu_concept_blocks(code)
            concept_count = len(blocks.get("concept_tags") or [])
            industry_count = len(blocks.get("industry") or [])
            self.quality_report.record_source("baidu_concept_blocks", True, concept_count + industry_count)
            return blocks
        except Exception as exc:
            self.quality_report.record_source("baidu_concept_blocks", False, 0, f"{code}: {exc}")
            return {}

    def _baidu_concept_blocks(self, code: str) -> dict:
        data = self._get_json(
            "https://finance.pae.baidu.com/api/getrelatedblock",
            params={
                "code": str(code).zfill(6),
                "market": "ab",
                "typeCode": "all",
                "finClientType": "pc",
            },
            headers={
                "Host": "finance.pae.baidu.com",
                "User-Agent": UA,
                "Accept": "application/vnd.finance-web.v1+json",
                "Origin": "https://gushitong.baidu.com",
                "Referer": "https://gushitong.baidu.com/",
            },
            timeout=10,
        )
        if str(data.get("ResultCode", -1)) != "0":
            raise RuntimeError(f"baidu ResultCode={data.get('ResultCode')}")
        result = {"industry": [], "concept": [], "region": [], "concept_tags": []}
        for block in data.get("Result", []) or []:
            block_type = str(block.get("type", ""))
            for item in block.get("list", []) or []:
                entry = {
                    "name": item.get("name", ""),
                    "change_pct": item.get("increase", ""),
                    "desc": item.get("desc", ""),
                }
                if not entry["name"]:
                    continue
                if "行业" in block_type:
                    result["industry"].append(entry)
                elif "概念" in block_type:
                    result["concept"].append(entry)
                    result["concept_tags"].append(entry["name"])
                elif "地域" in block_type:
                    result["region"].append(entry)
        result["concept_tags"] = list(dict.fromkeys(result["concept_tags"]))
        return result

    def _eastmoney_core_conception_blocks(self, code: str, limit: int = 12) -> dict:
        normalized = str(code).zfill(6)
        if normalized.startswith(("6", "9")):
            em_code = f"SH{normalized}"
        elif normalized.startswith("8"):
            em_code = f"BJ{normalized}"
        else:
            em_code = f"SZ{normalized}"
        data = self._get_json_resilient(
            "https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax",
            params={"code": em_code},
            headers={"User-Agent": UA, "Referer": "https://emweb.securities.eastmoney.com/"},
            timeout=12,
        )
        boards = sorted(data.get("ssbk") or [], key=lambda item: int(item.get("BOARD_RANK") or 999))
        result = {"industry": [], "concept": [], "region": [], "concept_tags": []}
        for item in boards[: max(1, int(limit))]:
            name = str(item.get("BOARD_NAME") or "").strip()
            if not name:
                continue
            bk_code = self._eastmoney_bk_code(str(item.get("BOARD_CODE") or ""))
            quote = self._eastmoney_board_quote(bk_code) if bk_code else {}
            entry = {
                "name": name,
                "change_pct": quote.get("change_pct", ""),
                "board_code": bk_code,
                "desc": "eastmoney_core_conception",
                "error": quote.get("error", ""),
            }
            rank = int(item.get("BOARD_RANK") or 999)
            if rank <= 3:
                result["industry"].append(entry)
            else:
                result["concept"].append(entry)
            result["concept_tags"].append(name)
        result["concept_tags"] = list(dict.fromkeys(result["concept_tags"]))
        return result

    def _eastmoney_bk_code(self, board_code: str) -> str:
        digits = "".join(ch for ch in str(board_code) if ch.isdigit())
        if not digits:
            return ""
        return f"BK{int(digits):04d}"

    def _eastmoney_board_quote(self, bk_code: str) -> dict:
        if not bk_code:
            return {}
        if bk_code in self._eastmoney_board_quote_cache:
            return self._eastmoney_board_quote_cache[bk_code]
        try:
            data = self._get_json_resilient(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={"secid": f"90.{bk_code}", "fields": "f57,f58,f170,f62"},
                headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
                timeout=8,
                retries=1,
            )
            item = data.get("data") or {}
            result = {
                "board_code": item.get("f57") or bk_code,
                "name": item.get("f58", ""),
                "change_pct": self._as_float(item.get("f170"), None, scale=1 / 100),
                "main_net": self._as_float(item.get("f62"), None),
            }
        except Exception as exc:
            try:
                result = self._eastmoney_board_member_avg_quote(bk_code)
                if result:
                    result["error"] = f"board_quote_failed_used_member_avg:{exc}"
                else:
                    result = {"board_code": bk_code, "change_pct": "", "error": str(exc)}
            except Exception as fallback_exc:
                result = {"board_code": bk_code, "change_pct": "", "error": f"{exc}; member_avg_failed:{fallback_exc}"}
        self._eastmoney_board_quote_cache[bk_code] = result
        return result

    def _eastmoney_board_member_avg_quote(self, bk_code: str) -> dict:
        data = self._get_json_resilient(
            "https://push2.eastmoney.com/api/qt/clist/get",
            params={
                "pn": 1,
                "pz": 30,
                "po": 1,
                "np": 1,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": f"b:{bk_code}",
                "fields": "f12,f14,f3,f62",
            },
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
            timeout=10,
            retries=1,
        )
        rows = (data.get("data") or {}).get("diff") or []
        changes = [self._as_float(item.get("f3"), None) for item in rows]
        changes = [value for value in changes if value is not None]
        if not changes:
            return {}
        main_nets = [self._as_float(item.get("f62"), 0.0) for item in rows]
        return {
            "board_code": bk_code,
            "name": bk_code,
            "change_pct": round(sum(changes) / len(changes), 2),
            "main_net": sum(main_nets),
            "source": "eastmoney_board_members_avg",
        }

    def _safe_stock_info(self, code: str) -> dict:
        try:
            info = self._eastmoney_stock_info(code)
            self.quality_report.record_source("eastmoney_stock_info", True, 1 if info else 0)
            if not info:
                self._stock_info_errors[code] = "list_date_missing_not_found"
            return info
        except Exception as exc:
            self.quality_report.record_source("eastmoney_stock_info", False, 0, f"{code}: {exc}")
            self._stock_info_errors[code] = "list_date_missing_remote_failed"
            return {}

    def _safe_quote_meta(self, codes: list[str]) -> dict[str, dict]:
        try:
            rows = self._eastmoney_quote_meta(codes)
            self.quality_report.record_source("eastmoney_quote_meta", True, len(rows))
            return rows
        except Exception as exc:
            self.quality_report.record_source("eastmoney_quote_meta", False, 0, str(exc))
            return {}

    def _safe_quote_fund_flow(self, codes: list[str]) -> dict[str, dict]:
        try:
            rows = self._eastmoney_quote_fund_flow(codes)
            self.quality_report.record_source("eastmoney_quote_fund_flow", True, len(rows))
            return rows
        except Exception as exc:
            self.quality_report.record_source("eastmoney_quote_fund_flow", False, 0, str(exc))
            return {}

    def _safe_fund_flow(self, code: str) -> tuple[list[dict], str, str]:
        errors: list[str] = []
        try:
            rows = self._eastmoney_fund_flow_minute(code)
            if rows:
                self.quality_report.record_source("eastmoney_fund_flow_minute", True, len(rows))
                return rows, "eastmoney_fund_flow_minute", ""
            errors.append("minute_empty")
        except Exception as exc:
            self.quality_report.record_source("eastmoney_fund_flow_minute", False, 0, f"{code}: {exc}")
            errors.append(f"minute_failed:{exc}")
        quote_fund_flow = self._safe_quote_fund_flow([code]).get(str(code).zfill(6))
        if quote_fund_flow:
            return [quote_fund_flow], "eastmoney_quote_fund_flow", ""
        errors.append("quote_fund_flow_empty")
        try:
            sina_current = self._sina_money_flow_current([code]).get(str(code).zfill(6))
            if sina_current:
                self.quality_report.record_source("sina_money_flow_current", True, 1)
                return [sina_current], "sina_money_flow_current", ""
            errors.append("sina_current_empty")
        except Exception as exc:
            self.quality_report.record_source("sina_money_flow_current", False, 0, f"{code}: {exc}")
            errors.append(f"sina_current_failed:{exc}")
        try:
            rows = self._eastmoney_fund_flow_daily(code)
            if rows:
                self.quality_report.record_source("eastmoney_fund_flow_daily", True, len(rows))
                return rows, "eastmoney_fund_flow_daily", ""
            errors.append("daily_empty")
        except Exception as exc:
            self.quality_report.record_source("eastmoney_fund_flow_daily", False, 0, f"{code}: {exc}")
            errors.append(f"daily_failed:{exc}")
        try:
            rows = self._sina_money_flow_history(code)
            if rows:
                self.quality_report.record_source("sina_money_flow_history", True, len(rows))
                return rows, "sina_money_flow_history", ""
            errors.append("sina_history_empty")
        except Exception as exc:
            self.quality_report.record_source("sina_money_flow_history", False, 0, f"{code}: {exc}")
            errors.append(f"sina_history_failed:{exc}")
        error = "; ".join(errors) if errors else "fund_flow_not_found"
        self._fund_flow_errors[code] = error
        return [], "missing", error

    def _safe_replay_fund_flow(self, code: str) -> tuple[list[dict], str, str]:
        try:
            rows = self._eastmoney_fund_flow_daily(code)
            dated = [row for row in rows if str(row.get("time", "")).startswith(self.expected_data_date)]
            if dated:
                self.quality_report.record_source("eastmoney_fund_flow_daily", True, len(dated))
                return dated, "eastmoney_fund_flow_daily", ""
            error = "replay_daily_fund_flow_unavailable"
        except Exception as exc:
            error = f"daily_failed:{exc}"
            self.quality_report.record_source("eastmoney_fund_flow_daily", False, 0, f"{code}: {exc}")
        try:
            rows = self._sina_money_flow_history(code)
            dated = [row for row in rows if str(row.get("time", "")).startswith(self.expected_data_date)]
            if dated:
                self.quality_report.record_source("sina_money_flow_history", True, len(dated))
                return dated, "sina_money_flow_history", ""
            error = f"{error}; replay_sina_money_flow_unavailable"
            self.quality_report.record_source("sina_money_flow_history", False, 0, f"{code}: replay date not found")
        except Exception as exc:
            error = f"{error}; sina_history_failed:{exc}"
            self.quality_report.record_source("sina_money_flow_history", False, 0, f"{code}: {exc}")
        self._fund_flow_errors[code] = error
        return [], "missing", error

    def _merge_standard_fields(self, base: dict, incoming: dict, source: str) -> dict:
        merged = dict(base)
        sources = dict(merged.get("_sources") or {})
        for key, value in incoming.items():
            if key == "_sources":
                for field, field_source in value.items():
                    sources[field] = field_source
                continue
            if value is not None and value != "":
                merged[key] = value
                sources.setdefault(key, source)
        merged["_sources"] = sources
        return merged

    def _merge_stock_info(self, stock: dict, info: dict) -> dict:
        if not info:
            return stock
        sources = dict(stock.get("_sources") or {})
        code = str(stock.get("code", ""))
        if info.get("f127"):
            stock["industry"] = info.get("f127")
            sources["industry"] = "eastmoney_stock_info.f127"
        if info.get("f117") and not stock.get("float_mcap_yi"):
            stock["float_mcap_yi"] = self._as_float(info.get("f117"), None, scale=1 / 100000000)
            sources["float_mcap_yi"] = "eastmoney_stock_info.f117"
        list_date = str(info.get("f189") or "")
        if list_date:
            stock["_sources"] = sources
            stock = self._apply_list_date(stock, list_date, "eastmoney_stock_info.f189")
            if stock.get("list_date") and code:
                self.update_list_date_cache(code, stock["list_date"], "eastmoney_stock_info")
            sources = dict(stock.get("_sources") or {})
        elif code:
            self._stock_info_errors.setdefault(code, "list_date_missing_not_found")
        stock["_sources"] = sources
        return stock

    def _merge_quote_meta(self, stock: dict, meta: dict) -> dict:
        if not meta:
            return stock
        sources = dict(stock.get("_sources") or {})
        if meta.get("f100") and not stock.get("industry"):
            stock["industry"] = meta.get("f100")
            sources["industry"] = "eastmoney_quote_meta.f100"
        list_date = str(meta.get("f26") or "")
        if list_date:
            stock["_sources"] = sources
            stock = self._apply_list_date(stock, list_date, "eastmoney_quote_meta.f26")
            if stock.get("list_date") and stock.get("code"):
                self.update_list_date_cache(str(stock["code"]), stock["list_date"], "eastmoney_quote_meta")
            return stock
        stock["_sources"] = sources
        return stock

    def _merge_baidu_theme_fallback(self, stock: dict, blocks: dict) -> dict:
        if not blocks:
            return stock
        sources = dict(stock.get("_sources") or {})
        block_change_pct = self._theme_block_change_pct_from_blocks(stock.get("theme_tags") or blocks.get("concept_tags") or [], blocks)
        if block_change_pct is not None and stock.get("theme_block_change_pct") in (None, ""):
            stock["theme_block_change_pct"] = block_change_pct
            sources["theme_block_change_pct"] = "baidu_concept_blocks.concept.change_pct"
        if stock.get("theme_tags"):
            stock["_sources"] = sources
            return stock
        tags = list(dict.fromkeys(blocks.get("concept_tags") or []))
        if not tags:
            tags = [item.get("name", "") for item in blocks.get("industry", []) if item.get("name")]
        tags = [tag for tag in tags if tag]
        if not tags:
            stock["_sources"] = sources
            return stock
        stock["theme_tags"] = tags
        stock["theme_source"] = "baidu_concept_blocks"
        stock.setdefault("theme_rank", None)
        stock.setdefault("same_theme_strong_count", 0)
        if blocks.get("industry") and not stock.get("industry"):
            stock["industry"] = blocks["industry"][0].get("name", "")
            sources["industry"] = "baidu_concept_blocks.industry"
        sources["theme_tags"] = "baidu_concept_blocks.concept_tags"
        sources["theme_source"] = "baidu_concept_blocks"
        stock["_sources"] = sources
        return stock

    def _theme_block_change_pct_from_blocks(self, tags: list[str], blocks: dict) -> float | None:
        concept_rows = list(blocks.get("concept") or [])
        industry_rows = list(blocks.get("industry") or [])
        rows = concept_rows or industry_rows
        if not rows:
            return None
        tag_set = {str(tag) for tag in tags if tag}
        ordered = [item for item in rows if str(item.get("name", "")) in tag_set] if tag_set else []
        ordered.extend(item for item in rows if item not in ordered)
        for item in ordered:
            value = self._as_float(item.get("change_pct"), None)
            if value is not None:
                return value
        return None

    def _merge_industry_theme_fallback(self, stock: dict) -> dict:
        if stock.get("theme_tags") or not stock.get("industry"):
            return stock
        industry = str(stock.get("industry") or "").strip()
        if not industry:
            return stock
        sources = dict(stock.get("_sources") or {})
        stock["theme_tags"] = [industry]
        stock["theme_source"] = "industry_fallback"
        stock.setdefault("theme_rank", None)
        stock.setdefault("same_theme_strong_count", 0)
        sources["theme_tags"] = sources.get("industry", "industry_fallback")
        sources["theme_source"] = sources.get("industry", "industry_fallback")
        stock["_sources"] = sources
        return stock

    def _merge_fund_flow(self, stock: dict, rows: list[dict], source: str = "", error: str = "") -> dict:
        stock["fund_flow_source"] = source or "missing"
        stock["fund_flow_error"] = error
        if not rows:
            sources = dict(stock.get("_sources") or {})
            if stock.get("main_net") not in (None, "") and stock.get("big_order_net") not in (None, ""):
                stock["fund_flow_source"] = self._existing_fund_flow_source(stock)
                stock["_sources"] = sources
                return stock
            if stock.get("big_order_net") not in (None, ""):
                stock["main_net"] = stock.get("big_order_net")
                stock["fund_flow_source"] = "estimated_from_big_order_net"
                sources["main_net"] = f"{sources.get('big_order_net', 'big_order_net')}.estimate"
                stock["_sources"] = sources
            elif stock.get("main_net") not in (None, ""):
                stock["fund_flow_source"] = self._existing_fund_flow_source(stock)
                stock["_sources"] = sources
            return stock
        latest = rows[-1]
        stock["main_net"] = latest.get("main_net")
        stock["big_order_net"] = latest.get("large_net")
        sources = dict(stock.get("_sources") or {})
        row_sources = dict(latest.get("_sources") or {})
        sources["main_net"] = row_sources.get("main_net", f"{source}.main_net")
        sources["big_order_net"] = row_sources.get("large_net", f"{source}.large_net")
        stock["_sources"] = sources
        return stock

    @staticmethod
    def _existing_fund_flow_source(stock: dict) -> str:
        current = stock.get("fund_flow_source")
        if current and current != "missing":
            return str(current)
        main_source = str((stock.get("_sources") or {}).get("main_net", ""))
        if "." in main_source:
            return main_source.rsplit(".", 1)[0]
        return "seed_fund_flow"

    def _merge_list_date_from_seed_or_cache(self, stock: dict) -> dict:
        for field in ("list_date", "listing_date", "listed_date", "上市日期", "f189"):
            if stock.get(field):
                source = (stock.get("_sources") or {}).get(field, f"candidate_seed.{field}")
                return self._apply_list_date(stock, str(stock[field]), source)
        cached = self.get_cached_list_date(str(stock.get("code", "")))
        if cached:
            return self._apply_list_date(stock, cached["list_date"], f"list_date_cache.{cached.get('source', 'unknown')}")
        return stock

    def _ensure_list_date_status(self, stock: dict, code: str) -> dict:
        if "is_new_stock" in stock and stock.get("is_new_stock") is not None:
            return stock
        existing_reasons = stock.get("_risk_unknown_reasons") or []
        if any(str(reason).startswith("list_date_missing") for reason in existing_reasons):
            return stock
        reason = self._stock_info_errors.get(code, "list_date_missing_not_found")
        self._append_risk_unknown(stock, reason)
        return stock

    def _derive_live_safety_and_shape(self, stock: dict) -> dict:
        sources = dict(stock.get("_sources") or {})
        code = str(stock.get("code", ""))
        name = str(stock.get("name", ""))
        price = self._as_float(stock.get("price"), 0.0)
        limit_up = self._as_float(stock.get("limit_up"), None)
        high = self._as_float(stock.get("high"), None)
        low = self._as_float(stock.get("low"), None)
        amount = self._as_float(stock.get("amount_wan"), None)
        volume = self._as_float(stock.get("volume"), None)

        if "is_bj" not in stock:
            stock["is_bj"] = code.startswith(("8", "4"))
            stock["is_bj_stock"] = stock["is_bj"]
            sources["is_bj"] = "code_prefix"
            sources["is_bj_stock"] = "code_prefix"
        name_indicates_st = self._is_st_or_delisting_name(name)
        if name_indicates_st or ("is_st" not in stock and name):
            stock["is_st"] = name_indicates_st
            sources["is_st"] = "name_prefix"
        suspended = (
            bool(stock.get("_quote_missing"))
            or price <= 0
            or (amount is not None and amount <= 0)
            or (volume is not None and volume <= 0)
        )
        if suspended or ("is_suspended" not in stock and "price" in stock):
            stock["is_suspended"] = suspended
            sources["is_suspended"] = "quote_price_amount"
        if limit_up is not None and "is_limit_up" not in stock:
            stock["is_limit_up"] = limit_up > 0 and price >= limit_up - 0.005
            sources["is_limit_up"] = "price_vs_limit_up"
        if high and low and high > low:
            stock["range_position"] = max(0.0, min(1.0, (price - low) / (high - low)))
            stock["tail_pullback_pct"] = max(0.0, (high - price) / high * 100)
            stock["upper_shadow_ratio"] = stock["tail_pullback_pct"] / max(abs(float(stock.get("change_pct", 0) or 0)), 1.0)
            sources["range_position"] = "tencent_quote.high_low_price"
            sources["tail_pullback_pct"] = "tencent_quote.high_price"
            sources["upper_shadow_ratio"] = "tencent_quote.high_price"
        stock["_sources"] = sources
        return stock

    def _derive_freshness(self, stock: dict) -> dict:
        freshness = dict(stock.get("_freshness") or {})
        reasons: list[str] = []
        if not freshness:
            freshness["live_quote"] = {
                "source": "live_quote",
                "data_date": "",
                "data_time": "",
                "is_stale": False,
                "stale_reason": "freshness_unknown",
            }
        for source, item in freshness.items():
            prior_today_reason = item.get("stale_reason", "") if item.get("data_date") == self.now.date().isoformat() else ""
            evaluated = self._freshness_for_date(
                source,
                item.get("data_date", ""),
                item.get("data_time", ""),
                stale_reason_if_today=prior_today_reason,
            )
            freshness[source] = evaluated
            self.quality_report.record_freshness(source, evaluated)
            for reason in self._freshness_rejection_reasons(evaluated):
                if reason not in reasons:
                    reasons.append(reason)
        stock["_freshness"] = freshness
        stock["_freshness_reasons"] = reasons
        return stock

    def _apply_session_context(self, snapshot: dict) -> None:
        session_state = get_session_state(self.now)
        is_trade_day = is_likely_cn_trade_day(self.now)
        warnings: list[str] = []
        session_reasons: list[str] = []
        if not is_trade_day or session_state == NON_TRADING_DAY:
            session_reasons.append("non_trading_day")
            warnings.append("non_trading_day")
        elif session_state != TAIL_SESSION and not self.allow_outside_session:
            session_reasons.append("outside_tail_session")
            warnings.append("outside_tail_session")
        snapshot["trade_date"] = self.now.date().isoformat()
        snapshot["run_time"] = self.now.isoformat()
        snapshot["session_state"] = session_state
        snapshot["is_trade_day"] = is_trade_day
        snapshot["session_reject_reasons"] = session_reasons
        self.quality_report.set_session_context(self.now.isoformat(), session_state, is_trade_day, warnings)

    def _freshness_for_date(
        self,
        source: str,
        data_date: str,
        data_time: str = "",
        stale_reason_if_today: str = "",
    ) -> dict:
        if not data_date:
            return {
                "source": source,
                "data_date": "",
                "data_time": data_time,
                "is_stale": False,
                "stale_reason": "timestamp_missing",
                "freshness_basis": "blocked",
            }
        if self.data_context == DATA_CONTEXT_PREVIOUS_CLOSE_REPLAY:
            if data_date == self.expected_data_date:
                return {
                    "source": source,
                    "data_date": data_date,
                    "data_time": data_time,
                    "is_stale": False,
                    "stale_reason": "",
                    "freshness_basis": "previous_close_expected",
                }
            reason = "replay_data_too_old" if data_date < self.expected_data_date else "replay_data_from_observation_day"
            return {
                "source": source,
                "data_date": data_date,
                "data_time": data_time,
                "is_stale": True,
                "stale_reason": reason,
                "freshness_basis": "blocked",
            }
        is_stale = data_date != self.now.date().isoformat()
        stale_reason = "quote_stale" if is_stale else stale_reason_if_today
        return {
            "source": source,
            "data_date": data_date,
            "data_time": data_time,
            "is_stale": is_stale,
            "stale_reason": stale_reason,
            "freshness_basis": "current_session",
        }

    def _normalize_data_date(self, value: str | int | None) -> str:
        parsed = self._parse_list_date(value)
        return parsed.isoformat() if parsed else ""

    @staticmethod
    def _freshness_rejection_reasons(freshness: dict) -> list[str]:
        reasons = []
        reason = freshness.get("stale_reason", "")
        if reason in {"replay_data_too_old", "replay_data_from_observation_day"}:
            reasons.extend(["freshness_unknown", reason])
        elif freshness.get("is_stale"):
            reasons.append("quote_stale")
        if reason == "timestamp_missing":
            reasons.extend(["timestamp_missing", "freshness_unknown"])
        if reason in {"freshness_unknown", "non_trading_day_cache_possible"}:
            reasons.append("freshness_unknown")
        return reasons

    def _replay_market_snapshot(self) -> dict:
        snapshot = {
            "indices": {},
            "trade_date": self.expected_data_date,
            "run_time": self.now.isoformat(),
            "session_state": get_session_state(self.now),
            "is_trade_day": is_likely_cn_trade_day(self.now),
            "_data_quality_flags": [],
        }
        try:
            indices = self._tencent_quotes(["sh000001", "sh000300", "sz399006"])
            for code, item in indices.items():
                raw_freshness = (item.get("_freshness") or {}).get("tencent_quote") or {}
                freshness = self._freshness_for_date(
                    "tencent_indices",
                    raw_freshness.get("data_date", ""),
                    raw_freshness.get("data_time", ""),
                )
                reasons = self._freshness_rejection_reasons(freshness)
                self.quality_report.record_source("tencent_indices", not reasons, 1, freshness=freshness)
                if reasons:
                    snapshot["_data_quality_flags"].extend(reasons)
                    continue
                snapshot["indices"][code] = {"name": item.get("name", ""), "change_pct": item.get("change_pct", 0)}
        except Exception as exc:
            self.quality_report.record_source("tencent_indices", False, 0, str(exc))
            snapshot["_data_quality_flags"].append("freshness_unknown")
        snapshot["_data_quality_flags"] = list(dict.fromkeys(snapshot["_data_quality_flags"]))
        return snapshot

    def _apply_list_date(self, stock: dict, raw_list_date: str, source: str) -> dict:
        parsed = self._parse_list_date(raw_list_date)
        if parsed is None:
            self._append_risk_unknown(stock, "list_date_missing_parse_failed")
            return stock
        sources = dict(stock.get("_sources") or {})
        listed_days = (self.now.date() - parsed).days
        stock["list_date"] = parsed.isoformat()
        stock["listed_days"] = listed_days
        stock["is_new_stock"] = listed_days < 60
        sources["list_date"] = source
        sources["listed_days"] = source
        sources["is_new_stock"] = source
        stock["_sources"] = sources
        return stock

    def _append_risk_unknown(self, stock: dict, reason: str) -> None:
        reasons = list(stock.get("_risk_unknown_reasons") or [])
        if reason not in reasons:
            reasons.append(reason)
        stock["_risk_unknown_reasons"] = reasons

    def _load_list_date_cache(self) -> dict[str, dict]:
        if self._list_date_cache is not None:
            return self._list_date_cache
        path = self.cache_dir / "list_date_cache.json"
        if not path.exists():
            self._list_date_cache = {}
            return self._list_date_cache
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._list_date_cache = data if isinstance(data, dict) else {}
        except Exception as exc:
            self.quality_report.warnings.append(f"list_date_cache: read_failed: {exc}")
            self._list_date_cache = {}
        return self._list_date_cache

    def _save_list_date_cache(self, cache: dict[str, dict]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / "list_date_cache.json"
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._list_date_cache = cache

    def _get_json(self, url: str, params: dict | None = None, headers: dict | None = None, timeout: int = 10, retries: int = 2) -> dict:
        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        raw = ""
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
            try:
                raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", errors="ignore")
                break
            except Exception:
                if attempt >= retries:
                    raise
        return json.loads(raw)

    def _get_json_resilient(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        timeout: int = 10,
        retries: int = 2,
    ) -> dict:
        try:
            return self._get_json(url, params=params, headers=headers, timeout=timeout, retries=retries)
        except Exception as first_exc:
            try:
                import requests  # type: ignore

                session = requests.Session()
                session.trust_env = False
                response = session.get(url, params=params or {}, headers=headers or {"User-Agent": UA}, timeout=timeout)
                response.raise_for_status()
                return response.json()
            except Exception as second_exc:
                raise RuntimeError(f"urllib_failed:{first_exc}; direct_requests_failed:{second_exc}") from second_exc

    @staticmethod
    def _as_float(value, default=0.0, scale: float = 1.0):
        try:
            if value in (None, "", "-"):
                return default
            return float(value) * scale
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _quote_vwap(quote: dict) -> float:
        amount_wan = AStockClient._as_float(quote.get("amount_wan"), 0.0)
        volume_hands = AStockClient._as_float(quote.get("volume"), 0.0)
        if amount_wan > 0 and volume_hands > 0:
            return round(amount_wan * 100 / volume_hands, 3)
        price = AStockClient._as_float(quote.get("price"), 0.0)
        open_price = AStockClient._as_float(quote.get("open"), 0.0)
        if price and open_price:
            return round((price + open_price) / 2, 3)
        return price

    @staticmethod
    def _pct_gap(target, base) -> float:
        target_value = AStockClient._as_float(target, 0.0)
        base_value = AStockClient._as_float(base, 0.0)
        if not target_value or not base_value:
            return 0.0
        return round((target_value - base_value) / base_value * 100, 2)

    def _first_float(self, item: dict, keys: tuple[str, ...], default=0.0):
        for key in keys:
            if key in item:
                value = self._as_float(item.get(key), None)
                if value is not None:
                    return value
        return default

    @staticmethod
    def _amount_to_wan(value):
        number = AStockClient._as_float(value, None)
        if number is None:
            return None
        return number / 10000 if number > 1000000 else number

    def _easyquotation_amount_wan(self, item: dict) -> float | None:
        explicit = self._first_float(item, ("amount_wan", "成交额_万"), None)
        if explicit is not None:
            return explicit
        amount = self._first_float(item, ("turnover", "amount", "成交额"), None)
        return self._amount_to_wan(amount)

    def _is_new_stock(self, list_date: str) -> bool:
        listed = self._parse_list_date(list_date)
        if listed is None:
            return False
        return (self.now.date() - listed).days < 60

    @staticmethod
    def _parse_list_date(value: str | int | None):
        if value in (None, "", "-"):
            return None
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if len(digits) < 8:
            return None
        try:
            return datetime.strptime(digits[:8], "%Y%m%d").date()
        except ValueError:
            return None

    @staticmethod
    def _is_st_or_delisting_name(name: str) -> bool:
        cleaned = str(name or "").strip()
        upper = cleaned.upper()
        return upper.startswith(("ST", "*ST", "S*ST")) or "退" in cleaned or "退市" in cleaned

    @staticmethod
    def _fill_missing_mas(rows: list[dict]) -> None:
        for idx, row in enumerate(rows):
            closes = [float(item.get("close", 0) or 0) for item in rows[: idx + 1]]
            for window in (5, 10, 20, 60):
                key = f"ma{window}"
                if row.get(key) is None:
                    sample = closes[-window:]
                    row[key] = round(sum(sample) / len(sample), 2) if sample else 0.0

    @staticmethod
    def _coerce_now(now: datetime | None) -> datetime:
        current = now or datetime.now(CN_TZ)
        if current.tzinfo is None:
            current = current.replace(tzinfo=CN_TZ)
        return current.astimezone(CN_TZ)

    @staticmethod
    def _freshness_from_tencent(raw_timestamp: str) -> dict:
        if not raw_timestamp:
            return {
                "source": "tencent_quote",
                "data_date": "",
                "data_time": "",
                "is_stale": False,
                "stale_reason": "timestamp_missing",
            }
        digits = "".join(ch for ch in str(raw_timestamp) if ch.isdigit())
        if len(digits) < 8:
            return {
                "source": "tencent_quote",
                "data_date": "",
                "data_time": "",
                "is_stale": False,
                "stale_reason": "freshness_unknown",
            }
        data_date = f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
        data_time = ""
        if len(digits) >= 14:
            data_time = f"{digits[8:10]}:{digits[10:12]}:{digits[12:14]}"
        return {
            "source": "tencent_quote",
            "data_date": data_date,
            "data_time": data_time,
            "is_stale": False,
            "stale_reason": "",
        }

    @staticmethod
    def _freshness_from_eastmoney_timestamp(raw_timestamp) -> dict:
        try:
            value = int(raw_timestamp)
        except (TypeError, ValueError):
            return {
                "source": "eastmoney_after_close_universe",
                "data_date": "",
                "data_time": "",
                "is_stale": False,
                "stale_reason": "timestamp_missing",
            }
        current = datetime.fromtimestamp(value, tz=CN_TZ)
        return {
            "source": "eastmoney_after_close_universe",
            "data_date": current.date().isoformat(),
            "data_time": current.strftime("%H:%M:%S"),
            "is_stale": False,
            "stale_reason": "",
        }

    def _freshness_from_easyquotation(self, item: dict) -> dict:
        raw_date = str(item.get("date") or item.get("日期") or "").strip()
        raw_time = str(item.get("time") or item.get("时间") or "").strip()
        data_date = self._normalize_data_date(raw_date)
        if not data_date:
            data_date = self.now.date().isoformat()
        return {
            "source": "easyquotation_sina_full_market",
            "data_date": data_date,
            "data_time": raw_time,
            "is_stale": False,
            "stale_reason": "" if raw_time else "timestamp_missing",
        }

    def _fallback(self, message: str, fn):
        self._note(message)
        return fn()

    def _note(self, message: str) -> None:
        self.fallback_messages.append(message)
        self.logger.warning(message)

    def write_quality_report(self, reports_dir: str, trade_date: str) -> str:
        return self.quality_report.write_markdown(reports_dir, trade_date)
