from __future__ import annotations

import copy
import json
import re
import uuid
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
        "max_newsnow_items_per_source": 40,
        "max_stock_news_per_code": 10,
        "newsnow_base_url": "https://newsnow.busiyi.world",
    },
    "paths": {
        "records_dir": "overnight_quant/records",
        "reports_dir": "overnight_quant/reports",
        "examples_dir": "overnight_quant/examples",
    },
}

MACRO_WORDS = ("央行", "利率", "汇率", "通胀", "美联储", "经济", "GDP", "PMI", "财政", "社融", "信贷", "关税")
POLICY_WORDS = ("国务院", "证监会", "监管", "政策", "条例", "办法", "发改委", "工信部", "商务部", "财政部", "交易所")
THEME_WORDS = (
    "人工智能",
    "算力",
    "半导体",
    "创新药",
    "机器人",
    "新能源",
    "消费",
    "军工",
    "医药",
    "低空经济",
    "商业航天",
    "数据中心",
    "光模块",
    "芯片",
    "稀土",
    "有色",
    "证券",
)
MARKET_WORDS = ("A股", "上证", "沪深300", "创业板", "科创板", "成交额", "北向", "主力资金", "人民币", "港股")
GLOBAL_WORDS = ("美股", "纳斯达克", "标普", "道指", "日经", "原油", "黄金", "美元", "美联储", "关税", "海外")
RISK_WORDS = ("风险", "处罚", "立案", "减持", "退市", "下调", "亏损", "终止")
POSITIVE_WORDS = ("增长", "中标", "回购", "增持", "突破", "获批", "改善", "支持")
BROAD_NEWS_SOURCES = (
    "eastmoney_global_news",
    "cls_telegraph",
    "newsnow_cls_hot",
    "newsnow_wallstreetcn",
    "newsnow_jin10",
    "newsnow_xueqiu_hotstock",
)


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
        self.use_only_supplied_fetchers = source_fetchers is not None

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
            "market_news": [],
            "global_news": [],
            "stock_news": [],
            "focus_directions": [],
            "attack_plan": [],
            "defence_plan": [],
            "risk_notes": [],
        }
        settings = self.config.get("news_briefing", {})
        broad_rows = [
            *self._fetch("eastmoney_global_news", max_items=int(settings.get("max_global_news", 80))),
            *self._fetch("cls_telegraph", max_items=int(settings.get("max_cls_news", 80))),
        ]
        newsnow_limit = int(settings.get("max_newsnow_items_per_source", 40))
        for source in BROAD_NEWS_SOURCES[2:]:
            broad_rows.extend(
                self._fetch(
                    source,
                    max_items=newsnow_limit,
                    base_url=str(settings.get("newsnow_base_url") or "https://newsnow.busiyi.world"),
                )
            )
        broad = _deduplicate_news(_within_window(broad_rows, start, end))
        result["macro_news"] = _select(broad, MACRO_WORDS, 12)
        result["policy_news"] = _select(broad, POLICY_WORDS, 12)
        result["theme_news"] = _select(broad, THEME_WORDS, 16)
        result["market_news"] = _select(broad, MARKET_WORDS, 12)
        result["global_news"] = _select(broad, GLOBAL_WORDS, 12)

        max_per_code = int(settings.get("max_stock_news_per_code", 10))
        for candidate in self.candidates:
            code = _normalize_code(candidate.get("code"))
            if not code:
                continue
            stock_rows = self._fetch("eastmoney_stock_news", code=code, max_items=max_per_code)
            announcement_rows = self._fetch("cninfo_announcements", code=code, max_items=max_per_code)
            for item in _deduplicate_news(_within_window(stock_rows + announcement_rows, start, end)):
                enriched = dict(item)
                enriched.setdefault("code", code)
                enriched.setdefault("name", candidate.get("name", ""))
                result["stock_news"].append(enriched)

        result["stock_news"] = _deduplicate_news(result["stock_news"])
        combined = _deduplicate_news(broad + result["stock_news"])
        result["news_count"] = len(combined)
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
        fetcher = self.fetchers.get(source)
        if fetcher is None and not self.use_only_supplied_fetchers:
            fetcher = DEFAULT_FETCHERS.get(source)
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
    result["sources"] = _aggregate_source_rows(list(getattr(analyzer, "_source_rows", [])))
    healthy_broad = [item for item in result["sources"] if item["source"] in BROAD_NEWS_SOURCES and item["ok"]]
    missing = [item["source"] for item in result["sources"] if not item["ok"]]
    result["source_coverage"] = f"{sum(1 for item in result['sources'] if item['ok'])}/{len(result['sources'])}"
    if not healthy_broad:
        result["status"] = "NEWS_BRIEFING_DEGRADED"
    elif missing:
        result["status"] = "NEWS_BRIEFING_PARTIAL"
    if missing:
        note = "部分来源不可用：" + "、".join(dict.fromkeys(missing))
        if note not in result["risk_notes"]:
            result["risk_notes"].append(note)
    return result


def fetch_eastmoney_global_news(max_items: int = 80, **_: Any) -> list[dict]:
    url = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
    params = {
        "client": "web",
        "biz": "web_724",
        "fastColumn": "102",
        "sortEnd": "",
        "pageSize": str(max_items),
        "req_trace": str(uuid.uuid4()),
    }
    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://kuaixun.eastmoney.com/"},
        timeout=12,
    )
    response.raise_for_status()
    data = response.json()
    rows = (data.get("data") or {}).get("fastNewsList") or []
    return [_normalize_news(item, "eastmoney_global_news") for item in rows if isinstance(item, dict)]


