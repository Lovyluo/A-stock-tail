from __future__ import annotations

from datetime import datetime
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


def _mootdx_source_version() -> str:
    try:
        package_version = version("mootdx")
    except PackageNotFoundError:
        package_version = "unavailable"
    return (
        f"mootdx_{package_version}_tdx_std_bars_1m_"
        "v2026-07-31"
    )


MOOTDX_MINUTE_SOURCE_VERSION = _mootdx_source_version()


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
