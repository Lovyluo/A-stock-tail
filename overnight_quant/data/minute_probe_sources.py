from __future__ import annotations

from datetime import date, datetime, time
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Callable, Iterable

from overnight_quant.data.close_time_contract import (
    CloseTimeContract,
    build_close_time_contract,
    normalize_close_time_contract,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import (
    parse_cn_datetime,
    stable_hash,
)
from overnight_quant.data.real_point_in_time_collectors import (
    RealPointInTimeCollectors,
    SourceContractError,
)
from overnight_quant.data.snapshot_store import ProviderBatch


PROBE_SOURCE_EASTMONEY = "eastmoney"
PROBE_SOURCE_MOOTDX = "mootdx"
SUPPORTED_MINUTE_PROBE_SOURCES = (
    PROBE_SOURCE_EASTMONEY,
    PROBE_SOURCE_MOOTDX,
)


def _mootdx_package_version() -> str:
    try:
        return version("mootdx")
    except PackageNotFoundError:
        return "unavailable"


def _mootdx_source_version() -> str:
    package_version = _mootdx_package_version()
    return (
        f"mootdx_{package_version}_tdx_std_bars_1m_"
        "v2026-07-31"
    )


MOOTDX_MINUTE_SOURCE_VERSION = _mootdx_source_version()
MOOTDX_TRANSACTION_SOURCE_VERSION = (
    f"mootdx_{_mootdx_package_version()}_"
    "tdx_std_transaction_v2026-08-06"
)


def build_minute_probe_collector(
    source: str,
    codes: Iterable[str],
    *,
    clock: Callable[[], datetime] | None = None,
) -> Any:
    normalized = normalize_probe_source(source)
    if normalized == PROBE_SOURCE_EASTMONEY:
        collector = RealPointInTimeCollectors(
            codes,
            clock=clock,
        )
        collector.probe_source = PROBE_SOURCE_EASTMONEY
        return collector
    return MootdxMinuteProbeCollectors(
        codes,
        clock=clock,
    )


def normalize_probe_source(source: str) -> str:
    normalized = str(source or "").strip().lower()
    if normalized not in SUPPORTED_MINUTE_PROBE_SOURCES:
        raise ValueError(
            "minute_probe_source_unsupported:"
            f"{normalized or '<empty>'}"
        )
    return normalized


class MootdxMinuteProbeCollectors:
    probe_source = PROBE_SOURCE_MOOTDX
    source_version = MOOTDX_MINUTE_SOURCE_VERSION

    def __init__(
        self,
        codes: Iterable[str],
        *,
        clock: Callable[[], datetime] | None = None,
        client_factory: Callable[[], Any] | None = None,
        time_contract: dict[str, Any] | CloseTimeContract | None = None,
    ):
        self.codes = _normalize_codes(codes)
        self.clock = clock or (lambda: datetime.now(CN_TZ))
        self.client_factory = client_factory or _default_mootdx_client
        self.time_contract = normalize_close_time_contract(time_contract)
        self._client: Any | None = None

    def collect_minute_bars(
        self,
        observed_at: datetime,
    ) -> ProviderBatch:
        if not self.codes:
            raise SourceContractError("target_codes_missing")
        client = self._get_client()
        records = []
        raw_hashes = []
        for code in self.codes:
            frame = client.bars(
                symbol=code,
                frequency="1m",
                start=0,
                offset=800,
            )
            if frame is None or getattr(frame, "empty", True):
                raise SourceContractError(
                    f"mootdx_minute_bar_empty:{code}"
                )
            normalized_rows = _normalize_mootdx_rows(
                frame,
                code=code,
                trade_date=observed_at.date().isoformat(),
            )
            if not normalized_rows:
                raise SourceContractError(
                    f"mootdx_trade_date_rows_empty:{code}"
                )
            completed_at = _as_cn(self.clock())
            response_hash = stable_hash(normalized_rows)
            raw_hashes.append(response_hash)
            for row in normalized_rows:
                records.append(
                    self._record(
                        row,
                        observed_at=observed_at,
                        available_at=completed_at,
                        raw_hash=response_hash,
                    )
                )
        return ProviderBatch(
            records=records,
            data_types=["minute_bar"],
            source_version=self.source_version,
            raw_hash=stable_hash(sorted(raw_hashes)),
        )

    def collect_transaction_evidence(
        self,
        observed_at: datetime,
        *,
        page_size: int = 800,
        max_pages: int = 20,
    ) -> dict[str, Any]:
        if not self.codes:
            raise SourceContractError("target_codes_missing")
        client = self._get_client()
        started_at = _as_cn(self.clock())
        by_code: dict[str, dict[str, Any]] = {}
        for code in self.codes:
            rows = []
            raw_hashes = []
            page_count = 0
            error = ""
            seen_rows = set()
            for page_index in range(max_pages):
                start = page_index * page_size
                try:
                    frame = client.transaction(
                        symbol=code,
                        start=start,
                        offset=page_size,
                    )
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    break
                page_count += 1
                if frame is None or getattr(frame, "empty", True):
                    break
                page_rows = _normalize_mootdx_transactions(
                    frame,
                    code=code,
                    trade_date=observed_at.date(),
                    source_offset=start,
                )
                if not page_rows:
                    break
                page_hash = stable_hash(page_rows)
                raw_hashes.append(page_hash)
                new_rows = []
                for row in page_rows:
                    identity = stable_hash(row)
                    if identity in seen_rows:
                        continue
                    seen_rows.add(identity)
                    rows.append(row)
                    new_rows.append(row)
                if not new_rows or _covers_attribution_window(rows):
                    break
            rows.sort(
                key=lambda row: (
                    row["event_time"],
                    int(row["source_position"]),
                )
            )
            completed_at = _as_cn(self.clock())
            by_code[code] = {
                "source": self.probe_source,
                "source_version": MOOTDX_TRANSACTION_SOURCE_VERSION,
                "source_volume_unit": "lot",
                "observed_at": _as_cn(observed_at).isoformat(
                    timespec="milliseconds"
                ),
                "available_at": completed_at.isoformat(
                    timespec="milliseconds"
                ),
                "timestamp_precision": _timestamp_precision(rows),
                "volume_unit": "lot",
                "coverage_complete": _covers_attribution_window(rows),
                "page_count": page_count,
                "raw_response_hashes": sorted(raw_hashes),
                "records": rows,
                "error": error,
            }
        completed_at = _as_cn(self.clock())
        evidence = {
            "source": self.probe_source,
            "source_version": MOOTDX_TRANSACTION_SOURCE_VERSION,
            "source_volume_unit": "lot",
            "volume_unit": "lot",
            "trade_date": observed_at.date().isoformat(),
            "requested_codes": list(self.codes),
            "request_started_at": started_at.isoformat(
                timespec="milliseconds"
            ),
            "request_completed_at": completed_at.isoformat(
                timespec="milliseconds"
            ),
            "by_code": dict(sorted(by_code.items())),
        }
        from overnight_quant.data.transaction_attribution import (
            compute_transaction_evidence_hash,
            normalize_mootdx_transaction_evidence,
        )

        evidence = normalize_mootdx_transaction_evidence(evidence)
        evidence["transaction_evidence_hash"] = (
            compute_transaction_evidence_hash(
                evidence,
                source=self.probe_source,
            )
        )
        return evidence

    def close(self) -> None:
        if self._client is None:
            return
        close = getattr(self._client, "close", None)
        if callable(close):
            close()
        self._client = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self.client_factory()
        return self._client

    def _record(
        self,
        row: dict[str, Any],
        *,
        observed_at: datetime,
        available_at: datetime,
        raw_hash: str,
    ) -> dict[str, Any]:
        observed = _as_cn(observed_at)
        available = _as_cn(available_at)
        event = _as_cn(row["event_time"])
        contract = self.time_contract or build_close_time_contract(
            observed.date()
        )
        payload = {
            "code": row["code"],
            "open": row["open"],
            "close": row["close"],
            "high": row["high"],
            "low": row["low"],
            "volume": row["volume"],
            "amount": row["amount"],
            "field_units": {
                "price": "CNY_per_share",
                "volume": "share",
                "amount": "CNY",
            },
            "minute_label_semantics": "unverified",
            "minute_label_verified": False,
            "is_final": False,
        }
        request = {
            "symbol": row["code"],
            "frequency": "1m",
            "start": 0,
            "offset": 800,
            "market": "std",
        }
        return {
            "event_time": event.isoformat(timespec="seconds"),
            "published_at": "",
            "observed_at": observed.isoformat(timespec="seconds"),
            "available_at": available.isoformat(timespec="seconds"),
            "decision_cutoff": contract.decision_time,
            "feature_event_cutoff": contract.feature_event_cutoff,
            "collection_deadline": contract.collection_deadline,
            "decision_time": contract.decision_time,
            "execution_not_before": contract.execution_not_before,
            "time_contract_version": contract.contract_version,
            "minute_label_semantics": (
                contract.minute_label_semantics
            ),
            "minute_label_validation_status": (
                contract.minute_label_validation_status
            ),
            "source": "mootdx_tdx_std_minute",
            "source_version": self.source_version,
            "request_hash": stable_hash(request),
            "raw_hash": raw_hash,
            "data_type": "minute_bar",
            "is_final": False,
            "payload": payload,
        }


def _default_mootdx_client() -> Any:
    from mootdx.quotes import Quotes

    return Quotes.factory(market="std")


def _normalize_mootdx_rows(
    frame: Any,
    *,
    code: str,
    trade_date: str,
) -> list[dict[str, Any]]:
    rows = []
    for item in frame.to_dict(orient="records"):
        event = parse_cn_datetime(item.get("datetime"))
        if event is None or event.date().isoformat() != trade_date:
            continue
        rows.append(
            {
                "code": code,
                "event_time": event.isoformat(timespec="seconds"),
                "open": _finite_number(item.get("open")),
                "close": _finite_number(item.get("close")),
                "high": _finite_number(item.get("high")),
                "low": _finite_number(item.get("low")),
                "volume": _finite_number(
                    item.get("vol", item.get("volume"))
                ),
                "amount": _finite_number(item.get("amount")),
            }
        )
    return sorted(rows, key=lambda row: row["event_time"])


def _normalize_mootdx_transactions(
    frame: Any,
    *,
    code: str,
    trade_date: date,
    source_offset: int,
) -> list[dict[str, Any]]:
    rows = []
    for row_index, item in enumerate(
        frame.to_dict(orient="records")
    ):
        source_time_text = str(
            item.get("datetime") or item.get("time") or ""
        ).strip()
        event, precision = _transaction_event_time(
            source_time_text,
            trade_date=trade_date,
        )
        if event is None:
            continue
        rows.append(
            {
                "code": code,
                "source": PROBE_SOURCE_MOOTDX,
                "source_version": MOOTDX_TRANSACTION_SOURCE_VERSION,
                "source_volume_unit": "lot",
                "event_time": event.isoformat(timespec="seconds"),
                "source_time_text": source_time_text,
                "source_time_origin": "source",
                "timestamp_precision": precision,
                "price": _finite_number(item.get("price")),
                "volume": _finite_number(
                    item.get("vol", item.get("volume"))
                ),
                "raw_volume": _finite_number(
                    item.get("vol", item.get("volume"))
                ),
                "raw_volume_unit": "lot",
                "trade_count": int(
                    _finite_number(item.get("num", 1))
                ),
                "buy_or_sell": item.get("buyorsell"),
                "source_position": source_offset + row_index,
            }
        )
    return rows


def _transaction_event_time(
    value: Any,
    *,
    trade_date: date,
) -> tuple[datetime | None, str]:
    text = str(value or "").strip()
    if not text:
        return None, "missing"
    precision = "second" if text.count(":") >= 2 else "minute"
    if "T" not in text and "-" not in text[:10]:
        text = f"{trade_date.isoformat()}T{text}"
    event = parse_cn_datetime(text)
    if event is None or event.date() != trade_date:
        return None, precision
    return event, precision


def _timestamp_precision(rows: list[dict[str, Any]]) -> str:
    precisions = {
        str(row.get("timestamp_precision") or "missing")
        for row in rows
    }
    if precisions == {"minute"}:
        return "minute"
    if precisions == {"second"}:
        return "second"
    if not precisions or precisions == {"missing"}:
        return "unknown"
    return "mixed"


def _covers_attribution_window(
    rows: list[dict[str, Any]],
) -> bool:
    events = [
        parse_cn_datetime(row.get("event_time")) for row in rows
    ]
    events = [item for item in events if item is not None]
    if not events:
        return False
    precision = _timestamp_precision(rows)
    if precision not in {"minute", "second"}:
        return False
    day = events[0].date()
    start = datetime.combine(day, time(14, 49), tzinfo=CN_TZ)
    end = datetime.combine(
        day,
        time(14, 50, 59),
        tzinfo=CN_TZ,
    )
    minutes = {(event.hour, event.minute) for event in events}
    if not {(14, 49), (14, 50)}.issubset(minutes):
        return False
    if precision == "minute":
        before = datetime.combine(
            day,
            time(14, 48),
            tzinfo=CN_TZ,
        )
        after = datetime.combine(
            day,
            time(14, 51),
            tzinfo=CN_TZ,
        )
        return (
            all(event.second == 0 for event in events)
            and min(events) <= before
            and max(events) >= after
        )
    return min(events) <= start and max(events) >= end


def _finite_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SourceContractError(
            f"mootdx_minute_field_invalid:{value}"
        ) from exc
    if number != number or number in {float("inf"), float("-inf")}:
        raise SourceContractError(
            f"mootdx_minute_field_invalid:{value}"
        )
    return number


def _normalize_codes(codes: Iterable[str]) -> list[str]:
    return sorted(
        {
            str(code).strip().zfill(6)
            for code in codes
            if str(code).strip()
        }
    )


def _as_cn(value: str | datetime) -> datetime:
    parsed = parse_cn_datetime(value)
    if parsed is None:
        raise SourceContractError(f"datetime_invalid:{value}")
    return parsed.astimezone(CN_TZ)
