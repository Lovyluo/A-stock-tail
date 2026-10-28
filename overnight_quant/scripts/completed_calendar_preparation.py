"""One-request calendar preparation; raw sealing precedes Provider parsing."""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import inspect
import json
from pathlib import Path
import re
import time

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_go_nogo import _completed_calendar_errors
from overnight_quant.data.mootdx_node_benchmark import write_benchmark_json_atomic
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.source_capability_adapters import SourceProviderEnvelope, execute_source_adapter
from overnight_quant.data.static_source_qualification import S1_PARTIAL_QUALIFICATION_RECORD_HASH
from overnight_quant.data import static_source_providers as providers
from overnight_quant.scripts import run_static_source_validation as static_cli


SCHEMA = "completed_calendar_preparation_v1"
VERIFIER = "completed_calendar_preparation_verifier_v1"
SUCCESS = "COMPLETED_CALENDAR_CONTRACT_VALID"
FAILED = "COMPLETED_CALENDAR_PREPARATION_FAILED"
VERIFIED = "COMPLETED_CALENDAR_EVIDENCE_VERIFIED"


def _safe(value):
    return static_cli._safe({**value, "records": [], "research_ranking": [],
                             "sampling_authorized": False})


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    return write_benchmark_json_atomic(static_cli._resolve_cache_output(path), value)


def _codes(value):
    if not isinstance(value, str):
        raise ValueError("explicit_fixed_codes_required")
    codes = tuple(sorted(part.strip() for part in value.split(",")))
    if codes != static_cli.DEFAULT_CODES:
        raise ValueError("exact_fixed_five_codes_required")
    return codes


def _adapter(collect):
    return execute_source_adapter(
        "trading_calendar",
        **dict(zip(("origin_source", "adapter", "source_version"),
                   providers.SOURCE_IDENTITIES["trading_calendar"])),
        provider_envelope=SourceProviderEnvelope(
            provider_key=providers.PROVIDER_KEYS["trading_calendar"], provider_callable=collect),
        environ={},
    )


class _SealBeforeParse:
    def __init__(self, transport, directory, clock):
        self.transport, self.directory, self.clock = transport, directory, clock
        self.request_count = 0
        self.stage = "request_contract"
        self.raw_ref = None

    def request(self, method, url, *, params, data, headers, timeout_seconds):
        if (self.request_count or method != "GET" or url != providers.TENCENT_KLINE_URL
                or params != {"param": "sh000001,day,,,260,qfq"} or data is not None
                or headers != {"User-Agent": providers.USER_AGENT, "Referer": "https://gu.qq.com/"}
                or timeout_seconds != 10.0):
            raise ValueError("calendar_single_request_contract_mismatch")
        request = {"method": method, "url": url,
                   "params": sorted((str(k), str(v)) for k, v in params.items()), "data": []}
        started, tick = self.clock().isoformat(), time.monotonic()
        self.stage = "request"
        self.request_count += 1
        response = self.transport.request(method, url, params=params, data=data,
                                          headers=headers, timeout_seconds=timeout_seconds)
        completed, elapsed = self.clock().isoformat(), (time.monotonic() - tick) * 1000
        self.stage = "raw_seal"
        # Copy the returned transport bytes, not a reconstruction from parsed rows.
        raw = static_cli._serialize_response({
            **request, "request_hash": stable_hash(request), "request_headers": dict(headers),
            "timeout_seconds": timeout_seconds, "raw_bytes": response.content,
            "raw_hash": hashlib.sha256(response.content).hexdigest(),
            "http_status_code": response.status_code, "response_url": response.url,
            "response_headers": dict(response.headers),
            "request_started_at": started, "response_completed_at": completed,
            "transport_elapsed_ms": elapsed,
        })
        path = self.directory / "raw-response.json"
        _write(path, {"schema_version": SCHEMA, **raw})
        self.raw_ref = {"file": path.name, "sha256": _sha(path)}
        self.stage = "provider_parse"
        return response


