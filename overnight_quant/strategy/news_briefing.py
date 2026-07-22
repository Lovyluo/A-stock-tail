from __future__ import annotations

import copy
import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable

import requests

from overnight_quant.data.market_calendar import CN_TZ, previous_likely_cn_trade_day


DEFAULT_NEWS_CONFIG = {
    "news_briefing": {
        "enabled": True,
        "lookback_start_time": "15:00",
        "morning_end_time": "09:25",
        "max_global_news": 80,
        "max_cls_news": 80,
        "max_stock_news_per_code": 10,
    },
    "paths": {
        "records_dir": "overnight_quant/records",
        "reports_dir": "overnight_quant/reports",
        "examples_dir": "overnight_quant/examples",
    },
}

MACRO_WORDS = ("央行", "利率", "汇率", "通胀", "美联储", "经济", "GDP", "PMI", "财政")
POLICY_WORDS = ("国务院", "证监会", "监管", "政策", "条例", "办法", "发改委", "工信部")
THEME_WORDS = ("人工智能", "算力", "半导体", "创新药", "机器人", "新能源", "消费", "军工", "医药")
RISK_WORDS = ("风险", "处罚", "立案", "减持", "退市", "下调", "亏损", "终止")
POSITIVE_WORDS = ("增长", "中标", "回购", "增持", "突破", "获批", "改善", "支持")


def load_news_config(path: str | None = None) -> dict:
    config = copy.deepcopy(DEFAULT_NEWS_CONFIG)
    config_path = Path(path) if path else Path(__file__).resolve().parents[1] / "config.yaml"
    if not config_path.exists():
        return config
    try:
        import yaml  # type: ignore

        _deep_update(config, yaml.safe_load(config_path.read_text(encoding="utf-8")) or {})
    except Exception:
        pass
    return config


class NewsBriefingAnalyzer:
    def __init__(self, config: dict, mode: str, now: datetime | None = None, candidates: list[dict] | None = None, source_fetchers: dict[str, Callable[..., list[dict]]] | None = None):
        self.config = config
        self.mode = mode
        self.now = _coerce_now(now)
        self.candidates = candidates or []
        self.fetchers = source_fetchers or {}

    def analyze(self, trade_date: str | None = None) -> dict:
        target = date.fromisoformat(trade_date) if trade_date else self.now.date()
        start, end = news_window(target, self.config)
        result = {
            "trade_date": target.isoformat(),
            "run_time": self.now.isoformat(timespec="seconds"),
            "mode": self.mode,
            "status": "NEWS_BRIEFING_READY",
            "window_start": start.isoformat(timespec="minutes"),
            "window_end": end.isoformat(timespec="minutes"),
            "sources": [],
            "macro_news": [],
            "policy_news": [],
            "theme_news": [],
            "stock_news": [],
            "focus_directions": [],
            "attack_plan": [],
            "defence_plan": [],
            "risk_notes": [],
        }
        settings = self.config.get("news_briefing", {})
        global_rows = self._fetch("eastmoney_global_news", max_items=int(settings.get("max_global_news", 80)))
        cls_rows = self._fetch("cls_telegraph", max_items=int(settings.get("max_cls_news", 80)))
        broad = _within_window(global_rows + cls_rows, start, end)
        result["macro_news"] = _select(broad, MACRO_WORDS, 12)
        result["policy_news"] = _select(broad, POLICY_WORDS, 12)
        result["theme_news"] = _select(broad, THEME_WORDS, 16)

        max_per_code = int(settings.get("max_stock_news_per_code", 10))
        for candidate in self.candidates:
            code = _normalize_code(candidate.get("code"))
            if not code:
                continue
            stock_rows = self._fetch("eastmoney_stock_news", code=code, max_items=max_per_code)
            announcement_rows = self._fetch("cninfo_announcements", code=code, max_items=max_per_code)
            for item in _within_window(stock_rows + announcement_rows, start, end):
                enriched = dict(item)
                enriched.setdefault("code", code)
                enriched.setdefault("name", candidate.get("name", ""))
                result["stock_news"].append(enriched)

        combined = broad + result["stock_news"]
        result["focus_directions"] = _focus_directions(combined)
        positive = sum(1 for item in combined if _contains(item, POSITIVE_WORDS))
        risky = sum(1 for item in combined if _contains(item, RISK_WORDS))
        result["attack_plan"] = [
            "只观察消息、竞价与量价方向形成共振的候选。",
            "出现分歧时等待 VWAP 承接和成交量确认，不追逐瞬时拉升。",
        ]
        result["defence_plan"] = [
            "负面消息与弱指数同时出现时降低观察等级。",
            "持仓优先检查成本线、止损线和 VWAP 反抽是否有效。",
        ]
        if risky > positive:
            result["risk_notes"].append("负面或风险关键词多于正面关键词，防御优先。")
        missing = [item["source"] for item in result["sources"] if not item["ok"]]
        if missing:
            result["status"] = "NEWS_BRIEFING_DEGRADED"
            result["risk_notes"].append("缺失来源：" + "、".join(missing))
        result["risk_notes"].append("规则摘要可能遗漏语义，仅作为信息整理。")
        return result

    def _fetch(self, source: str, **kwargs) -> list[dict]:
        fetcher = self.fetchers.get(source) or DEFAULT_FETCHERS.get(source)
        if not fetcher:
            self._record_source(source, False, 0, "fetcher_missing")
            return []
        try:
            rows = list(fetcher(**kwargs) or [])
            self._record_source(source, bool(rows), len(rows), "" if rows else "empty")
            return rows
        except Exception as exc:
            self._record_source(source, False, 0, f"{type(exc).__name__}: {exc}")
            return []

    def _record_source(self, source: str, ok: bool, rows: int, error: str) -> None:
        # The result object is built inside analyze; keep a per-run buffer.
        if not hasattr(self, "_source_rows"):
            self._source_rows = []
        self._source_rows.append({"source": source, "ok": ok, "rows": rows, "error": error, "fetched_at": self.now.isoformat(timespec="seconds")})


