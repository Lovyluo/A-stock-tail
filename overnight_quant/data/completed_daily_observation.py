"""Offline, price-only observation. No source activation or trading output."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
import hashlib
import json
import math
from pathlib import Path

from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.s2_integration_contract import FIXED_CODES, security_identity
from overnight_quant.data.static_source_providers import (
    SOURCE_IDENTITIES, STATIC_SOURCE_EVIDENCE_SCHEMA_VERSION, TENCENT_KLINE_URL,
    StaticSourceContractError, StaticSourceProviders, validate_static_provider_records,
)
from overnight_quant.scripts.run_static_source_evidence_verify import (
    _ReplayClock, _ReplayTransport,
)
from overnight_quant.scripts.run_static_source_validation import compute_evidence_hash


STRATEGY = "completed_daily_price_observation_v1"
FORMULA = "r20_close_ratio_trading_calendar_v1"
MANIFEST_SCHEMA = "completed_daily_frozen_batch_v1"
RESULT_SCHEMA = "completed_daily_observation_result_v1"
CONTRACT = {
    "strategy": STRATEGY, "formula": FORMULA,
    "expression": "100 * (C_D / C_D_minus_20 - 1)",
    "codes": list(FIXED_CODES), "required_sessions": 60, "lookback_sessions": 20,
    "adjustment": "qfq", "price_unit": "CNY_per_share",
    "exclude_run_date": True, "risk_assessment": "NOT_ASSESSED",
    "source_identities": {k: SOURCE_IDENTITIES[k] for k in ("trading_calendar", "daily_bar_qfq")},
}
CONTRACT_HASH = stable_hash(CONTRACT)


def read_anchored_json(path: str | Path, expected_sha256: str) -> dict:
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("external_file_sha256_mismatch")
    value = json.loads(raw.decode("utf-8-sig"), parse_constant=_invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("input_object_required")
    return value


def _invalid_constant(value):
    raise ValueError(f"non_finite_json:{value}")


def _time(value):
    if not isinstance(value, str):
        raise ValueError("timestamp_missing")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timedelta(hours=8):
        raise ValueError("timestamp_timezone_invalid")
    return parsed


def _unique(rows):
    # Existing normalization policy: identical copies collapse; conflicts survive.
    return sorted({stable_hash(row): row for row in rows}.values(), key=stable_hash)


def _replay(evidence: dict, run_date: str):
    if evidence.get("evidence_schema_version") != STATIC_SOURCE_EVIDENCE_SCHEMA_VERSION:
        raise ValueError("source_evidence_schema_invalid")
    if evidence.get("evidence_hash") != compute_evidence_hash(evidence):
        raise ValueError("source_evidence_hash_mismatch")
    if evidence.get("trade_date") != run_date:
        raise ValueError("source_run_date_mismatch")
    if sorted(evidence.get("requested_codes") or []) != list(FIXED_CODES):
        raise ValueError("fixed_five_codes_mismatch")
    result, captures = {}, []
    calendar = []
    for capability, expected_count in (("trading_calendar", 1), ("daily_bar_qfq", 5)):
        responses = _unique((evidence.get("raw_responses") or {}).get(capability) or [])
        if len(responses) != expected_count:
            raise ValueError(f"{capability}:capture_count_invalid")
        if capability == "daily_bar_qfq":
            responses.sort(key=lambda row: dict(row["params"])["param"].split(",")[0][2:])
        for response in responses:
            material = {key: response[key] for key in ("method", "url", "params", "data")}
            if response.get("request_hash") != stable_hash(material):
                raise ValueError("request_hash_mismatch")
            if response.get("capability") != capability:
                raise ValueError("capture_capability_mismatch")
            if response["response_url"].split("?", 1)[0] != TENCENT_KLINE_URL:
                raise ValueError("capture_response_url_mismatch")
        transport = _ReplayTransport(responses)
        clock = _ReplayClock(responses)
        provider = StaticSourceProviders(
            FIXED_CODES, target_trade_date=run_date,
            feature_cutoff=evidence["feature_cutoff"], transport=transport, clock=clock,
        )
        batch = (provider.collect_trading_calendar_batch() if capability == "trading_calendar"
                 else provider.collect_qfq_daily_batch(calendar))
        if transport.responses or clock.values:
            raise ValueError("unconsumed_capture")
        replayed = _unique(batch.records)
        stored = _unique((evidence.get("records_by_capability") or {}).get(capability) or [])
        if stored != replayed:
            raise ValueError(f"{capability}:raw_record_replay_mismatch")
        validation = validate_static_provider_records(
            capability, replayed, target_trade_date=run_date,
            codes=FIXED_CODES, calendar_trade_dates=calendar,
        )
        if not validation["valid"]:
            raise ValueError(f"{capability}:{','.join(validation['errors'])}")
        result[capability] = replayed
        if capability == "trading_calendar":
            calendar = replayed[0]["payload"]["trade_dates"]
        captures.extend(responses)
    return result, captures, calendar


def observe(evidence: dict, *, source_file_sha256: str, manifest: dict | None,
            run_date: str, generated_at: str, historical_offline: bool = False,
            synthetic_test_only: bool = False) -> dict:
    """Derive from raw responses, never trust supplied scores or admission flags.

    External byte anchors are checked by the file entry point. A manifest binds
    one immutable source file, not a collection of independently selected dates.
    """
    result = {
        "result_schema_version": RESULT_SCHEMA, "strategy_id": STRATEGY, "run_date": run_date,
        "formula_version": FORMULA, "contract_hash": CONTRACT_HASH,
        "observation_input_ready": False, "risk_assessment": "NOT_ASSESSED",
        "status": "OBSERVATION_REJECTED", "errors": [], "observation_ranking": [],
        "qualification": "0/3", "data_ready": False, "hard_gate_authorized": False,
        "records": [], "candidates": [], "tickets": [], "orders": [],
        "synthetic_test_only": synthetic_test_only,
        "historical_offline": historical_offline, "source_file_sha256": source_file_sha256,
        "disclosures": [
            "Internal historical price observation; not a buy recommendation or probability.",
            "QFQ close ratio is not dividend-inclusive total return; historical QFQ may be revised.",
            "15:00 event markers are completed-day attribution, not vendor publication/finality.",
            "Announcements, suspension/ST/delisting, limits and tradability are NOT_ASSESSED.",
            "Fixed five-stock coverage is not whole-market coverage or continuing availability.",
        ],
        "audit": {"generated_at": generated_at},
    }
    try:
        day = date.fromisoformat(run_date)
        now = _time(generated_at)
        if evidence.get("synthetic_test_only") is True and not synthetic_test_only:
            raise ValueError("synthetic_test_only_isolation")
        if not historical_offline and day != now.date():
            raise ValueError("historical_input_requires_explicit_mode")
        records, captures, calendar = _replay(evidence, run_date)
        result["audit"]["component_replay_passed"] = True
        if manifest is None:
            raise ValueError("frozen_batch_manifest_missing")
        if manifest.get("schema_version") != MANIFEST_SCHEMA:
            raise ValueError("manifest_schema_invalid")
        if manifest.get("contract_hash") != CONTRACT_HASH:
            raise ValueError("observation_contract_hash_mismatch")
        kind = manifest.get("provenance_kind")
        if kind not in {"frozen_capture", "historical_offline", "synthetic_test_only"}:
            raise ValueError("provenance_kind_invalid")
        if (kind == "synthetic_test_only") != synthetic_test_only:
            raise ValueError("synthetic_test_only_isolation")
        if kind == "historical_offline" and not historical_offline:
            raise ValueError("historical_input_requires_explicit_mode")
        batch_id = manifest.get("batch_id")
        if not isinstance(batch_id, str) or not batch_id.strip():
            raise ValueError("batch_identity_missing")
        if manifest.get("run_date") != run_date:
            raise ValueError("manifest_run_date_mismatch")
        if manifest.get("source_file_sha256") != source_file_sha256:
            raise ValueError("manifest_source_anchor_mismatch")
        frozen = _time(manifest.get("frozen_at"))
        if frozen.date() != day or frozen > now:
            raise ValueError("freeze_time_invalid")
        expected_bindings = []
        for response in captures:
            started, completed = _time(response["observed_at"]), _time(response["available_at"])
            if not started.date() == completed.date() == day or not started <= completed <= frozen:
                raise ValueError("capture_available_or_date_invalid")
            expected_bindings.append({
                "batch_id": batch_id, "capability": response["capability"],
                "request_hash": response["request_hash"], "raw_hash": response["raw_hash"],
                "source_version": SOURCE_IDENTITIES[response["capability"]][2],
            })
        if _unique(manifest.get("capture_bindings") or []) != _unique(expected_bindings):
            raise ValueError("batch_capture_or_version_mismatch")
        required = calendar[-60:]
        if len(required) != 60 or required[-1] >= run_date:
            raise ValueError("completed_calendar_invalid")
        ranking, computation = [], []
        for code in FIXED_CODES:
            rows = sorted((r for r in records["daily_bar_qfq"] if r["payload"]["code"] == code),
                          key=lambda r: r["payload"]["date"])
            if [r["payload"]["date"] for r in rows] != required:
                raise ValueError(f"{code}:latest_60_calendar_sessions_missing")
            prices = []
            for row in rows:
                price = row["payload"]["close"]
                if type(price) not in (float, int) or not math.isfinite(price) or price <= 0:
                    raise ValueError("close_price_invalid")
                prices.append(price)
            with localcontext() as context:
                context.prec = 34
                r20 = float(100 * (Decimal(str(prices[-1])) / Decimal(str(prices[-21])) - 1))
            if not math.isfinite(r20):
                raise ValueError("r20_non_finite")
            refs = [{k: r[k] for k in ("event_time", "observed_at", "available_at", "raw_hash",
                                      "request_hash", "source_version")} for r in (rows[-21], rows[-1])]
            ranking.append({"security_identity": security_identity(code), "code": code,
                            "start_date": required[-21], "end_date": required[-1],
                            "start_close": prices[-21], "end_close": prices[-1],
                            "R20": r20, "evidence_references": refs})
            computation.append({"identity": security_identity(code), "dates": required, "closes": prices})
        ranking.sort(key=lambda row: (-row["R20"], row["security_identity"]))
        for rank, row in enumerate(ranking, 1):
            row["rank"] = rank
        result.update(observation_input_ready=True, status="OBSERVATION_COMPUTED",
                      observation_ranking=ranking, batch_id=batch_id, completed_date=required[-1])
        # Audit anchors/times vary independently of the price decision projection.
        result["decision_hash"] = stable_hash({"contract_hash": CONTRACT_HASH,
                                               "run_date": run_date, "inputs": computation})
        result["audit"]["frozen_at"] = frozen.isoformat()
        result["audit"]["capture_bindings"] = _unique(expected_bindings)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError,
            OverflowError, StaticSourceContractError) as exc:
        result.update(observation_input_ready=False, status="OBSERVATION_REJECTED", observation_ranking=[])
        result["errors"] = [f"{type(exc).__name__}:{exc}"]
        result["decision_hash"] = stable_hash({"contract_hash": CONTRACT_HASH, "run_date": run_date,
                                               "rejected": result["errors"]})
    return result
