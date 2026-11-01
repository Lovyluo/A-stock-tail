"""Fixed six-request daily capture; not a Provider registry or trading route."""
from __future__ import annotations

import base64
from datetime import date, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .completed_daily_observation import (
    CONTRACT_HASH, MANIFEST_SCHEMA, _replay, _time, observe, read_anchored_json,
)
from .market_calendar import CN_TZ
from .mootdx_node_benchmark import write_benchmark_json_atomic as write
from .point_in_time import stable_hash
from .probe_worker_process import run_probe_worker_process
from .s2_integration_contract import FIXED_CODES
from .static_source_providers import (
    SOURCE_IDENTITIES, STATIC_SOURCE_EVIDENCE_SCHEMA_VERSION, StaticHttpResponse,
    StaticSourceProviders, TENCENT_KLINE_URL,
    _qfq_rows,
)
from overnight_quant.scripts.run_static_source_validation import compute_evidence_hash, _serialize_response

SCHEMA = "fixed_six_daily_freeze_v1"
AUTH = "fixed_six_daily_authorization_v1"
PLAN = ["sh000001,day,,,260,qfq"] + [
    f"{'sh' if c.startswith('6') else 'sz'}{c},day,,,120,qfq" for c in FIXED_CODES]
BUDGET = {"request_ms": 10000, "request_cleanup_ms": 1000,
          "total_ms": 90000, "batch_cleanup_ms": 2000, "finish_reserve_ms": 10000}
IDENTITY = stable_hash({"schema": SCHEMA, "plan": PLAN, "budget": BUDGET, "observation": CONTRACT_HASH})
STATE = Path(__file__).resolve().parents[1] / "data/cache/fixed-six-daily-state"


def now():
    return datetime.now(CN_TZ).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_anchors(directory):
    return {p.name: sha(p) for p in Path(directory).iterdir() if p.is_file()}


def safe(**fields):
    return {"schema_version": SCHEMA, "qualification": "0/3", "data_ready": False,
            "hard_gate_authorized": False, "records": [], "candidates": [], "tickets": [],
            "orders": [], "risk_assessment": "NOT_ASSESSED", "observation_ranking": [], **fields}


def head():
    p = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=2)
    if p.returncode:
        raise ValueError("git_identity_query_failed")
    return p.stdout.strip()