def news_window(target: date, config: dict) -> tuple[datetime, datetime]:
    settings = config.get("news_briefing", {})
    previous = previous_likely_cn_trade_day(target)
    start = datetime.combine(previous, _parse_time(settings.get("lookback_start_time", "15:00")), tzinfo=CN_TZ)
    end = datetime.combine(target, _parse_time(settings.get("morning_end_time", "09:25")), tzinfo=CN_TZ)
    return start, end


def finalize_news_sources(analyzer: NewsBriefingAnalyzer, result: dict) -> dict:
    result["sources"] = list(getattr(analyzer, "_source_rows", []))
    missing = [item["source"] for item in result["sources"] if not item["ok"]]
    if missing:
        result["status"] = "NEWS_BRIEFING_DEGRADED"
        note = "缺失来源：" + "、".join(dict.fromkeys(missing))
        if note not in result["risk_notes"]:
            result["risk_notes"].append(note)
    return result


def fetch_eastmoney_global_news(max_items: int = 80, **_: Any) -> list[dict]:
    url = "https://np-weblist.eastmoney.com/comm/web/getNewsByColumns"
    params = {"client": "web", "biz": "web_news_col", "column": "345", "page_index": 1, "page_size": max_items}
    data = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=12).json()
    rows = data.get("data", {}).get("list") or data.get("data") or []
    return [_normalize_news(item, "eastmoney_global_news") for item in rows if isinstance(item, dict)]


def fetch_cls_telegraph(max_items: int = 80, **_: Any) -> list[dict]:
    url = "https://www.cls.cn/nodeapi/telegraphList"
    params = {"app": "CailianpressWeb", "category": "", "os": "web", "rn": max_items, "sv": "8.4.6"}
    data = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.cls.cn/telegraph"}, timeout=12).json()
    rows = data.get("data", {}).get("roll_data") or data.get("data", {}).get("telegraph_list") or []
    return [_normalize_news(item, "cls_telegraph") for item in rows if isinstance(item, dict)]


