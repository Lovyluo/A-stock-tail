from __future__ import annotations

import csv
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable
import urllib.request

import requests


CATALOG_FIELDS = ("code", "name", "market", "source", "updated_at")
DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent / "cache" / "stock_catalog.csv"
SINA_STOCK_LIST_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
EASTMONEY_STOCK_LIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def normalize_stock_code(value: Any) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def catalog_path_from_config(config: dict | None = None) -> Path:
    configured = (config or {}).get("paths", {}).get("stock_catalog_path")
    return Path(configured) if configured else DEFAULT_CATALOG_PATH


def load_stock_catalog(path: str | Path | None = None) -> dict[str, dict[str, str]]:
    catalog_path = Path(path) if path else DEFAULT_CATALOG_PATH
    if not catalog_path.is_file():
        return {}
    try:
        mtime_ns = catalog_path.stat().st_mtime_ns
        cached = _load_stock_catalog_cached(str(catalog_path.resolve()), mtime_ns)
    except (OSError, csv.Error, UnicodeError):
        return {}
    return {code: dict(row) for code, row in cached.items()}


@lru_cache(maxsize=8)
def _load_stock_catalog_cached(path: str, mtime_ns: int) -> dict[str, dict[str, str]]:
    del mtime_ns
    rows: dict[str, dict[str, str]] = {}
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            code = normalize_stock_code(raw.get("code"))
            name = str(raw.get("name") or "").strip()
            if code and name:
                rows[code] = {field: str(raw.get(field) or "").strip() for field in CATALOG_FIELDS}
                rows[code]["code"] = code
                rows[code]["name"] = name
    return rows


