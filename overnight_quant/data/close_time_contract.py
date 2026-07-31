from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from typing import Any

from overnight_quant.data.market_calendar import CN_TZ


TIME_CONTRACT_VERSION = "close_confirmation_timeline_v2"
MINUTE_LABEL_END = "minute_end"
MINUTE_LABEL_START = "minute_start"
MINUTE_LABEL_UNVERIFIED = "unverified"
VALIDATED_MINUTE_LABELS = {MINUTE_LABEL_END, MINUTE_LABEL_START}


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

    @property
    def minute_label_verified(self) -> bool:
        return (
            self.minute_label_semantics in VALIDATED_MINUTE_LABELS
            and self.minute_label_validation_status == "VERIFIED"
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_close_time_contract(
    trade_date: str | date,
    *,
    minute_label_semantics: str = MINUTE_LABEL_UNVERIFIED,
    verified: bool = False,
    probe_evidence_hash: str = "",
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
        MINUTE_LABEL_UNVERIFIED,
    }:
        raise ValueError(f"minute_label_semantics_invalid:{semantics}")

    if semantics == MINUTE_LABEL_END:
        collection_deadline = time(14, 50, 30)
        decision_time = time(14, 50, 35)
        execution_not_before = time(14, 51)
    else:
        # Unknown semantics use the conservative minute-start timeline.
        collection_deadline = time(14, 51, 5)
        decision_time = time(14, 51, 10)
        execution_not_before = time(14, 52)

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