def fetch_eastmoney_stock_news(code: str, max_items: int = 10, **_: Any) -> list[dict]:
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    params = {"cb": "", "param": f"{{\"uid\":\"\",\"keyword\":\"{code}\",\"type\":[\"cmsArticleWebOld\"],\"pageIndex\":1,\"pageSize\":{max_items}}}"}
    text = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=12).text.strip()
    text = re.sub(r"^[^(]*\(|\)\s*;?$", "", text)
    data = json.loads(text)
    rows = data.get("result", {}).get("cmsArticleWebOld") or data.get("result", {}).get("items") or []
    return [_normalize_news(item, "eastmoney_stock_news") for item in rows if isinstance(item, dict)]


def fetch_cninfo_announcements(code: str, max_items: int = 10, **_: Any) -> list[dict]:
    if code.startswith("6"):
        org_id = f"gssh0{code}"
    elif code.startswith(("8", "4")):
        org_id = f"gsbj0{code}"
    else:
        org_id = f"gssz0{code}"
    payload = {"stock": f"{code},{org_id}", "tabName": "fulltext", "pageSize": str(max_items), "pageNum": "1", "column": "", "category": "", "plate": "", "seDate": "", "searchkey": "", "secid": "", "sortName": "", "sortType": "", "isHLtitle": "true"}
    data = requests.post("https://www.cninfo.com.cn/new/hisAnnouncement/query", data=payload, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninfo.com.cn/new/disclosure"}, timeout=12).json()
    return [_normalize_news(item, "cninfo_announcements") for item in data.get("announcements") or []]


DEFAULT_FETCHERS = {
    "eastmoney_global_news": fetch_eastmoney_global_news,
    "cls_telegraph": fetch_cls_telegraph,
    "eastmoney_stock_news": fetch_eastmoney_stock_news,
    "cninfo_announcements": fetch_cninfo_announcements,
}


def _normalize_news(item: dict, source: str) -> dict:
    timestamp = item.get("showTime") or item.get("time") or item.get("ctime") or item.get("date") or item.get("announcementTime") or item.get("publish_time") or ""
    if isinstance(timestamp, (int, float)):
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        timestamp = datetime.fromtimestamp(timestamp, CN_TZ).isoformat(timespec="seconds")
    return {
        "title": _clean(item.get("title") or item.get("brief") or item.get("content") or item.get("announcementTitle") or ""),
        "summary": _clean(item.get("summary") or item.get("digest") or item.get("content") or ""),
        "published_at": str(timestamp),
        "source": source,
        "url": item.get("url") or item.get("shareurl") or "",
    }


def _within_window(rows: list[dict], start: datetime, end: datetime) -> list[dict]:
    selected = []
    for row in rows:
        stamp = _parse_datetime(row.get("published_at"))
        if stamp is None or start <= stamp <= end:
            selected.append(row)
    return selected


def _select(rows: list[dict], words: tuple[str, ...], limit: int) -> list[dict]:
    return [row for row in rows if _contains(row, words)][:limit]


def _focus_directions(rows: list[dict]) -> list[str]:
    counts = {word: sum(1 for row in rows if _contains(row, (word,))) for word in THEME_WORDS}
    return [word for word, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])) if count][:5]


def _contains(row: dict, words: tuple[str, ...]) -> bool:
    text = f"{row.get('title', '')} {row.get('summary', '')}"
    return any(word.lower() in text.lower() for word in words)


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
            try:
                parsed = datetime.strptime(text[:19], fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return (parsed.replace(tzinfo=CN_TZ) if parsed.tzinfo is None else parsed).astimezone(CN_TZ)


def _parse_time(value: str) -> time:
    hour, minute = (int(item) for item in str(value).split(":")[:2])
    return time(hour, minute)


def _coerce_now(value: datetime | None) -> datetime:
    current = value or datetime.now(CN_TZ)
    return (current.replace(tzinfo=CN_TZ) if current.tzinfo is None else current).astimezone(CN_TZ)


def _normalize_code(value: Any) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", str(value or ""))).strip()


def _deep_update(target: dict, source: dict) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