def prepare_calendar(*, network, trade_date, codes, approved_sha, output, transport=None, clock=None):
    if not network:
        return _safe({"status": "CALENDAR_NETWORK_NOT_REQUESTED", "network_requests_made": 0,
                      "provider_validation_passed": False})
    now = clock or (lambda: datetime.now(CN_TZ))
    stage, directory, capture = "input_validation", None, None
    result = {"preparation_schema_version": SCHEMA, "status": FAILED,
              "provider_validation_passed": False, "network_requests_made": 0,
              "trade_date": trade_date, "producer_commit_sha": approved_sha}
    try:
        if not isinstance(trade_date, str) or date.fromisoformat(trade_date).isoformat() != trade_date:
            raise ValueError("canonical_trade_date_required")
        if now().astimezone(CN_TZ).date().isoformat() != trade_date:
            raise ValueError("target_must_be_current_cn_date")
        fixed = _codes(codes)
        if (not isinstance(approved_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", approved_sha)
                or static_cli._git_head() != approved_sha):
            raise ValueError("approved_code_sha_mismatch")
        if not output:
            raise ValueError("new_ignored_cache_output_directory_required")
        target = static_cli._resolve_cache_output(output)
        if target.exists():
            raise FileExistsError(f"calendar_output_exists:{target}")
        # Check the real validator's call shape before sending anything.
        inspect.signature(providers.validate_static_provider_records).bind(
            "trading_calendar", [], target_trade_date=trade_date, codes=fixed)
        capture = _SealBeforeParse(transport or providers.StaticUrllibTransport(), target, now)
        provider = providers.StaticSourceProviders(fixed, target_trade_date=trade_date,
            feature_cutoff=f"{trade_date}T14:50:00+08:00", transport=capture, clock=now,
            timeout_seconds=10.0)
        stage = "output_reservation"
        target.mkdir(parents=True, exist_ok=False)
        directory = target
        intent = {"preparation_schema_version": SCHEMA, "trade_date": trade_date,
                  "producer_commit_sha": approved_sha, "requested_codes": list(fixed),
                  "created_at": now().isoformat(), "maximum_requests": 1,
                  "provider_key": providers.PROVIDER_KEYS["trading_calendar"]}
        _write(directory / "intent.json", _safe(intent))
        captured, errors = {}, []

        def collect():
            try:
                captured["batch"] = provider.collect_trading_calendar_batch()
                return captured["batch"].records
            except Exception as exc:
                errors.append(exc)
                raise

        stage = "provider_capture"
        adapter = _adapter(collect)
        if errors:
            raise errors[0]
        if adapter.get("status") != "SOURCE_ADAPTER_BOUND" or not adapter.get("execution_ok"):
            raise ValueError(f"calendar_adapter_rejected:{adapter['status']}")
        batch = captured["batch"]
        stage = "business_validation"
        checked = providers.validate_static_provider_records(
            "trading_calendar", batch.records, target_trade_date=trade_date, codes=fixed)
        if not checked["valid"] or len(batch.records) != 1:
            raise ValueError(f"calendar_records_rejected:{checked['errors']}")
        record = batch.records[0]
        result = _safe({**result, "status": SUCCESS, "contract_schema_version": "completed_calendar_contract_v1",
            "target_trade_date": trade_date, "requested_codes": list(fixed),
            "source": record["origin_source"], "source_version": record["source_version"],
            "qualification_status": "qualified", "qualification_record_hash": S1_PARTIAL_QUALIFICATION_RECORD_HASH,
            **{k: record[k] for k in ("raw_hash", "request_hash", "event_time", "observed_at", "available_at")},
            **{k: record["payload"][k] for k in ("trade_dates", "latest_completed_trade_date")},
            "provider_record": record, "adapter_execution": adapter,
            "raw_responses": [static_cli._serialize_response(r) for r in batch.responses],
            "raw_capture": capture.raw_ref, "intent_sha256": _sha(directory / "intent.json"),
            "provider_verifier_contract_hash": providers.compute_static_source_verifier_contract_hash(),
            "network_requests_made": capture.request_count, "provider_validation_passed": True})
        stage = "completed_calendar_validation"
        # Check semantics now; only the independent file verifier can check the external anchor.
        errors = [error for error in _completed_calendar_errors(result, trade_date,
            expected_file_sha256=None, actual_file_sha256=None)
            if error != "calendar_external_sha256_anchor_required"]
        if errors:
            raise ValueError(f"completed_calendar_rejected:{errors}")
        stage = "contract_write"
        path = directory / "completed-calendar.json"
        _write(path, result)
        return {**result, "result_path": str(path), "file_sha256": _sha(path)}
    except Exception as exc:
        # Never leave an admissible completed-calendar payload in a failure receipt.
        result = _safe({"preparation_schema_version": SCHEMA, "trade_date": trade_date,
            "producer_commit_sha": approved_sha, "status": FAILED, "provider_validation_passed": False,
            "failure_stage": capture.stage if stage == "provider_capture" and capture else stage,
            "error_type": type(exc).__name__, "error": str(exc),
            "network_requests_made": capture.request_count if capture else 0,
            "raw_capture": capture.raw_ref if capture else None, "failure_receipt_persisted": False})
        if directory is not None:
            try:
                path = directory / "failure-receipt.json"
                result["failure_receipt_persisted"] = True
                _write(path, result)
                result.update(result_path=str(path), file_sha256=_sha(path))
            except Exception as write_error:
                result.update(failure_receipt_persisted=False,
                              receipt_write_error=f"{type(write_error).__name__}:{write_error}")
        return result


def verify_calendar_file(path, *, expected_file_sha256):
    """Offline replay through the unchanged Provider, Adapter and completed-calendar validator."""
    from overnight_quant.scripts.run_static_source_evidence_verify import _ReplayClock, _ReplayTransport
    errors, result, actual, payload = [], {}, "", {}
    try:
        path = Path(path)
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_file_sha256 or "") or actual != expected_file_sha256:
            raise ValueError("external_sha256_anchor_mismatch")
        payload = json.loads(raw)
        if payload.get("preparation_schema_version") != SCHEMA:
            raise ValueError("calendar_preparation_schema_mismatch")
        if type(payload.get("network_requests_made")) is not int or payload["network_requests_made"] != 1:
            raise ValueError("calendar_single_request_count_invalid")
        if any(payload.get(k) != v for k, v in _safe({}).items()):
            raise ValueError("calendar_safety_mismatch")
        ref = payload.get("raw_capture") or {}
        if ref.get("file") != "raw-response.json" or _sha(path.parent / ref["file"]) != ref.get("sha256"):
            raise ValueError("raw_capture_anchor_mismatch_or_missing")
        sealed = json.loads((path.parent / ref["file"]).read_bytes())
        transport = _ReplayTransport([sealed])
        # Validate raw bytes and request independently even for a legal failure receipt.
        transport.request("GET", providers.TENCENT_KLINE_URL,
            params={"param": "sh000001,day,,,260,qfq"}, data=None, headers={}, timeout_seconds=10.0)
        if payload["status"] == SUCCESS:
            if (path.parent / "failure-receipt.json").exists():
                raise ValueError("calendar_failure_receipt_present")
            if payload.get("contract_schema_version") != "completed_calendar_contract_v1":
                raise ValueError("completed_calendar_contract_schema_mismatch")
            fixed = _codes(",".join(payload["requested_codes"]))
            responses = payload["raw_responses"]
            if len(responses) != 1 or payload["network_requests_made"] != 1:
                raise ValueError("calendar_response_count_invalid")
            for key in ("method", "url", "params", "data", "request_hash", "raw_hash", "content_base64",
                        "byte_count", "http_status_code", "response_url"):
                if responses[0].get(key) != sealed.get(key):
                    raise ValueError(f"sealed_response_mismatch:{key}")
            if sealed.get("schema_version") != SCHEMA:
                raise ValueError("raw_capture_schema_mismatch")
            if payload["provider_verifier_contract_hash"] != providers.compute_static_source_verifier_contract_hash():
                raise ValueError("calendar_provider_contract_changed")
            if _sha(path.parent / "intent.json") != payload["intent_sha256"]:
                raise ValueError("intent_anchor_mismatch")
            intent = json.loads((path.parent / "intent.json").read_bytes())
            if (intent.get("maximum_requests") != 1 or intent.get("preparation_schema_version") != SCHEMA
                    or intent.get("provider_key") != providers.PROVIDER_KEYS["trading_calendar"]):
                raise ValueError("intent_contract_mismatch")
            for key in ("trade_date", "producer_commit_sha", "requested_codes"):
                if intent[key] != payload[key]:
                    raise ValueError(f"intent_identity_mismatch:{key}")
            replay = _ReplayTransport(responses)
            provider = providers.StaticSourceProviders(fixed, target_trade_date=payload["trade_date"],
                feature_cutoff=f"{payload['trade_date']}T14:50:00+08:00", transport=replay,
                clock=_ReplayClock(responses))
            batch = provider.collect_trading_calendar_batch()
            checked = providers.validate_static_provider_records("trading_calendar", batch.records,
                target_trade_date=payload["trade_date"], codes=fixed)
            if not checked["valid"] or list(batch.records) != [payload["provider_record"]] or replay.responses:
                raise ValueError("calendar_record_replay_mismatch")
            if _adapter(lambda: batch.records) != payload["adapter_execution"]:
                raise ValueError("calendar_adapter_replay_mismatch")
            record = batch.records[0]
            expected = {**record["payload"], **{k: record[k] for k in
                ("event_time", "observed_at", "available_at", "raw_hash", "request_hash")}}
            for key in ("trade_dates", "latest_completed_trade_date", "event_time", "observed_at",
                        "available_at", "raw_hash", "request_hash"):
                if payload[key] != expected[key]:
                    raise ValueError(f"calendar_contract_replay_mismatch:{key}")
            if payload.get("target_trade_date") != payload["trade_date"] or not payload["provider_validation_passed"]:
                raise ValueError("completed_calendar_status_mismatch")
            errors.extend(_completed_calendar_errors(payload, payload["trade_date"],
                expected_file_sha256=expected_file_sha256, actual_file_sha256=actual))
        elif payload["status"] != FAILED or payload.get("provider_validation_passed") is not False:
            raise ValueError("calendar_result_status_invalid")
    except Exception as exc:
        errors.append(f"{type(exc).__name__}:{exc}")
    passed = not errors and payload.get("status") == SUCCESS
    result = _safe({"status": VERIFIED if not errors else "COMPLETED_CALENDAR_EVIDENCE_INVALID",
        "verifier": VERIFIER, "evidence_integrity_verified": not errors,
        "provider_validation_passed": passed, "preparation_result": payload.get("status", FAILED),
        "expected_file_sha256": expected_file_sha256, "actual_file_sha256": actual, "errors": errors})
    return {**result, "replay_hash": stable_hash(result)}
