from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, Iterable

from overnight_quant.data.market_calendar import CN_TZ


FORMAL_MODES = {"live", "shadow", "paper", "replay"}
REQUIRED_TEMPORAL_FIELDS = (
    "event_time",
    "published_at",
    "observed_at",
    "available_at",
    "decision_cutoff",
    "source",
    "source_version",
    "request_hash",
    "raw_hash",
)


class PointInTimeError(ValueError):
    pass


@dataclass(frozen=True)
class PointInTimeRecord:
    event_time: str
    published_at: str
    observed_at: str
    available_at: str
    decision_cutoff: str
    source: str
    source_version: str
    request_hash: str
    raw_hash: str
    payload: dict[str, Any]
    data_type: str = "market"

    def available_for(self, decision_time: str | datetime) -> bool:
        accepted, _ = records_available_at(
            [self],
            decision_time,
            require_published_at_for_news=True,
        )
        return bool(accepted)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_point_in_time_record(
    payload: dict[str, Any],
    *,
    event_time: str | datetime,
    observed_at: str | datetime,
    available_at: str | datetime,
    decision_cutoff: str | datetime,
    source: str,
    source_version: str,
    published_at: str | datetime | None = None,
    request: Any = None,
    raw: Any = None,
    data_type: str = "market",
) -> PointInTimeRecord:
    published = normalize_datetime_text(published_at)
    return PointInTimeRecord(
        event_time=normalize_datetime_text(event_time),
        published_at=published,
        observed_at=normalize_datetime_text(observed_at),
        available_at=normalize_datetime_text(available_at),
        decision_cutoff=normalize_datetime_text(decision_cutoff),
        source=str(source or "").strip(),
        source_version=str(source_version or "").strip(),
        request_hash=stable_hash(request if request is not None else {}),
        raw_hash=stable_hash(raw if raw is not None else payload),
        payload=dict(payload),
        data_type=str(data_type or "market"),
    )


def records_available_at(
    records: Iterable[PointInTimeRecord | dict[str, Any]],
    decision_time: str | datetime,
    *,
    require_published_at_for_news: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    decision = parse_cn_datetime(decision_time)
    if decision is None:
        raise PointInTimeError("decision_time_invalid")
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in records:
        row = item.as_dict() if isinstance(item, PointInTimeRecord) else dict(item)
        missing = [field for field in REQUIRED_TEMPORAL_FIELDS if field not in row]
        reason = ""
        if missing:
            reason = "temporal_contract_missing:" + ",".join(missing)
        elif require_published_at_for_news and row.get("data_type") == "news" and not row.get("published_at"):
            reason = "news_published_at_missing"
        else:
            event = parse_cn_datetime(row.get("event_time"))
            observed = parse_cn_datetime(row.get("observed_at"))
            available = parse_cn_datetime(row.get("available_at"))
            cutoff = parse_cn_datetime(row.get("decision_cutoff"))
            published = parse_cn_datetime(row.get("published_at")) if row.get("published_at") else None
            if event is None or observed is None or available is None or cutoff is None:
                reason = "temporal_contract_invalid"
            elif event > decision:
                reason = "event_after_decision"
            elif observed > decision:
                reason = "observed_after_decision"
            elif available > decision:
                reason = "available_after_decision"
            elif available < observed:
                reason = "available_before_observed"
            elif decision > cutoff:
                reason = "decision_after_cutoff"
            elif row.get("data_type") == "news" and published and published > decision:
                reason = "news_published_after_decision"
        if reason:
            row["pit_reject_reason"] = reason
            rejected.append(row)
        else:
            accepted.append(row)
    return accepted, rejected


def demo_field_paths(value: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            key_lower = str(key).lower()
            if "demo" in key_lower and item not in (None, "", 0, False, [], {}):
                paths.append(path)
            paths.extend(demo_field_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(demo_field_paths(item, f"{prefix}[{index}]"))
    elif isinstance(value, str) and "demo" in value.lower():
        paths.append(prefix or "$")
    return sorted(set(paths))


def enforce_formal_no_demo(result: dict[str, Any], mode: str) -> dict[str, Any]:
    guarded = dict(result)
    paths = demo_field_paths(guarded) if str(mode).lower() in FORMAL_MODES else []
    guarded["demo_field_count"] = len(paths)
    guarded["demo_field_paths"] = paths
    fallback_status = str(guarded.get("status") or "").upper() == "DATA_FALLBACK_DEMO"
    if paths or fallback_status:
        guarded["status"] = "FORMAL_DATA_REJECTED"
        guarded["selected"] = []
        guarded["shadow_candidates"] = []
        guarded["paper_intents"] = []
        guarded["tickets"] = []
        guarded["orders"] = []
        guarded["formal_reject_reason"] = "demo_data_prohibited"
    return guarded


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_datetime_text(value: str | datetime | None) -> str:
    if value in (None, ""):
        return ""
    parsed = parse_cn_datetime(value)
    if parsed is None:
        raise PointInTimeError(f"datetime_invalid:{value}")
    return parsed.isoformat(timespec="seconds")


def parse_cn_datetime(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip().replace("Z", "+00:00")
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ)