def check_inputs(day, codes, approved_sha, output, authorization, *, test_only=False, reserved=False):
    if date.fromisoformat(day).isoformat() != day or day != datetime.now(CN_TZ).date().isoformat():
        raise ValueError("current_cn_run_date_required")
    if tuple(codes.split(",")) != FIXED_CODES:
        raise ValueError("explicit_ordered_fixed_five_required")
    if not re.fullmatch("[0-9a-f]{40}", approved_sha or "") or head() != approved_sha:
        raise ValueError("approved_code_identity_mismatch")
    target = Path(output).resolve()
    if target.exists() and not reserved:
        raise ValueError("output_exists_no_overwrite")
    if not test_only:
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=2)
        if dirty.returncode or dirty.stdout.strip():
            raise ValueError("clean_fixed_code_worktree_required")
        cache = (Path(__file__).resolve().parents[1] / "data/cache").resolve()
        if not target.is_relative_to(cache):
            raise ValueError("ignored_cache_output_required")
        expected = {"schema_version": AUTH, "status": "PM_APPROVED", "single_use": True,
                    "run_date": day, "approved_sha": approved_sha, "contract_hash": IDENTITY,
                    "output": str(target), "codes": list(FIXED_CODES), "budget": BUDGET}
        if any(authorization.get(k) != v for k, v in expected.items()):
            raise ValueError("authorization_identity_mismatch")
        stamp = _time(now())
        if not _time(authorization["created_at"]) <= stamp <= _time(authorization["expires_at"]):
            raise ValueError("authorization_time_invalid")
        if _time(authorization["expires_at"]).date().isoformat() != day:
            raise ValueError("authorization_date_invalid")
        for field in ("pm_source", "source_use_basis"):
            if not isinstance(authorization.get(field), str) or authorization[field].strip().upper() in ("", "UNKNOWN", "TBD"):
                raise ValueError(f"authorization_{field}_unconfirmed")
    batch = authorization.get("batch_id")
    if not isinstance(batch, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", batch):
        raise ValueError("batch_id_required_before_request")
    return target


class CaptureTransport:
    def __init__(self, task, directory):
        self.task, self.directory, self.index = task, directory, 0
        self.timings = []
        self.calendar = []

    def request(self, method, url, *, params, data, headers, timeout_seconds):
        index = self.index
        if index >= 6 or method != "GET" or url != TENCENT_KLINE_URL or data is not None or params != {"param": PLAN[index]}:
            raise ValueError("request_not_in_fixed_plan")
        if self.task["work_deadline"] - time.perf_counter() < (6 - index) * 10 + 10:
            raise TimeoutError("remaining_request_and_finish_budget_insufficient")
        self.index += 1
        started = time.perf_counter()
        request_task = {**self.task, "mode": "request", "index": index,
                        "request_started_monotonic": started, "request_deadline": started + 9,
                        "headers": headers, "controller_pid": os.getpid()}
        result = run_probe_worker_process(request_task, 10000, bounded_cleanup_ms=1000,
            hard_deadline_monotonic=started + 10, retain_streams=True,
            worker_command=[sys.executable, "-m", "overnight_quant.scripts.run_daily_freeze_worker"])
        # The receipt is retained even when a child timed out without returning JSON.
        write(self.directory / f"process-{index}.json", result)
        self.timings.append(result)
        if not result["cleanup_complete"]:
            write(Path(self.task["state"]) / "cleanup-incomplete.json", safe(batch_id=self.task["batch_id"], process=result))
            raise RuntimeError("request_cleanup_incomplete")
        if not result["ok"] or time.perf_counter() > started + 10:
            raise RuntimeError(f"request_failed:{result['error_code']}")
        raw_path = self.directory / f"raw-{index}.json"
        raw = read_anchored_json(raw_path, result["payload"]["raw_file_sha256"])
        if raw["index"] != index or raw["batch_id"] != self.task["batch_id"]:
            raise ValueError("raw_capture_identity_mismatch")
        if index and raw["http_status_code"] == 200:
            symbol = PLAN[index].split(',')[0]
            body = json.loads(base64.b64decode(raw['content_base64']))
            rows = _qfq_rows(body['data'][symbol]['qfqday'], FIXED_CODES[index-1],
                             date.fromisoformat(self.task['day']), set(self.calendar))
            if [r['date'] for r in rows[-60:]] != self.calendar[-60:]:
                raise ValueError("latest_60_sessions_missing_stop_dispatch")
        return StaticHttpResponse(base64.b64decode(raw["content_base64"], validate=True),
                                  raw["http_status_code"], raw["response_url"], raw["response_headers"])


def coordinate(task):
    directory = Path(task["output"])
    transport = CaptureTransport(task, directory)
    evidence = {"evidence_schema_version": STATIC_SOURCE_EVIDENCE_SCHEMA_VERSION,
                "trade_date": task["day"], "feature_cutoff": f"{task['day']}T14:50:00+08:00",
                "requested_codes": list(FIXED_CODES), "raw_responses": {}, "records_by_capability": {},
                "synthetic_test_only": task["test_only"], "producer_commit_sha": task["approved_sha"]}
    try:
        provider = StaticSourceProviders(FIXED_CODES, target_trade_date=task["day"],
            feature_cutoff=evidence["feature_cutoff"], transport=transport,
            clock=lambda: datetime.now(CN_TZ), timeout_seconds=10)
        calendar = provider.collect_trading_calendar_batch()
        transport.calendar = calendar.records[0]["payload"]["trade_dates"]
        for capability, batch in (("trading_calendar", calendar),):
            evidence["records_by_capability"][capability] = list(batch.records)
            evidence["raw_responses"][capability] = [_serialize_response(r) for r in batch.responses]
        daily = provider.collect_qfq_daily_batch(calendar.records[0]["payload"]["trade_dates"])
        evidence["records_by_capability"]["daily_bar_qfq"] = list(daily.records)
        evidence["raw_responses"]["daily_bar_qfq"] = [_serialize_response(r) for r in daily.responses]
        evidence["evidence_hash"] = compute_evidence_hash(evidence)
        _replay(evidence, task["day"])
        write(directory / "source.json", evidence)
        # Freeze is measured only after publication and byte re-read, not before write.
        source_sha = sha(directory / "source.json")
        read_anchored_json(directory / "source.json", source_sha)
        frozen_at = now()
        manifest = {"schema_version": MANIFEST_SCHEMA, "contract_hash": CONTRACT_HASH,
                    "provenance_kind": "synthetic_test_only" if task["test_only"] else "frozen_capture",
                    "batch_id": task["batch_id"], "run_date": task["day"],
                    "source_file_sha256": source_sha, "frozen_at": frozen_at,
                    "capture_bindings": [
                        {"batch_id": task["batch_id"], "capability": cap, "request_hash": r["request_hash"],
                         "raw_hash": r["raw_hash"], "source_version": SOURCE_IDENTITIES[cap][2]}
                        for cap in ("trading_calendar", "daily_bar_qfq") for r in evidence["raw_responses"][cap]]}
        candidate = observe(evidence, source_file_sha256=source_sha, manifest=manifest,
            run_date=task["day"], generated_at=now(), synthetic_test_only=task["test_only"])
        if not candidate["observation_input_ready"]:
            raise ValueError(f"batch_coverage_rejected:{candidate['errors']}")
        write(directory / "manifest.json", manifest)
        manifest_written_at = now()
        manifest_sha = sha(directory / "manifest.json")
        anchors = {"source": source_sha, "manifest": manifest_sha, "files": file_anchors(directory)}
        # The same independent file verifier is used by the CLI and by production.
        validated = verify(directory, anchors, require_execution=False)
        if not validated["evidence_integrity_verified"] or not validated["observation_input_ready"]:
            raise ValueError(f"frozen_input_rejected:{validated['errors']}")
        write(directory / "input-validation.json", safe(evidence_integrity_verified=True,
              capture_execution_result="PENDING_CLEANUP", observation_input_ready=False, synthetic_test_only=task['test_only']))
        if time.perf_counter() >= task["work_deadline"]:
            raise TimeoutError("batch_work_deadline_exceeded")
        write(directory / "capture-complete.json", safe(status="CAPTURE_COMPLETE", batch_id=task["batch_id"],
            synthetic_test_only=task['test_only'],
            anchors=anchors, source_written_before_freeze=True, frozen_at=frozen_at,
            manifest_written_at=manifest_written_at, completed_at=now(),
            capture_count=transport.index, work_finished_monotonic=time.perf_counter()))
        return {"ok": True, "payload": {"anchors": {**anchors, "files": file_anchors(directory)}}}
    except Exception as exc:
        write(directory / "failure.json", safe(status="CAPTURE_FAILED", batch_id=task["batch_id"],
              synthetic_test_only=task['test_only'],
              error=f"{type(exc).__name__}:{exc}", requests_dispatched=transport.index, completed_at=now()))
        return {"ok": False, "error": f"{type(exc).__name__}:{exc}"}


def verify(directory, anchors, *, require_execution=True):
    """Independent replay of captured bytes; never accepts the producer's score."""
    directory = Path(directory)
    errors, records, observation = [], [], None
    try:
        if not isinstance(anchors.get("files"), dict):
            raise ValueError("external_material_anchors_required")
        if set(anchors["files"]) != {p.name for p in directory.iterdir() if p.is_file()}:
            raise ValueError("material_catalog_incomplete")
        for name, digest in anchors["files"].items():
            if Path(name).name != name or sha(directory / name) != digest:
                raise ValueError("material_file_anchor_mismatch")
        intent = read_anchored_json(directory / "intent.json", anchors["intent"] if "intent" in anchors else sha(directory / "intent.json"))
        if intent['contract_hash'] != IDENTITY or intent['budget'] != BUDGET or intent['codes'] != ','.join(FIXED_CODES):
            raise ValueError('intent_execution_contract_mismatch')
        if not intent['test_only']:
            auth = intent['authorization']
            expected = {'schema_version': AUTH, 'status': 'PM_APPROVED', 'single_use': True,
                        'run_date': intent['day'], 'approved_sha': intent['approved_sha'],
                        'contract_hash': IDENTITY, 'output': str(directory.resolve()),
                        'codes': list(FIXED_CODES), 'budget': BUDGET, 'batch_id': intent['batch_id']}
            if any(auth.get(k) != v for k, v in expected.items()) or not _time(auth['created_at']) <= _time(intent['created_at']) <= _time(auth['expires_at']):
                raise ValueError('captured_authorization_invalid')
        batch = intent["batch_id"]
        files = sorted(p for p in directory.iterdir() if re.fullmatch(r"raw-\d.json", p.name))
        for index, path in enumerate(files):
            raw = json.loads(path.read_text(encoding="utf-8"))
            material = {k: raw[k] for k in ("method", "url", "params", "data")}
            body = base64.b64decode(raw["content_base64"], validate=True)
            if raw["index"] != index or raw["batch_id"] != batch or raw["run_date"] != intent["day"]:
                raise ValueError("raw_batch_identity_mismatch")
            capability = "trading_calendar" if index == 0 else "daily_bar_qfq"
            if raw["source_version"] != SOURCE_IDENTITIES[capability][2]:
                raise ValueError("raw_source_version_invalid")
            if material != {"method": "GET", "url": TENCENT_KLINE_URL,
                            "params": [["param", PLAN[index]]], "data": []}:
                raise ValueError("raw_request_plan_mismatch")
            if sha_bytes(body) != raw["raw_hash"] or len(body) != raw["byte_count"]:
                raise ValueError("raw_bytes_hash_mismatch")
            written = json.loads((directory / f"raw-written-{index}.json").read_text())
            if written["raw_file_sha256"] != sha(path) or written["batch_id"] != batch:
                raise ValueError("raw_write_receipt_mismatch")
            if not _time(raw["request_started_at"]) <= _time(raw["response_completed_at"]) <= _time(written["written_at"]):
                raise ValueError("raw_wall_time_order_invalid")
            if any(_time(t).date().isoformat() != intent["day"] for t in (raw["request_started_at"], raw["response_completed_at"], written["written_at"])):
                raise ValueError("raw_capture_date_invalid")
            times = [raw[k] for k in ("dispatch_monotonic", "http_started_monotonic", "response_completed_monotonic", "raw_write_started_monotonic")]
            if any(type(t) not in (int, float) or not math.isfinite(t) for t in times) or times != sorted(times):
                raise ValueError("raw_monotonic_order_invalid")
            if not times[-1] <= written["written_monotonic"] <= times[0] + 9:
                raise ValueError("raw_seal_absolute_deadline_invalid")
            process = json.loads((directory / f"process-{index}.json").read_text(encoding="utf-8"))
            if process["ok"] and (not process["cleanup_complete"] or process["elapsed_ms"] > 10000):
                raise ValueError("request_process_budget_invalid")
            records.append(raw)
        if "source" in anchors and "manifest" in anchors:
            evidence = read_anchored_json(directory / "source.json", anchors["source"])
            if evidence['producer_commit_sha'] != intent['approved_sha'] or evidence['synthetic_test_only'] is not intent['test_only']:
                raise ValueError('source_producer_identity_mismatch')
            manifest = read_anchored_json(directory / "manifest.json", anchors["manifest"])
            if len(records) != 6 or manifest["batch_id"] != batch:
                raise ValueError("complete_six_requests_required")
            referenced = [r for cap in ("trading_calendar", "daily_bar_qfq") for r in evidence["raw_responses"][cap]]
            if [r["raw_hash"] for r in referenced] != [r["raw_hash"] for r in records]:
                raise ValueError("source_raw_capture_mismatch")
            for index, (ref, raw) in enumerate(zip(referenced, records)):
                written = json.loads((directory / f"raw-written-{index}.json").read_text())
                if not _time(ref["observed_at"]) <= _time(raw["request_started_at"]) <= _time(written["written_at"]) <= _time(ref["available_at"]):
                    raise ValueError("provider_transport_time_order_invalid")
            if any(not json.loads((directory / f"process-{i}.json").read_text())["ok"] for i in range(6)):
                raise ValueError("source_failed_request")
            if manifest["frozen_at"] < max(w["response_completed_at"] for w in records):
                raise ValueError("freeze_before_response_completion")
            observation = observe(evidence, source_file_sha256=anchors["source"], manifest=manifest,
                run_date=intent["day"], generated_at=now(), historical_offline=True,
                synthetic_test_only=intent["test_only"])
            if not observation["observation_input_ready"]:
                raise ValueError(str(observation["errors"]))
        if require_execution:
            execution = read_anchored_json(directory / "execution.json", anchors["execution"])
            if execution["batch_id"] != batch:
                raise ValueError("execution_batch_mismatch")
            success = (execution["status"] == "EXECUTION_COMPLETE" and execution["process"]["ok"]
                       and execution["process"]["cleanup_complete"] and execution["elapsed_ms"] <= 90000)
            if success:
                complete = json.loads((directory / "capture-complete.json").read_text())
                if complete["batch_id"] != batch or complete["capture_count"] != 6 or not intent["started_monotonic"] <= complete["work_finished_monotonic"] < intent["work_deadline"]:
                    raise ValueError("capture_completion_budget_invalid")
                finished = anchors["finished_monotonic"]
                if type(finished) not in (int, float) or not math.isfinite(finished) or not complete["work_finished_monotonic"] <= finished <= intent["started_monotonic"] + 90:
                    raise ValueError("final_freeze_absolute_deadline_invalid")
                if not _time(complete["frozen_at"]) <= _time(complete["manifest_written_at"]) <= _time(execution["completed_at"]):
                    raise ValueError("final_wall_time_order_invalid")
        else:
            success = True
        if not success:
            observation = None
        return safe(evidence_integrity_verified=True, capture_execution_result="PASSED" if success and observation else "FAILED",
                    observation_input_ready=bool(success and observation),
                    observation_ranking=observation["observation_ranking"] if success and observation else [],
                    decision_hash=observation["decision_hash"] if success and observation else None,
                    synthetic_test_only=intent["test_only"], errors=[])
    except Exception as exc:
        errors.append(f"{type(exc).__name__}:{exc}")
    return safe(evidence_integrity_verified=False, capture_execution_result="FAILED", observation_input_ready=False, errors=errors)


def sha_bytes(body):
    return hashlib.sha256(body).hexdigest()


def capture(*, day, codes, approved_sha, output, authorization=None, network=False, fixture=None):
    started = time.perf_counter()
    if not network and fixture is None:
        return safe(status="NETWORK_NOT_REQUESTED", requests_dispatched=0)
    test_only = fixture is not None
    target = None
    lock = None
    owned, cleaned = False, True
    dispatch_attempted, cleanup_known = False, False
    stage, result, reported = "input_validation", None, None
    state = Path(fixture).resolve().parent / "state" if test_only else STATE
    try:
        if network and test_only:
            raise ValueError("fixture_network_mutually_exclusive")
        target = check_inputs(day, codes, approved_sha, output, authorization or {}, test_only=test_only)
        state.mkdir(parents=True, exist_ok=True)
        if (state / "cleanup-incomplete.json").exists():
            raise ValueError("persistent_cleanup_block")
        lock = state / "active.lock"
        with lock.open("x") as f:
            f.write(str(target))
        owned = True
        target.mkdir(parents=True, exist_ok=False)
        task = {"mode": "coordinate", "day": day, "codes": codes, "approved_sha": approved_sha,
                "batch_id": authorization["batch_id"], "output": str(target), "state": str(state),
                "test_only": test_only, "fixture": str(Path(fixture).resolve()) if test_only else None,
                "contract_hash": IDENTITY, "budget": BUDGET, "started_monotonic": started,
                "work_deadline": started + 85, "controller_pid": os.getpid()}
        write(target / "intent.json", {**task, "created_at": now(), "authorization": authorization})
        stage = "coordinator_dispatch"
        cleaned = False
        dispatch_attempted = True
        result = run_probe_worker_process(task, max(0, int((started + 87 - time.perf_counter()) * 1000)),
            bounded_cleanup_ms=2000, hard_deadline_monotonic=started + 87, retain_streams=True,
            worker_command=[sys.executable, "-m", "overnight_quant.scripts.run_daily_freeze_worker"])
        cleaned = result["cleanup_complete"]
        cleanup_known = True
        if not cleaned:
            write(state / "cleanup-incomplete.json", safe(batch_id=task["batch_id"], process=result))
        status = "EXECUTION_COMPLETE" if result["ok"] and result["cleanup_complete"] and time.perf_counter() < started + 90 else "EXECUTION_FAILED"
        execution = safe(status=status, batch_id=task["batch_id"], process=result,
                         synthetic_test_only=test_only,
                         elapsed_ms=(time.perf_counter() - started) * 1000, completed_at=now())
        stage = "execution_receipt"
        write(target / "execution.json", execution)
        if time.perf_counter() >= started + 90:
            return safe(status="FINAL_WRITE_BUDGET_EXCEEDED", observation_input_ready=False)
        stage = "final_verification_and_output"
        anchors = {"intent": sha(target / "intent.json"), "execution": sha(target / "execution.json"), "files": file_anchors(target)}
        if status == "EXECUTION_COMPLETE":
            anchors.update({k: v for k, v in result["payload"]["anchors"].items() if k != "files"})
        anchors["finished_monotonic"] = time.perf_counter()
        if status == "EXECUTION_COMPLETE":
            verified = verify(target, anchors)
            if not verified['observation_input_ready']:
                return safe(status='FINAL_VERIFICATION_FAILED', anchors=anchors, output=str(target), errors=verified['errors'])
            write(target / 'observation.json', verified)
            anchors['files'] = file_anchors(target)
            anchors['finished_monotonic'] = time.perf_counter()
        if anchors["finished_monotonic"] >= started + 90:
            return safe(status="FINAL_ANCHOR_BUDGET_EXCEEDED", observation_input_ready=False)
        final = verify(target, anchors)
        if owned and cleaned and not (state / 'cleanup-incomplete.json').exists():
            lock.unlink()
            owned = False
        anchors['finished_monotonic'] = time.perf_counter()
        if time.perf_counter() >= started + 90:
            return safe(status="FINAL_VERIFICATION_BUDGET_EXCEEDED", observation_input_ready=False)
        return {**final, "status": status, "anchors": anchors, "output": str(target)}
    except Exception as exc:
        reported = safe(status="CONTROLLER_FAILED" if dispatch_attempted else "REQUEST_REJECTED",
            error=f"{type(exc).__name__}:{exc}", failure_stage=stage,
            dispatch_attempted=dispatch_attempted,
            requests_dispatched=None if dispatch_attempted else 0,
            request_count_status="UNKNOWN_AFTER_DISPATCH" if dispatch_attempted else "KNOWN_ZERO_BEFORE_DISPATCH",
            cleanup_complete=cleaned if cleanup_known else None, process=result,
            observation_input_ready=False, synthetic_test_only=test_only,
            failure_receipt_persisted=False)
        if owned and target is not None:
            try:
                if time.perf_counter() >= started + 90:
                    raise TimeoutError("controller_failure_receipt_budget_exhausted")
                path = target / "controller-failure.json"
                write(path, reported)
                reported["failure_receipt_persisted"] = True
                reported["failure_receipt_path"] = str(path)
            except Exception as receipt_error:
                reported["failure_receipt_error"] = f"{type(receipt_error).__name__}:{receipt_error}"
        return reported
    finally:
        # Never release a lock owned by a different controller, or an unconfirmed tree.
        cleanup_stage = "cleanup_guard"
        try:
            if owned and not cleaned and not (state / "cleanup-incomplete.json").exists():
                write(state / "cleanup-incomplete.json", safe(status="CLEANUP_UNCONFIRMED", output=str(target)))
            cleanup_stage = "lock_release"
            if owned and cleaned and lock and lock.exists() and lock.read_text() == str(target) and not (state / "cleanup-incomplete.json").exists():
                lock.unlink()
        except Exception as cleanup_error:
            if reported is None:
                raise
            # Preserve the first failure. An uncertain tree retains its activity lock.
            reported[f"{cleanup_stage}_error"] = f"{type(cleanup_error).__name__}:{cleanup_error}"