def fetch_cls_telegraph(max_items: int = 80, **_: Any) -> list[dict]:
    url = "https://www.cls.cn/nodeapi/telegraphList"
    params = {"rn": str(max_items), "page": "1"}
    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.cls.cn/"},
        timeout=12,
    )
    response.raise_for_status()
    data = response.json()
    rows = data.get("data", {}).get("roll_data") or data.get("data", {}).get("telegraph_list") or []
    return [_normalize_news(item, "cls_telegraph") for item in rows if isinstance(item, dict)]


def fetch_eastmoney_stock_news(code: str, max_items: int = 10, **_: Any) -> list[dict]:
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    callback = "jQuery_news"
    inner = json.dumps(
        {
            "uid": "",
            "keyword": code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": max_items,
                    "preTag": "",
                    "postTag": "",
                }
            },
        },
        separators=(",", ":"),
    )
    response = requests.get(
        url,
        params={"cb": callback, "param": inner},
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://so.eastmoney.com/"},
        timeout=12,
    )
    response.raise_for_status()
    text = response.text.strip()
    if "(" in text and text.rfind(")") > text.index("("):
        text = text[text.index("(") + 1 : text.rfind(")")]
    data = json.loads(text)
    payload = data.get("result", {}).get("cmsArticleWebOld") or []
    rows = payload.get("list") or [] if isinstance(payload, dict) else payload
    return [_normalize_news(item, "eastmoney_stock_news") for item in rows if isinstance(item, dict)]


def fetch_newsnow(
    source_id: str,
    source_name: str,
    max_items: int = 40,
    base_url: str = "https://newsnow.busiyi.world",
    **_: Any,
) -> list[dict]:
    response = requests.get(
        f"{base_url.rstrip('/')}/api/s",
        params={"id": source_id},
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        timeout=12,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        raise ValueError("newsnow_items_missing")
    normalized = []
    for item in rows[:max_items]:
        if not isinstance(item, dict):
            continue
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        normalized.append(
            _normalize_news(
                {
                    "title": item.get("title"),
                    "summary": extra.get("info") or extra.get("hover"),
                    "publish_time": item.get("pubDate") or extra.get("date"),
                    "url": item.get("url") or item.get("mobileUrl"),
                },
                source_name,
            )
        )
    return [item for item in normalized if item.get("title")]


def fetch_newsnow_cls_hot(**kwargs: Any) -> list[dict]:
    return fetch_newsnow("cls-hot", "newsnow_cls_hot", **kwargs)


def fetch_newsnow_wallstreetcn(**kwargs: Any) -> list[dict]:
    return fetch_newsnow("wallstreetcn-quick", "newsnow_wallstreetcn", **kwargs)


def fetch_newsnow_jin10(**kwargs: Any) -> list[dict]:
    return fetch_newsnow("jin10", "newsnow_jin10", **kwargs)


def fetch_newsnow_xueqiu_hotstock(**kwargs: Any) -> list[dict]:
    return fetch_newsnow("xueqiu-hotstock", "newsnow_xueqiu_hotstock", **kwargs)


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
    "newsnow_cls_hot": fetch_newsnow_cls_hot,
    "newsnow_wallstreetcn": fetch_newsnow_wallstreetcn,
    "newsnow_jin10": fetch_newsnow_jin10,
    "newsnow_xueqiu_hotstock": fetch_newsnow_xueqiu_hotstock,
    "eastmoney_stock_news": fetch_eastmoney_stock_news,
    "cninfo_announcements": fetch_cninfo_announcements,
}


def _normalize_news(item: dict, source: str) -> dict:
    timestamp = (
        item.get("showTime")
        or item.get("time")
        or item.get("ctime")
        or item.get("date")
        or item.get("announcementTime")
        or item.get("publish_time")
        or ""
    )
    if isinstance(timestamp, (int, float)):
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        timestamp = datetime.fromtimestamp(timestamp, CN_TZ).isoformat(timespec="seconds")
    return {
        "title": _clean(item.get("title") or item.get("brief") or item.get("content") or item.get("announcementTitle") or ""),
        "summary": _clean(item.get("summary") or item.get("digest") or item.get("content") or "")[:500],
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


def _deduplicate_news(rows: list[dict]) -> list[dict]:
    selected: dict[str, dict] = {}
    for row in rows:
        title = _clean(row.get("title") or row.get("summary"))
        if not title:
            continue
        key = re.sub(r"[\W_]+", "", title).lower()
        current = selected.get(key)
        if current is None or len(str(row.get("summary") or "")) > len(str(current.get("summary") or "")):
            selected[key] = row
    return sorted(
        selected.values(),
        key=lambda row: _parse_datetime(row.get("published_at")) or datetime.min.replace(tzinfo=CN_TZ),
        reverse=True,
    )


def _aggregate_source_rows(rows: list[dict]) -> list[dict]:
    aggregated: dict[str, dict] = {}
    for row in rows:
        source = str(row.get("source") or "unknown")
        item = aggregated.setdefault(
            source,
            {
                "source": source,
                "ok": False,
                "rows": 0,
                "attempts": 0,
                "error": "",
                "fetched_at": row.get("fetched_at", ""),
            },
        )
        item["attempts"] += 1
        item["ok"] = bool(item["ok"] or row.get("ok"))
        item["rows"] += int(row.get("rows") or 0)
        if row.get("error") and not row.get("ok"):
            errors = [part for part in str(item.get("error") or "").split(" | ") if part]
            if str(row["error"]) not in errors:
                errors.append(str(row["error"]))
            item["error"] = " | ".join(errors[:3])
        item["fetched_at"] = row.get("fetched_at") or item["fetched_at"]
    return list(aggregated.values())


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
