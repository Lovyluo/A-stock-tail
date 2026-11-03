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
CODES = observation.INITIAL_POOL
IDENTITIES = frozenset(observation.normalize_pool(CODES))
NETWORK_SWITCH = "OBSERVATION_QUOTE_NETWORK_ENABLED"
HTTP_SECONDS = 5
TOTAL_SECONDS = 10
INTERVAL_SECONDS = 10
AGE_SECONDS = 60
FINALIZATION_VERSION = "manual_quote_refresh_finalization_v2"


def utc_now():
    return datetime.now(timezone.utc)


def safe(status, **values):
    return dict(status=status, contract=VERSION, risk_assessment="NOT_ASSESSED",
                tradability="NOT_ASSESSED", usage_basis="UNKNOWN", qualification="0/3",
                data_ready=False, hard_gate_authorized=False, records=[], candidates=[],
                tickets=[], orders=[], **values)


def committed_refresh_outcome(root, batch, response_sha256):
    """One admission rule for controller, storage and page; early receipt is not a commit."""
    directory = Path(root) / VERSION / "attempts" / batch
    failure = directory / "final-failure.json"
    rejected = safe("FAILED", batch_id=batch, error="final_commit_missing_or_invalid")
    try:
        if failure.exists():
            body = observation._json(failure.read_bytes())
            if body.get("batch_id") == batch and body.get("status") in ("FAILED", "REJECTED"):
                return body
            return rejected
        commit = observation._json((directory / "final-commit.json").read_bytes())
        final_bytes = (directory / "final-result.json").read_bytes()
        receipt_bytes = (directory / "receipt.json").read_bytes()
        final = observation._json(final_bytes)
        receipt = observation._json(receipt_bytes)
        if (commit.get("schema") != FINALIZATION_VERSION or commit.get("batch_id") != batch
                or commit.get("status") != "SUCCEEDED"
                or commit.get("final_result_sha256") != hashlib.sha256(final_bytes).hexdigest()
                or commit.get("candidate_receipt_sha256") != hashlib.sha256(receipt_bytes).hexdigest()
                or commit.get("response_sha256") != response_sha256
                or receipt.get("status") != "CANDIDATE_SUCCEEDED"
                or receipt.get("batch_id") != batch or receipt.get("cleanup_complete") is not True
                or final.get("status") != "CANDIDATE_SUCCEEDED" or final.get("batch_id") != batch
                or final.get("cleanup_complete") is not True):
            return rejected
        stages = [commit.get(key) for key in ("started_monotonic", "receipt_completed_monotonic",
                  "unlock_started_monotonic", "unlock_completed_monotonic", "final_write_started_monotonic",
                  "final_write_completed_monotonic", "commit_prepared_monotonic")]
        if (any(type(value) not in (int, float) or not math.isfinite(value) for value in stages)
                or stages != sorted(stages) or not 0 <= stages[-1] - stages[0] < TOTAL_SECONDS):
            return rejected
        if final.get("elapsed_before_final_write_ms") != (stages[4] - stages[0]) * 1000:
            return rejected
        lock = Path(root) / VERSION / "control" / "active.lock"
        if lock.exists() and observation._json(lock.read_bytes()).get("batch_id") == batch:
            return rejected
        return {**final, "status": commit["status"]}
    except (OSError, ValueError, KeyError, TypeError):
        return rejected


