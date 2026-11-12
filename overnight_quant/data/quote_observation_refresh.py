"""Single-click, quote-only observation; no strategy or production records."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid
import re

from overnight_quant.data import offline_price_observation as observation
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.probe_worker_process import run_probe_worker_process
from overnight_quant.data.source_capability_adapters import (
    SOURCE_ADAPTER_BOUND, SourceProviderEnvelope, execute_source_adapter,
)
from overnight_quant.data.tencent_direct_http_providers import (
    TENCENT_ADAPTER, TENCENT_ORIGIN_SOURCE, TENCENT_SOURCE_VERSION,
    TENCENT_QUOTE_PROVIDER_KEY, TencentDirectHttpProviders, TencentHttpResponse,
    build_tencent_request_url,
)

VERSION = "manual_quote_observation_v1"
SELECTION_VERSION = "manual_quote_selection_v2"
REFRESH_CONTRACTS = (VERSION, SELECTION_VERSION)
CODES = observation.INITIAL_POOL
IDENTITIES = frozenset(observation.normalize_pool(CODES))
NETWORK_SWITCH = "OBSERVATION_QUOTE_NETWORK_ENABLED"
HTTP_SECONDS = 5
TOTAL_SECONDS = 10  # v3 execution budget only; confirmation persistence is separate.
INTERVAL_SECONDS = 10
AGE_SECONDS = 60
FINALIZATION_VERSION = "manual_quote_refresh_confirmation_v3"
EXECUTION_CANDIDATE_VERSION = "manual_quote_refresh_execution_candidate_v3"


def utc_now():
    return datetime.now(timezone.utc)


def safe(status, *, contract=VERSION, **values):
    return dict(status=status, contract=contract, risk_assessment="NOT_ASSESSED",
                tradability="NOT_ASSESSED", usage_basis="UNKNOWN", qualification="0/3",
                data_ready=False, hard_gate_authorized=False, records=[], candidates=[],
                tickets=[], orders=[], **values)


def selection_supported(identity):
    return isinstance(identity, str) and bool(re.fullmatch(
        r"(?:SH:(?:600|601|603|605|688)|SZ:(?:000|001|002|003|300|301))[0-9]{3}", identity))


def normalize_selection(values):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 5:
        raise ValueError("quote_selection_requires_one_to_five")
    if not all(selection_supported(identity) for identity in values):
        raise ValueError("quote_selection_identity_unsupported")
    if len(set(values)) != len(values):
        raise ValueError("quote_selection_duplicate")
    return sorted(values)


def refresh_request_codes(material):
    contract = material.get("contract")
    if contract == VERSION and tuple(material.get("codes", [])) == CODES:
        return CODES
    if contract == SELECTION_VERSION:
        if material.get("synthetic_test_only") is not True:
            raise ValueError("quote_selection_test_only")
        selection = normalize_selection(material.get("selection"))
        codes = sorted(identity.split(":")[1] for identity in selection)
        if material["selection"] == selection and material.get("codes") == codes:
            return tuple(codes)
    raise ValueError("refresh_contract_or_codes_invalid")


def _verify_selection_binding(envelope, intent):
    if (refresh_request_codes(envelope) != refresh_request_codes(intent)
            or any(envelope.get(key) != intent.get(key)
                   for key in ("contract", "batch_id", "synthetic_test_only", "selection"))):
        raise ValueError("quote_selection_intent_mismatch")


def _duration_ms(start, end):
    if (type(start) not in (int, float) or type(end) not in (int, float)
            or not math.isfinite(start) or not math.isfinite(end) or end < start):
        return None
    value = (end - start) * 1000
    return value if math.isfinite(value) else None


def _execution_candidate(root, batch, response_sha256, contract=VERSION):
    """Validate published candidate data, never infer execution completion from it."""
    directory = Path(root) / contract / "attempts" / batch
    commit_bytes = (directory / "final-commit.json").read_bytes()
    commit = observation._json(commit_bytes)
    final_bytes = (directory / "final-result.json").read_bytes()
    receipt_bytes = (directory / "receipt.json").read_bytes()
    final = observation._json(final_bytes)
    receipt = observation._json(receipt_bytes)
    if (commit.get("schema") != EXECUTION_CANDIDATE_VERSION or commit.get("batch_id") != batch
                or commit.get("status") != "CANDIDATE_SUCCEEDED"
                or commit.get("final_result_sha256") != hashlib.sha256(final_bytes).hexdigest()
                or commit.get("candidate_receipt_sha256") != hashlib.sha256(receipt_bytes).hexdigest()
                or commit.get("response_sha256") != response_sha256
                or receipt.get("status") != "CANDIDATE_SUCCEEDED"
                or receipt.get("batch_id") != batch or receipt.get("cleanup_complete") is not True
                or final.get("status") != "CANDIDATE_SUCCEEDED" or final.get("batch_id") != batch
                or final.get("cleanup_complete") is not True):
        raise ValueError("execution_candidate_identity_invalid")
    stages = [commit.get(key) for key in ("started_monotonic", "receipt_completed_monotonic",
                  "unlock_started_monotonic", "unlock_completed_monotonic", "final_write_started_monotonic",
                  "final_write_completed_monotonic", "commit_prepared_monotonic")]
    if (any(type(value) not in (int, float) or not math.isfinite(value) for value in stages)
                or stages != sorted(stages) or not 0 <= stages[-1] - stages[0] < TOTAL_SECONDS):
        raise ValueError("execution_candidate_timeline_invalid")
    if final.get("elapsed_before_final_write_ms") != (stages[4] - stages[0]) * 1000:
        raise ValueError("execution_candidate_elapsed_invalid")
    stored = observation._json((Path(root) / contract / "snapshots" / (stable_hash(batch) + ".json")).read_bytes())
    if stored.get("sha256") != response_sha256 or stable_hash(stored.get("response")) != response_sha256:
        raise ValueError("sealed_observation_data_mismatch")
    if contract == SELECTION_VERSION:
        intent = observation._json((directory / "intent.json").read_bytes())
        dispatched = observation._json((Path(root) / VERSION / "control" / f"attempt-{batch}.json").read_bytes())
        response = observation._json((directory / "response.json").read_bytes())
        if (intent != dispatched or commit.get("intent_sha256") != stable_hash(intent)
                or stored["response"] != response or response.get("contract") != contract
                or response.get("batch_id") != batch or final.get("contract") != contract
                or final.get("selection") != intent.get("selection")):
            raise ValueError("sealed_selection_binding_invalid")
        _verify_selection_binding(response, intent)
    return final, commit, hashlib.sha256(commit_bytes).hexdigest()


def committed_refresh_outcome(root, batch, response_sha256, *, contract=VERSION):
    """Only one complete v3 confirmation admits success; candidate publication cannot."""
    if contract not in REFRESH_CONTRACTS:
        raise ValueError("refresh_contract_invalid")
    directory = Path(root) / contract / "attempts" / batch
    unknown = safe("UNCONFIRMED", contract=contract, batch_id=batch, error="completion_unconfirmed",
                   execution_result="UNKNOWN", result_committed=False, caller_received_result="UNKNOWN",
                   execution_elapsed_ms=None, confirmation_elapsed_ms=None, total_elapsed_ms=None)
    try:
        confirmation = observation._json((directory / "final-confirmation.json").read_bytes())
        final, commit, commit_sha = _execution_candidate(root, batch, response_sha256, contract)
        start = confirmation.get("execution_started_monotonic")
        end = confirmation.get("execution_completed_monotonic")
        confirm_start = confirmation.get("confirmation_started_monotonic")
        if (confirmation.get("schema") != FINALIZATION_VERSION or confirmation.get("batch_id") != batch
                or confirmation.get("response_sha256") != response_sha256
                or confirmation.get("candidate_commit_sha256") != commit_sha
                or confirmation.get("execution_result") != "EXECUTION_SUCCEEDED"
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       for value in (start, end, confirm_start))
                or start != commit["started_monotonic"]
                or not commit["commit_prepared_monotonic"] <= end <= confirm_start
                or not 0 <= end - start < TOTAL_SECONDS
                or type(confirmation.get("execution_elapsed_ms")) not in (int, float)
                or confirmation.get("execution_elapsed_ms") != (end - start) * 1000):
            return unknown
        # Confirmation publication is the durable boundary. Later page/process exits
        # or failure diagnostics do not revoke a fully confirmed execution.
        return {**final, "status": "SUCCEEDED", "execution_result": "EXECUTION_SUCCEEDED",
                "result_committed": True, "caller_received_result": "UNKNOWN",
                "execution_elapsed_ms": confirmation["execution_elapsed_ms"],
                "confirmation_elapsed_ms": None, "total_elapsed_ms": None}
    except (OSError, ValueError, KeyError, TypeError):
        try:
            failure = observation._json((directory / "final-failure.json").read_bytes())
            if failure.get("batch_id") == batch and failure.get("status") in ("FAILED", "REJECTED", "UNCONFIRMED"):
                return failure
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return unknown


def _finish_refresh(store, target, result, *, started, hard, owned, cleaned, dispatched, published):
    """PM v3 boundary: execution <10s; persist its observed result separately."""
    batch = target.name if target is not None else None
    contract = result["contract"]
    state = store.root / VERSION / "control"
    lock = state / "active.lock"
    stage = "candidate_receipt"
    execution_completed = confirmation_started = None
    response_sha = None
    try:
        if target is not None:
            result.update(batch_id=batch, finalization_contract=FINALIZATION_VERSION)
            candidate = dict(result)
            observation.write_once(target / "receipt.json", candidate)
            receipt_completed = time.perf_counter()
            if receipt_completed >= hard:
                raise TimeoutError("candidate_receipt_budget_exhausted")
        if owned and cleaned and not (published and result["status"] != "CANDIDATE_SUCCEEDED"):
            if dispatched:
                observation.write_once(state / ("release-" + batch + ".json"),
                                       {"dispatch_monotonic": time.monotonic()})
            if published:
                # Never delete this record: incomplete publication blocks a later refresh.
                observation.write_once(state / ("pending-finalization-" + batch + ".json"),
                                       {"batch_id": batch, "contract": contract, "response_sha256": stable_hash(
                                           observation._json((target / "response.json").read_bytes()))})
            stage = "unlock"
            unlock_started = time.perf_counter()
            if unlock_started >= hard:
                raise TimeoutError("unlock_budget_exhausted")
            lock.unlink()
            unlock_completed = time.perf_counter()
            if unlock_completed >= hard:
                raise TimeoutError("unlock_budget_exhausted")
        if result["status"] != "CANDIDATE_SUCCEEDED":
            observed = time.perf_counter()
            result.update(execution_result="EXECUTION_FAILED" if dispatched else "NOT_EXECUTED",
                          result_committed=False, caller_received_result="UNKNOWN",
                          execution_elapsed_ms=(observed - started) * 1000,
                          confirmation_elapsed_ms=None, total_elapsed_ms=None)
            if target is not None:
                observation.write_once(target / "final-failure.json", result)
            finished = time.perf_counter()
            return {**result, "total_elapsed_ms": (finished - started) * 1000,
                    "elapsed_ms": (finished - started) * 1000}
        stage = "final_result"
        final_started = time.perf_counter()
        if final_started >= hard:
            raise TimeoutError("final_result_budget_exhausted")
        final = {**result, "status": "CANDIDATE_SUCCEEDED",
                 "elapsed_before_final_write_ms": (final_started - started) * 1000}
        final.pop("elapsed_ms", None)
        observation.write_once(target / "final-result.json", final)
        final_bytes = (target / "final-result.json").read_bytes()
        if observation._json(final_bytes) != final:
            raise ValueError("final_result_readback_mismatch")
        final_completed = time.perf_counter()
        if final_completed >= hard:
            raise TimeoutError("final_result_budget_exhausted")
        response = observation._json((target / "response.json").read_bytes())
        commit = dict(schema=EXECUTION_CANDIDATE_VERSION, batch_id=batch, status="CANDIDATE_SUCCEEDED",
            response_sha256=stable_hash(response), final_result_sha256=hashlib.sha256(final_bytes).hexdigest(),
            candidate_receipt_sha256=hashlib.sha256((target / "receipt.json").read_bytes()).hexdigest(),
            started_monotonic=started, receipt_completed_monotonic=receipt_completed,
            unlock_started_monotonic=unlock_started, unlock_completed_monotonic=unlock_completed,
            final_write_started_monotonic=final_started, final_write_completed_monotonic=final_completed,
            commit_prepared_monotonic=time.perf_counter())
        if contract == SELECTION_VERSION:
            commit["intent_sha256"] = stable_hash(observation._json((target / "intent.json").read_bytes()))
        stage = "final_commit"
        # Stage/fsync/read back privately; incomplete writes are never a public commit.
        staged = target / "final-commit-candidate.json"
        observation.write_once(staged, commit)
        if observation._json(staged.read_bytes()) != commit:
            raise ValueError("final_commit_readback_mismatch")
        if time.perf_counter() >= hard:
            raise TimeoutError("final_commit_budget_exhausted")
        os.link(staged, target / "final-commit.json")  # Atomic, same-volume, no replacement.
        stage = "post_publication_execution_check"
        response_sha = stable_hash(response)
        _, _, candidate_sha = _execution_candidate(store.root, batch, response_sha, contract)
        if lock.exists():
            raise ValueError("execution_unlock_unconfirmed")
        observed = time.perf_counter()
        if not math.isfinite(observed) or observed < started or observed >= hard:
            raise TimeoutError("execution_completion_budget_exhausted")
        execution_completed = observed  # Only now may EXECUTION_SUCCEEDED exist.

        stage = "confirmation_persistence"
        confirmation_started = time.perf_counter()
        if not math.isfinite(confirmation_started) or confirmation_started < execution_completed:
            raise ValueError("confirmation_clock_invalid")
        confirmation = dict(schema=FINALIZATION_VERSION, batch_id=batch,
            execution_result="EXECUTION_SUCCEEDED", response_sha256=response_sha,
            candidate_commit_sha256=candidate_sha,
            execution_started_monotonic=started, execution_completed_monotonic=execution_completed,
            execution_elapsed_ms=(execution_completed - started) * 1000,
            confirmation_started_monotonic=confirmation_started,
            confirmation_elapsed_ms=None, total_elapsed_ms=None, caller_received_result="UNKNOWN")
        # This single confirmation records an already observed result. Its own I/O
        # is separately measured, not recursively certified by another file.
        confirmation_candidate = target / "confirmation-candidate.json"
        observation.write_once(confirmation_candidate, confirmation)
        if observation._json(confirmation_candidate.read_bytes()) != confirmation:
            raise ValueError("confirmation_readback_mismatch")
        os.link(confirmation_candidate, target / "final-confirmation.json")
        persisted = committed_refresh_outcome(store.root, batch, response_sha, contract=contract)
        finished = time.perf_counter()
        if persisted["status"] != "SUCCEEDED":
            raise ValueError("confirmation_invalid")
        return {**persisted, "confirmation_elapsed_ms": _duration_ms(confirmation_started, finished),
                "total_elapsed_ms": _duration_ms(started, finished), "elapsed_ms": _duration_ms(started, finished)}
    except BaseException as exc:
        if execution_completed is not None and response_sha is not None:
            persisted = committed_refresh_outcome(store.root, batch, response_sha, contract=contract)
            if persisted["status"] == "SUCCEEDED":
                if not isinstance(exc, Exception):
                    raise  # Result committed; whether the caller received it stays unknown.
                finished = time.perf_counter()
                return {**persisted, "confirmation_elapsed_ms": _duration_ms(confirmation_started, finished),
                        "total_elapsed_ms": _duration_ms(started, finished),
                        "elapsed_ms": _duration_ms(started, finished), "return_warning": type(exc).__name__}
        result.update(status="UNCONFIRMED" if execution_completed is not None else "FAILED",
                      execution_result="EXECUTION_SUCCEEDED" if execution_completed is not None else "EXECUTION_FAILED",
                      result_committed=False, caller_received_result="UNKNOWN",
                      execution_elapsed_ms=_duration_ms(started, execution_completed if execution_completed is not None else time.perf_counter()),
                      confirmation_elapsed_ms=None, total_elapsed_ms=None,
                      error=f"{stage}:{type(exc).__name__}:{exc}",
                      elapsed_ms=_duration_ms(started, time.perf_counter()))
        if target is not None:
            try:
                observation.write_once(target / "final-failure.json", result)
            except Exception:
                # No valid terminal outcome means no success, even if failure logging failed.
                try:
                    with lock.open("x", encoding="utf-8") as output:
                        json.dump({"batch_id": batch, "pid": os.getpid()}, output)
                except OSError:
                    result["failure_record_persisted"] = False
        if not isinstance(exc, Exception):
            raise
        finished = time.perf_counter()
        return {**result,
                "confirmation_elapsed_ms": _duration_ms(confirmation_started, finished),
                "total_elapsed_ms": _duration_ms(started, finished),
                "elapsed_ms": _duration_ms(started, finished)}


def decode_response(envelope):
    """Re-run the existing Provider and quote Adapter from retained response bytes."""
    codes = refresh_request_codes(envelope)
    contract = envelope["contract"]
    if type(envelope.get("synthetic_test_only")) is not bool:
        raise ValueError("refresh_scope_missing")
    if envelope.get("source_version") != TENCENT_SOURCE_VERSION:
        raise ValueError("refresh_source_version_mismatch")
    if envelope.get("response_complete") is not True:
        raise ValueError("refresh_response_incomplete")
    if not isinstance(envelope.get("batch_id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", envelope["batch_id"]):
        raise ValueError("refresh_batch_missing")
    times = [observation._time(envelope.get(key)) for key in
             ("request_started_at", "response_completed_at", "completed_at")]
    if not times[0] <= times[1] <= times[2]:
        raise ValueError("refresh_time_order_invalid")
    raw = base64.b64decode(envelope["raw_base64"], validate=True)
    if len(raw) > 262144 or hashlib.sha256(raw).hexdigest() != envelope["raw_sha256"]:
        raise ValueError("refresh_raw_sha256_mismatch")
    expected_url = build_tencent_request_url(codes)
    if (envelope.get("request_url") != expected_url or type(envelope.get("request_count")) is not int
            or envelope["request_count"] != 1):
        raise ValueError("refresh_request_identity_invalid")
    if contract == SELECTION_VERSION and envelope.get("response_url") != expected_url:
        raise ValueError("refresh_response_request_mismatch")

    class Replay:
        def request(self, method, url, **kwargs):
            if method != "GET" or url != expected_url:
                raise ValueError("refresh_replay_request_invalid")
            return TencentHttpResponse(raw, envelope["http_status"], envelope["response_url"])

    clock = iter(times[:2])
    provider = TencentDirectHttpProviders(codes, transport=Replay(), clock=lambda: next(clock),
                                         timeout_seconds=HTTP_SECONDS)
    records = []

    def collect():
        records.extend(provider.collect_quote_records())
        return records

    if contract == VERSION:
        result = execute_source_adapter("quote", origin_source=TENCENT_ORIGIN_SOURCE,
            adapter=TENCENT_ADAPTER, source_version=TENCENT_SOURCE_VERSION,
            provider_envelope=SourceProviderEnvelope(provider_key=TENCENT_QUOTE_PROVIDER_KEY,
                                                     provider_callable=collect), environ={})
        if result.get("status") != SOURCE_ADAPTER_BOUND:
            raise ValueError("quote_adapter_rejected:" + str(result.get("status")))
    else:
        # Fixture-only candidates must not use the formal Adapter binding.
        collect()
    if len(records) != len(codes) or {r["payload"]["code"] for r in records} != set(codes):
        raise ValueError("refresh_coverage_rejected")
    rows = {}
    for record in records:
        payload = record["payload"]
        identity = observation._identity(payload["code"])
        rows[identity] = dict(code=payload["code"], security_identity=identity,
            price=payload["price"], price_basis=observation.PRICE_BASIS,
            unit=payload["field_units"]["price"], source_version=record["source_version"],
            batch_id=envelope["batch_id"], event_time=record["event_time"],
            observed_at=envelope["response_completed_at"], available_at=record["available_at"],
            request_started_at=record["observed_at"], raw_hash=record["raw_hash"])
    snapshot = dict(version=observation.VERSION, observation_contract=contract,
        scope="synthetic_test_only" if envelope["synthetic_test_only"] else "historical",
        batch_id=envelope["batch_id"], captured_at=envelope["completed_at"],
        source_version=TENCENT_SOURCE_VERSION,
        file_sha256=hashlib.sha256((json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True)
                                  + "\n").encode("utf-8")).hexdigest(), rows=rows,
        request_status="SUCCEEDED", risk_assessment="NOT_ASSESSED")
    if contract == SELECTION_VERSION:
        if set(rows) != set(envelope["selection"]):
            raise ValueError("refresh_market_identity_mismatch")
        snapshot["selection"] = list(envelope["selection"])
    for identity in rows:
        observation._quote(snapshot, identity, times[2])
    return snapshot


def evaluate_current(snapshot, identity, rule, *, now=None, attempt=None, contract=VERSION):
    identity = observation._identity(identity)
    clock = now or utc_now()
    result = observation.evaluate(snapshot, identity, rule, now=clock)
    reason, age = result["reason"], None
    if contract == VERSION and identity not in IDENTITIES:
        reason = "本轮刷新范围未支持"
    elif contract == SELECTION_VERSION and not selection_supported(identity):
        reason = "本轮刷新范围未支持"
    elif contract == SELECTION_VERSION and identity not in (attempt or {}).get("selection", []):
        reason = "本次未获取（未选择）"
    elif not attempt or attempt.get("status") != "SUCCEEDED":
        reason = ("完成状态未确认：" if (attempt or {}).get("status") == "UNCONFIRMED" else "本次刷新未成功：") + str((attempt or {}).get("error", "尚未刷新"))
    elif not snapshot or attempt.get("batch_id") != snapshot.get("batch_id"):
        reason = "所选为历史快照，不代替本次刷新"
    elif snapshot.get("observation_contract") != contract:
        reason = "refresh_contract_invalid"
    elif contract == SELECTION_VERSION and snapshot.get("selection") != attempt["selection"]:
        reason = "本次报价集合不匹配"
    if snapshot and identity in snapshot.get("rows", {}) and (contract == VERSION or not reason):
        try:
            age = (clock - observation._time(snapshot["rows"][identity].get("event_time"))).total_seconds()
            if age < 0:
                reason = "quote_time_order_or_future_invalid"
            elif age > AGE_SECONDS and not reason:
                reason = "报价陈旧：超过60秒"
        except (ValueError, TypeError):
            reason = "timestamp_missing_or_invalid"
    if reason:
        for condition in result["conditions"].values():
            if condition["state"] != "UNSET":
                condition.update(state="INSUFFICIENT", label="数据不足")
            condition["comparison_price"] = None
    else:
        for condition in result["conditions"].values():
            if condition["state"] in ("MET", "NOT_MET"):
                condition["label"] = "本次报价" + condition["label"]
    result.update(reason=reason, quote_age_seconds=age, observation_contract=contract,
                  age_status="年龄符合" if age is not None and 0 <= age <= AGE_SECONDS else "陈旧/无效",
                  historical_only=False, tradability="NOT_ASSESSED")
    return result


class RefreshController:
    """A shared in-flight guard and cooldown for both observation contracts."""
    def __init__(self, store, *, network_enabled=False, fixture=None, contract=VERSION):
        if contract not in REFRESH_CONTRACTS:
            raise ValueError("refresh_contract_invalid")
        self.contract = contract
        self.store = store
        self.fixture = Path(fixture).resolve() if fixture else None
        self.test_only = store.scope == "synthetic_test_only"
        self.enabled = (self.test_only and self.fixture is not None) or (
            contract == VERSION and not self.test_only and network_enabled is True
            and os.environ.get(NETWORK_SWITCH) == "1")
        self.state = store.root / VERSION / "control"

    def refresh(self, pool, *, selection=None):
        started = time.perf_counter()
        hard = started + TOTAL_SECONDS
        result = safe("REJECTED", contract=self.contract, error="network_not_enabled", requests_made=0)
        owned = dispatched = False
        cleaned = True
        target = None
        process = None
        published = False
        selection_fields = {}
        if not self.enabled:
            return result
        try:
            identities = observation.normalize_pool(pool)
            codes = list(CODES)
            if self.contract == SELECTION_VERSION:
                identities_selected = normalize_selection(selection)
                if not set(identities_selected) <= set(identities):
                    raise ValueError("quote_selection_outside_pool")
                selection_fields = {"selection": identities_selected}
                codes = sorted(identity.split(":")[1] for identity in identities_selected)
            elif selection is not None or not set(identities) & IDENTITIES:
                raise ValueError("本轮刷新范围未支持")
            self.state.mkdir(parents=True, exist_ok=True)
            if (self.state / "cleanup-incomplete.json").exists():
                raise ValueError("cleanup_incomplete_block")
            lock = self.state / "active.lock"
            batch = uuid.uuid4().hex
            with lock.open("x", encoding="utf-8") as output:
                json.dump({"pid": os.getpid(), "batch_id": batch}, output)
            owned = True
            for pending in self.state.glob("pending-finalization-*.json"):
                body = observation._json(pending.read_bytes())
                if committed_refresh_outcome(self.store.root, body["batch_id"], body["response_sha256"],
                                             contract=body.get("contract", VERSION))["status"] != "SUCCEEDED":
                    raise ValueError("incomplete_finalization_block")
            tick = time.monotonic()
            for path in [*self.state.glob("attempt-*.json"), *self.state.glob("release-*.json")]:
                previous = observation._json(path.read_bytes())
                if (type(previous.get("dispatch_monotonic")) not in (int, float)
                        or not math.isfinite(previous["dispatch_monotonic"])
                        or previous["dispatch_monotonic"] < 0):
                    raise ValueError("refresh_cooldown_record_invalid")
                elapsed = tick - previous["dispatch_monotonic"]
                if elapsed < INTERVAL_SECONDS:
                    raise ValueError("refresh_cooldown_10_seconds_or_clock_reset")
            target = self.store.root / self.contract / "attempts" / batch
            target.mkdir(parents=True, exist_ok=False)
            intent = dict(contract=self.contract, batch_id=batch, codes=codes, created_at=utc_now().isoformat(),
                          synthetic_test_only=self.test_only, dispatch_monotonic=tick,
                          http_timeout_seconds=HTTP_SECONDS, execution_budget_seconds=TOTAL_SECONDS,
                          confirmation_persistence_separate=True, finalization_contract=FINALIZATION_VERSION,
                          **selection_fields)
            observation.write_once(self.state / ("attempt-" + batch + ".json"), intent)
            observation.write_once(target / "intent.json", intent)
            task = dict(intent, output=str(target.resolve()), fixture=str(self.fixture) if self.fixture else None)
            remaining = min(started + 8.5, hard) - time.perf_counter()
            if remaining <= .5:
                raise TimeoutError("controller_budget_exhausted_before_dispatch")
            dispatched, cleaned = True, False
            process = run_probe_worker_process(task, int(remaining * 1000), bounded_cleanup_ms=500,
                hard_deadline_monotonic=started + 8.5, retain_streams=True,
                worker_command=[sys.executable, "-m", "overnight_quant.data.quote_observation_worker"])
            cleaned = process["cleanup_complete"] is True
            if not cleaned:
                observation.write_once(self.state / "cleanup-incomplete.json",
                                       dict(batch_id=batch, process=process))
            if not process["ok"] or not cleaned:
                raise ValueError((process["error_code"] or "worker_failed") + ":" + str(process.get("error", "")))
            source_bytes = (target / "response.json").read_bytes()
            envelope = observation._json(source_bytes)
            if (envelope["batch_id"] != batch or envelope["synthetic_test_only"] != self.test_only
                    or envelope.get("contract") != self.contract):
                raise ValueError("worker_batch_or_scope_mismatch")
            if self.contract == SELECTION_VERSION:
                _verify_selection_binding(envelope, intent)
            if decode_response(envelope)["file_sha256"] != hashlib.sha256(source_bytes).hexdigest():
                raise ValueError("response_file_bytes_mismatch")
            if time.perf_counter() >= hard:
                raise TimeoutError("controller_budget_exhausted")
            self.store.save_refresh(envelope)
            published = True
            if time.perf_counter() >= hard:
                raise TimeoutError("controller_publication_budget_exhausted")
            result = safe("CANDIDATE_SUCCEEDED", contract=self.contract, batch_id=batch,
                          requests_made=1, error="", **selection_fields)
        except Exception as exc:
            result = safe("FAILED" if dispatched else "REJECTED", contract=self.contract,
                          error=f"{type(exc).__name__}:{exc}", requests_made=None if dispatched else 0,
                          **selection_fields)
            if target is not None and (target / "response-received.json").exists():
                result["requests_made"] = 1
        finally:
            if target is not None:
                result.update(output=str(target), process=process, cleanup_complete=cleaned,
                              synthetic_test_only=self.test_only,
                              elapsed_ms=(time.perf_counter() - started) * 1000)
            result = _finish_refresh(self.store, target, result, started=started, hard=hard,
                                     owned=owned, cleaned=cleaned, dispatched=dispatched, published=published)
        return result