def resolve_stock_name(
    code: Any,
    path: str | Path | None = None,
    *,
    fetch_remote: bool = True,
    quote_fetcher: Callable[[str], dict | str | None] | None = None,
) -> str:
    normalized = normalize_stock_code(code)
    if len(normalized) != 6:
        return ""
    catalog_path = Path(path) if path else DEFAULT_CATALOG_PATH
    cached = load_stock_catalog(catalog_path).get(normalized, {})
    if cached.get("name"):
        return str(cached["name"])
    if not fetch_remote:
        return ""
    fetcher = quote_fetcher or fetch_tencent_stock_identity
    try:
        result = fetcher(normalized)
    except Exception:
        return ""
    if isinstance(result, str):
        name = result.strip()
        market = _market_for_code(normalized)
    else:
        name = str((result or {}).get("name") or "").strip()
        market = str((result or {}).get("market") or _market_for_code(normalized))
    if not name:
        return ""
    _upsert_catalog_row(
        catalog_path,
        {
            "code": normalized,
            "name": name,
            "market": market,
            "source": "tencent_quote",
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    )
    return name


def update_stock_catalog(
    path: str | Path | None = None,
    *,
    source_fetchers: list[tuple[str, Callable[[], list[dict]]]] | None = None,
    now: datetime | None = None,
) -> dict:
    catalog_path = Path(path) if path else DEFAULT_CATALOG_PATH
    fetchers = source_fetchers or [
        ("sina_market_center", fetch_sina_stock_catalog),
        ("eastmoney_clist", fetch_eastmoney_stock_catalog),
    ]
    merged: dict[str, dict[str, str]] = {}
    source_results = []
    used_sources = []
    for source_name, fetcher in fetchers:
        try:
            source_rows = _normalize_catalog_rows(fetcher(), source_name)
            source_results.append({"source": source_name, "ok": bool(source_rows), "rows": len(source_rows), "error": "" if source_rows else "empty"})
            if source_rows:
                used_sources.append(source_name)
                for row in source_rows:
                    merged.setdefault(row["code"], row)
                if len(merged) >= 1000:
                    break
        except Exception as exc:
            source_results.append({"source": source_name, "ok": False, "rows": 0, "error": f"{type(exc).__name__}: {exc}"})
    if not merged:
        return {
            "status": "STOCK_CATALOG_UPDATE_FAILED",
            "count": 0,
            "path": str(catalog_path),
            "sources": source_results,
        }
    timestamp = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    for row in merged.values():
        row["updated_at"] = timestamp
    _write_catalog(catalog_path, list(merged.values()))
    return {
        "status": "STOCK_CATALOG_READY",
        "count": len(merged),
        "path": str(catalog_path),
        "source": ",".join(used_sources),
        "updated_at": timestamp,
        "sources": source_results,
    }


def fetch_sina_stock_catalog(max_pages: int = 100, page_size: int = 100) -> list[dict]:
    session = requests.Session()
    headers = {"User-Agent": USER_AGENT, "Referer": "https://finance.sina.com.cn/"}
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        response = session.get(
            SINA_STOCK_LIST_URL,
            params={
                "page": str(page),
                "num": str(page_size),
                "sort": "symbol",
                "asc": "1",
                "node": "hs_a",
                "symbol": "",
                "_s_r_a": "page",
            },
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        page_rows = response.json()
        if not isinstance(page_rows, list) or not page_rows:
            break
        for item in page_rows:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "")
            rows.append(
                {
                    "code": item.get("code"),
                    "name": item.get("name"),
                    "market": symbol[:2].lower() if symbol[:2].lower() in {"sh", "sz", "bj"} else "",
                }
            )
        if len(page_rows) < page_size:
            break
    return rows


def fetch_eastmoney_stock_catalog(max_pages: int = 20, page_size: int = 500) -> list[dict]:
    session = requests.Session()
    headers = {"User-Agent": USER_AGENT, "Referer": "https://quote.eastmoney.com/"}
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        response = session.get(
            EASTMONEY_STOCK_LIST_URL,
            params={
                "pn": str(page),
                "pz": str(page_size),
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f3",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
                "fields": "f12,f14,f13",
            },
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        page_rows = (response.json().get("data") or {}).get("diff") or []
        for item in page_rows:
            rows.append(
                {
                    "code": item.get("f12"),
                    "name": item.get("f14"),
                    "market": _market_for_code(item.get("f12")),
                }
            )
        if len(page_rows) < page_size:
            break
    return rows


def fetch_tencent_stock_identity(code: str) -> dict:
    normalized = normalize_stock_code(code)
    market = _market_for_code(normalized)
    request = urllib.request.Request(f"https://qt.gtimg.cn/q={market}{normalized}")
    request.add_header("User-Agent", USER_AGENT)
    text = urllib.request.urlopen(request, timeout=8).read().decode("gbk", errors="ignore")
    if '"' not in text:
        return {}
    fields = text.split('"', 2)[1].split("~")
    name = fields[1].strip() if len(fields) > 1 else ""
    returned_code = normalize_stock_code(fields[2] if len(fields) > 2 else normalized)
    if returned_code != normalized or not name:
        return {}
    return {"code": normalized, "name": name, "market": market}


def _normalize_catalog_rows(rows: list[dict], source: str) -> list[dict[str, str]]:
    normalized: dict[str, dict[str, str]] = {}
    for item in rows or []:
        code = normalize_stock_code(item.get("code"))
        name = str(item.get("name") or "").strip()
        if len(code) != 6 or not name:
            continue
        normalized[code] = {
            "code": code,
            "name": name,
            "market": str(item.get("market") or _market_for_code(code)),
            "source": source,
            "updated_at": "",
        }
    return list(normalized.values())


def _upsert_catalog_row(path: Path, row: dict[str, str]) -> None:
    rows = load_stock_catalog(path)
    rows[row["code"]] = {field: str(row.get(field) or "") for field in CATALOG_FIELDS}
    _write_catalog(path, list(rows.values()))


def _write_catalog(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CATALOG_FIELDS)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: item.get("code", "")):
            writer.writerow({field: row.get(field, "") for field in CATALOG_FIELDS})
    temporary.replace(path)
    _load_stock_catalog_cached.cache_clear()


def _market_for_code(code: Any) -> str:
    normalized = normalize_stock_code(code)
    if normalized.startswith(("6", "9")):
        return "sh"
    if normalized.startswith(("4", "8")):
        return "bj"
    return "sz"
