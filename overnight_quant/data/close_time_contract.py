from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from typing import Any

from overnight_quant.data.market_calendar import CN_TZ


TIME_CONTRACT_VERSION = "close_confirmation_timeline_v2"
MINUTE_LABEL_END = "minute_end"
MINUTE_LABEL_START = "minute_start"
MINUTE_LABEL_END_PROVISIONAL = "minute_end_provisional"
MINUTE_LABEL_START_PROVISIONAL = "minute_start_provisional"
MINUTE_LABEL_UNVERIFIED = "unverified"
VALIDATED_MINUTE_LABELS = {MINUTE_LABEL_END, MINUTE_LABEL_START}
PROVISIONAL_MINUTE_LABELS = {
    MINUTE_LABEL_END_PROVISIONAL,
    MINUTE_LABEL_START_PROVISIONAL,
}


@dataclass(frozen=True)
class CloseTimeContract:
    feature_event_cutoff: str
    collection_deadline: str
    decision_time: str
    execution_not_before: str
    minute_label_semantics: str = MINUTE_LABEL_UNVERIFIED
    minute_label_validation_status: str = "PENDING_REAL_OBSERVATION"
    probe_evidence_hash: str = ""
    contract_version: str = TIME_CONTRACT_VERSION
    bar_label_time: str = ""
    interval_start: str = ""
    interval_end: str = ""
    first_observed_at: str = ""
    finalized_at: str = ""
    is_final: bool = True
    finalization_delay_ms: float = 0.0
    transaction_evidence_hash: str = ""
    combined_evidence_hash: str = ""

    def __post_init__(self) -> None:
        values = [
            _parse_datetime(getattr(self, field))
            for field in (
                "feature_event_cutoff",
                "collection_deadline",
                "decision_time",
                "execution_not_before",
            )
        ]
        if any(value is None for value in values):
            raise ValueError("close_time_contract_invalid")
        ordered = [value for value in values if value is not None]
        if ordered != sorted(ordered):
            raise ValueError("close_time_contract_order_invalid")
        if (
            self.minute_label_validation_status == "VERIFIED"
            and (
                self.minute_label_semantics
                not in VALIDATED_MINUTE_LABELS
                or not _valid_evidence_hash(self.probe_evidence_hash)
            )
        ):
            raise ValueError(
                "verified_minute_label_requires_probe_evidence_hash"
            )
        if self.minute_label_semantics in PROVISIONAL_MINUTE_LABELS:
            if (
                self.minute_label_validation_status
                != "PROVISIONAL_TRANSACTION_ATTRIBUTION"
            ):
                raise ValueError(
                    "provisional_minute_label_status_invalid"
                )
            if not self.is_final:
                raise ValueError(
                    "provisional_minute_label_requires_final_bar"
                )
            if not all(
                _valid_evidence_hash(value)
                for value in (
                    self.probe_evidence_hash,
                    self.transaction_evidence_hash,
                    self.combined_evidence_hash,
                )
            ):
                raise ValueError(
                    "provisional_minute_label_evidence_invalid"
                )
            attribution_times = [
                _parse_datetime(value)
                for value in (
                    self.bar_label_time,
                    self.interval_start,
                    self.interval_end,
                    self.first_observed_at,
                    self.finalized_at,
                )
            ]
            if any(value is None for value in attribution_times):
                raise ValueError(
                    "provisional_minute_label_times_invalid"
                )
            if float(self.finalization_delay_ms) < 0:
                raise ValueError(
                    "provisional_finalization_delay_invalid"
                )

    @property
    def minute_label_verified(self) -> bool:
        return (
            self.minute_label_semantics in VALIDATED_MINUTE_LABELS
            and self.minute_label_validation_status == "VERIFIED"
        )

    @property
    def minute_label_provisional(self) -> bool:
        return (
            self.minute_label_semantics in PROVISIONAL_MINUTE_LABELS
            and self.minute_label_validation_status
            == "PROVISIONAL_TRANSACTION_ATTRIBUTION"
            and self.is_final
        )

    @property
    def decision_eligible(self) -> bool:
        return self.minute_label_verified and self.is_final

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_close_time_contract(
    trade_date: str | date,
    *,
    minute_label_semantics: str = MINUTE_LABEL_UNVERIFIED,
    verified: bool = False,
    probe_evidence_hash: str = "",
    bar_label_time: str = "",
    interval_start: str = "",
    interval_end: str = "",
    first_observed_at: str = "",
    finalized_at: str = "",
    is_final: bool = True,
    finalization_delay_ms: float = 0.0,
    transaction_evidence_hash: str = "",
    combined_evidence_hash: str = "",
) -> CloseTimeContract:
    day = (
        trade_date
        if isinstance(trade_date, date)
        else date.fromisoformat(str(trade_date))
    )
    semantics = str(minute_label_semantics or "").strip().lower()
    if semantics not in {
        MINUTE_LABEL_END,
        MINUTE_LABEL_START,
        MINUTE_LABEL_END_PROVISIONAL,
        MINUTE_LABEL_START_PROVISIONAL,
        MINUTE_LABEL_UNVERIFIED,
    }:
        raise ValueError(f"minute_label_semantics_invalid:{semantics}")

    if semantics in {
        MINUTE_LABEL_END,
        MINUTE_LABEL_END_PROVISIONAL,
    }:
        collection_deadline = time(14, 50, 30)
        decision_time = time(14, 50, 35)
        execution_not_before = time(14, 51)
    else:
        # Unknown semantics use the conservative minute-start timeline.
        collection_deadline = time(14, 51, 5)
        decision_time = time(14, 51, 10)
        execution_not_before = time(14, 52)

    if semantics in PROVISIONAL_MINUTE_LABELS:
        validation_status = "PROVISIONAL_TRANSACTION_ATTRIBUTION"
    else:
        validation_status = (
            "VERIFIED"
            if verified and semantics in VALIDATED_MINUTE_LABELS
            else "PENDING_REAL_OBSERVATION"
        )
    if validation_status == "VERIFIED" and not _valid_evidence_hash(
        probe_evidence_hash
    ):
        raise ValueError(
            "verified_minute_label_requires_probe_evidence_hash"
        )
    return CloseTimeContract(
        feature_event_cutoff=_at(day, time(14, 50)),
        collection_deadline=_at(day, collection_deadline),
        decision_time=_at(day, decision_time),
        execution_not_before=_at(day, execution_not_before),
        minute_label_semantics=semantics,
        minute_label_validation_status=validation_status,
        probe_evidence_hash=str(probe_evidence_hash),
        bar_label_time=str(bar_label_time or ""),
        interval_start=str(interval_start or ""),
        interval_end=str(interval_end or ""),
        first_observed_at=str(first_observed_at or ""),
        finalized_at=str(finalized_at or ""),
        is_final=bool(is_final),
        finalization_delay_ms=float(finalization_delay_ms or 0.0),
        transaction_evidence_hash=str(
            transaction_evidence_hash or ""
        ),
        combined_evidence_hash=str(combined_evidence_hash or ""),
    )