def _finish_refresh(store, target, result, *, started, hard, owned, cleaned, dispatched, published):
    """Publish only after unlock and write/readback checks; keep failure vetoes append-only."""
    batch = target.name if target is not None else None
    state = store.root / VERSION / "control"
    lock = state / "active.lock"
    stage = "candidate_receipt"
    try:
        if target is not None:
            result.update(batch_id=batch, finalization_contract=FINALIZATION_VERSION)
            candidate = {**result, "status": "CANDIDATE_SUCCEEDED" if published and result["status"] == "SUCCEEDED"
                         else result["status"]}
            observation.write_once(target / "receipt.json", candidate)
            receipt_completed = time.perf_counter()
            if receipt_completed >= hard:
                raise TimeoutError("candidate_receipt_budget_exhausted")
        if owned and cleaned and not (published and result["status"] != "SUCCEEDED"):
            if dispatched:
                observation.write_once(state / ("release-" + batch + ".json"),
                                       {"dispatch_monotonic": time.monotonic()})
            if published:
                # Never delete this record: incomplete publication blocks a later refresh.
                observation.write_once(state / ("pending-finalization-" + batch + ".json"),
                                       {"batch_id": batch, "response_sha256": stable_hash(
                                           observation._json((target / "response.json").read_bytes()))})
            stage = "unlock"
            unlock_started = time.perf_counter()
            if unlock_started >= hard:
                raise TimeoutError("unlock_budget_exhausted")
            lock.unlink()
            unlock_completed = time.perf_counter()
            if unlock_completed >= hard:
                raise TimeoutError("unlock_budget_exhausted")
        if result["status"] != "SUCCEEDED":
            if target is not None:
                observation.write_once(target / "final-failure.json", result)
            return result
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
        commit = dict(schema=FINALIZATION_VERSION, batch_id=batch, status="SUCCEEDED",
            response_sha256=stable_hash(response), final_result_sha256=hashlib.sha256(final_bytes).hexdigest(),
            candidate_receipt_sha256=hashlib.sha256((target / "receipt.json").read_bytes()).hexdigest(),
            started_monotonic=started, receipt_completed_monotonic=receipt_completed,
            unlock_started_monotonic=unlock_started, unlock_completed_monotonic=unlock_completed,
            final_write_started_monotonic=final_started, final_write_completed_monotonic=final_completed,
            commit_prepared_monotonic=time.perf_counter())
        stage = "final_commit"
        # Stage/fsync/read back privately; incomplete writes are never a public commit.
        staged = target / "final-commit-candidate.json"
        observation.write_once(staged, commit)
        if observation._json(staged.read_bytes()) != commit:
            raise ValueError("final_commit_readback_mismatch")
        if time.perf_counter() >= hard:
            raise TimeoutError("final_commit_budget_exhausted")
        os.link(staged, target / "final-commit.json")  # Atomic, same-volume, no replacement.
        if time.perf_counter() >= hard:
            raise TimeoutError("final_commit_publication_budget_exhausted")
        persisted = committed_refresh_outcome(store.root, batch, stable_hash(response))
        finished = time.perf_counter()
        if persisted["status"] != "SUCCEEDED" or finished >= hard:
            raise TimeoutError("final_commit_confirmation_failed")
        return {**persisted, "elapsed_ms": (finished - started) * 1000}
    except BaseException as exc:
        result.update(status="FAILED", error=f"{stage}:{type(exc).__name__}:{exc}",
                      elapsed_ms=(time.perf_counter() - started) * 1000)
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
        return result


def decode_response(envelope):
    """Re-run the existing Provider and quote Adapter from retained response bytes."""
    if envelope.get("contract") != VERSION or tuple(envelope.get("codes", [])) != CODES:
        raise ValueError("refresh_contract_or_codes_invalid")
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
    expected_url = build_tencent_request_url(CODES)
    if (envelope.get("request_url") != expected_url or type(envelope.get("request_count")) is not int
            or envelope["request_count"] != 1):
        raise ValueError("refresh_request_identity_invalid")

    class Replay:
        def request(self, method, url, **kwargs):
            if method != "GET" or url != expected_url:
                raise ValueError("refresh_replay_request_invalid")
            return TencentHttpResponse(raw, envelope["http_status"], envelope["response_url"])

    clock = iter(times[:2])
    provider = TencentDirectHttpProviders(CODES, transport=Replay(), clock=lambda: next(clock),
                                         timeout_seconds=HTTP_SECONDS)
    records = []

    def collect():
        records.extend(provider.collect_quote_records())
        return records

    result = execute_source_adapter("quote", origin_source=TENCENT_ORIGIN_SOURCE,
        adapter=TENCENT_ADAPTER, source_version=TENCENT_SOURCE_VERSION,
        provider_envelope=SourceProviderEnvelope(provider_key=TENCENT_QUOTE_PROVIDER_KEY,
                                                 provider_callable=collect), environ={})
    if result.get("status") != SOURCE_ADAPTER_BOUND:
        raise ValueError("quote_adapter_rejected:" + str(result.get("status")))
    if len(records) != 5 or {r["payload"]["code"] for r in records} != set(CODES):
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
    snapshot = dict(version=observation.VERSION, observation_contract=VERSION,
        scope="synthetic_test_only" if envelope["synthetic_test_only"] else "historical",
        batch_id=envelope["batch_id"], captured_at=envelope["completed_at"],
        source_version=TENCENT_SOURCE_VERSION,
        file_sha256=hashlib.sha256((json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True)
                                  + "\n").encode("utf-8")).hexdigest(), rows=rows,
        request_status="SUCCEEDED", risk_assessment="NOT_ASSESSED")
    for identity in IDENTITIES:
        observation._quote(snapshot, identity, times[2])
    return snapshot