def normalize_close_time_contract(
    value: dict[str, Any] | CloseTimeContract | None,
    *,
    fallback_decision_time: str | datetime | None = None,
) -> CloseTimeContract | None:
    if isinstance(value, CloseTimeContract):
        return value
    if isinstance(value, dict) and value:
        required = (
            "feature_event_cutoff",
            "collection_deadline",
            "decision_time",
            "execution_not_before",
        )
        if not all(value.get(field) for field in required):
            return None
        return CloseTimeContract(
            feature_event_cutoff=_normalize_datetime(
                value["feature_event_cutoff"]
            ),
            collection_deadline=_normalize_datetime(
                value["collection_deadline"]
            ),
            decision_time=_normalize_datetime(value["decision_time"]),
            execution_not_before=_normalize_datetime(
                value["execution_not_before"]
            ),
            minute_label_semantics=str(
                value.get("minute_label_semantics")
                or MINUTE_LABEL_UNVERIFIED
            ).lower(),
            minute_label_validation_status=str(
                value.get("minute_label_validation_status")
                or "PENDING_REAL_OBSERVATION"
            ).upper(),
            probe_evidence_hash=str(
                value.get("probe_evidence_hash") or ""
            ),
            contract_version=str(
                value.get("contract_version") or TIME_CONTRACT_VERSION
            ),
            bar_label_time=str(value.get("bar_label_time") or ""),
            interval_start=str(value.get("interval_start") or ""),
            interval_end=str(value.get("interval_end") or ""),
            first_observed_at=str(
                value.get("first_observed_at") or ""
            ),
            finalized_at=str(value.get("finalized_at") or ""),
            is_final=bool(value.get("is_final", True)),
            finalization_delay_ms=float(
                value.get("finalization_delay_ms") or 0.0
            ),
            transaction_evidence_hash=str(
                value.get("transaction_evidence_hash") or ""
            ),
            combined_evidence_hash=str(
                value.get("combined_evidence_hash") or ""
            ),
        )
    if fallback_decision_time in (None, ""):
        return None
    decision = _parse_datetime(fallback_decision_time)
    if decision is None:
        return None
    stamp = decision.isoformat(timespec="seconds")
    return CloseTimeContract(
        feature_event_cutoff=stamp,
        collection_deadline=stamp,
        decision_time=stamp,
        execution_not_before=stamp,
        minute_label_semantics="legacy_compat",
        minute_label_validation_status="LEGACY_COMPAT",
        probe_evidence_hash="",
        contract_version="legacy_single_cutoff_v1",
    )


def contract_datetimes(
    contract: CloseTimeContract,
) -> dict[str, datetime]:
    values = {
        field: _parse_datetime(getattr(contract, field))
        for field in (
            "feature_event_cutoff",
            "collection_deadline",
            "decision_time",
            "execution_not_before",
        )
    }
    if any(value is None for value in values.values()):
        raise ValueError("close_time_contract_invalid")
    return values  # type: ignore[return-value]


def _at(day: date, value: time) -> str:
    return datetime.combine(day, value, tzinfo=CN_TZ).isoformat(
        timespec="seconds"
    )


def _normalize_datetime(value: str | datetime) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        raise ValueError(f"close_time_contract_datetime_invalid:{value}")
    return parsed.isoformat(timespec="seconds")


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip().replace("Z", "+00:00")
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ)


def _valid_evidence_hash(value: str) -> bool:
    text = str(value or "").strip().lower()
    return (
        len(text) == 64
        and all(character in "0123456789abcdef" for character in text)
    )