def evaluate_current(snapshot, identity, rule, *, now=None, attempt=None):
    identity = observation._identity(identity)
    clock = now or utc_now()
    result = observation.evaluate(snapshot, identity, rule, now=clock)
    reason, age = result["reason"], None
    if identity not in IDENTITIES:
        reason = "本轮刷新范围未支持"
    elif not attempt or attempt.get("status") != "SUCCEEDED":
        reason = "本次刷新未成功：" + str((attempt or {}).get("error", "尚未刷新"))
    elif not snapshot or attempt.get("batch_id") != snapshot.get("batch_id"):
        reason = "所选为历史快照，不代替本次刷新"
    elif snapshot.get("observation_contract") != VERSION:
        reason = "refresh_contract_invalid"
    if snapshot and identity in snapshot.get("rows", {}):
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
    result.update(reason=reason, quote_age_seconds=age, observation_contract=VERSION,
                  age_status="年龄符合" if age is not None and 0 <= age <= AGE_SECONDS else "陈旧/无效",
                  historical_only=False, tradability="NOT_ASSESSED")
    return result


class RefreshController:
    """A persisted in-flight guard and cooldown for this one fixed observation action."""
    def __init__(self, store, *, network_enabled=False, fixture=None):
        self.store = store
        self.fixture = Path(fixture).resolve() if fixture else None
        self.test_only = store.scope == "synthetic_test_only"
        self.enabled = (self.test_only and self.fixture is not None) or (
            not self.test_only and network_enabled is True and os.environ.get(NETWORK_SWITCH) == "1")
        self.state = store.root / VERSION / "control"

    def refresh(self, pool):
        started = time.perf_counter()
        hard = started + TOTAL_SECONDS
        result = safe("REJECTED", error="network_not_enabled", requests_made=0)
        owned = dispatched = False
        cleaned = True
        target = None
        process = None
        published = False
        if not self.enabled:
            return result
        try:
            identities = observation.normalize_pool(pool)
            if not set(identities) & IDENTITIES:
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
                if committed_refresh_outcome(self.store.root, body["batch_id"], body["response_sha256"])["status"] != "SUCCEEDED":
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
            target = self.store.root / VERSION / "attempts" / batch
            target.mkdir(parents=True, exist_ok=False)
            intent = dict(contract=VERSION, batch_id=batch, codes=list(CODES), created_at=utc_now().isoformat(),
                          synthetic_test_only=self.test_only, dispatch_monotonic=tick,
                          http_timeout_seconds=HTTP_SECONDS, controller_budget_seconds=TOTAL_SECONDS)
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
            if envelope["batch_id"] != batch or envelope["synthetic_test_only"] != self.test_only:
                raise ValueError("worker_batch_or_scope_mismatch")
            if decode_response(envelope)["file_sha256"] != hashlib.sha256(source_bytes).hexdigest():
                raise ValueError("response_file_bytes_mismatch")
            if time.perf_counter() >= hard:
                raise TimeoutError("controller_budget_exhausted")
            self.store.save_refresh(envelope)
            published = True
            if time.perf_counter() >= hard:
                raise TimeoutError("controller_publication_budget_exhausted")
            result = safe("SUCCEEDED", batch_id=batch, requests_made=1, error="")
        except Exception as exc:
            result = safe("FAILED" if dispatched else "REJECTED", error=f"{type(exc).__name__}:{exc}",
                          requests_made=None if dispatched else 0)
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
