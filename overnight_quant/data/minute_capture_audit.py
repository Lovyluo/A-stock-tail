"""Immutable audit capture, not a minute Provider or an admission profile."""
from __future__ import annotations

import base64
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import subprocess
import sys
import time

from .minute_capture_worker import CODES, ENDPOINT, SOURCE_VERSION, VERSION as WORKER_VERSION, CN, digest
from .mootdx_node_benchmark import write_benchmark_json_atomic as write
from .probe_worker_process import run_probe_worker_process

VERSION = "mootdx_minute_capture_audit_v1"
ROOT = Path(__file__).resolve().parents[2]
AUDIT_ROOT = ROOT / "overnight_quant/data/cache/minute_capture_audit"
CLOCKS = ("14:49:55", "14:50:05", "14:50:30", "14:50:55")


def safe(**values):
    return {**values, "audit_only": True, "qualification": "0/3", "data_ready": False,
            "hard_gate_authorized": False, "sampling_authorized": False,
            "records": [], "research_ranking": [], "candidates": [], "tickets": [], "orders": []}


def frozen_plan():
    plan = {"schema": "mootdx_minute_capture_plan_v1", "version": VERSION,
            "worker_version": WORKER_VERSION, "trade_date": None,
            "execution_authorized": False, "date_policy": "separate_PM_single_date_authorization_required",
            "codes": list(CODES), "endpoint": ENDPOINT, "source_version": SOURCE_VERSION,
            "client_versions": {"mootdx": "0.11.7", "tdxpy": "0.2.7"},
            "observations": list(CLOCKS), "feature_cutoff": "14:50:00",
            "collection_deadline": "14:51:05", "decision_time": "14:51:10",
            "execution_not_before": "14:52:00", "wave_total_deadline_ms": 2000,
            "process_io_budget_ms": 1500, "termination_cleanup_reserve_ms": 500,
            "single_request_cap_ms": 2000, "concurrency": 1,
            "same_node_policy": "serial_complete_previous_before_next_no_extra_spacing_approved",
            "eastmoney_500ms_rule_applied": False, "page_size": 800, "max_pages_per_code": 1,
            "bars_requests_per_wave": 5, "bars_requests_total": 20, "transaction_requests": 0,
            "retry": False, "fallback": False,
            "target_window": "[14:38,14:50)",
            "candidate_native_end_labels": [f"14:{minute:02d}" for minute in range(39, 51)],
            "candidate_label_interpretation": "historical_single_label_only_full_profile_UNPROVEN",
            "stable_observations": 3, "finality_policy": "observed_stability_not_upstream_finality",
            "final_wave_latest_exit": "14:50:57", "confirmation_and_seal_reserve_ms": 8000,
            "per_wave_publish_and_validation_budget_ms": 2000,
            "final_confirmation_budget_ms": 2000, "seal_budget_ms": 3000,
            "scheduling_and_receipt_reserve_ms": 1000,
            "offline_verification": "after_seal_not_a_source_available_at_timestamp",
            "remaining_budget_proven": False}
    return {**plan, "plan_hash": digest(plan)}


def check_plan(plan):
    if plan != frozen_plan():
        raise ValueError("frozen_plan_mismatch")


def read_anchored(path, expected):
    raw = Path(path).read_bytes()
    if type(expected) is not str or sha256(raw).hexdigest() != expected:
        raise ValueError("external_file_sha256_mismatch")
    return json.loads(raw.decode("utf-8-sig"), parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))


def events_from_result(result):
    events = []
    # Preserve the stream as received, including any truncated final line.
    for line in result.get("stdout", "").splitlines():
        try:
            item = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(item, dict) and "kind" in item:
            events.append(item)
    return events


def native_label(row):
    value = row.get("datetime")
    if isinstance(value, dict):
        value = value.get("isoformat")
    if not isinstance(value, str) or len(value) < 16:
        raise ValueError("native_minute_label_missing")
    # Selecting the native minute label is not a second-precision event timestamp.
    date.fromisoformat(value[:10])
    datetime.strptime(value[11:16], "%H:%M")
    if value[10] not in (" ", "T"):
        raise ValueError("native_minute_label_invalid")
    return value[:10], value[11:16], value


def analyse(waves, trade_date):
    issues, history, calls = [], {}, []
    for index, wave in enumerate(waves):
        result = wave["result"]
        if not result.get("ok") or result.get("elapsed_ms", math.inf) > 2000:
            issues.append(f"wave_failed:{index}")
        if result.get("cleanup_complete") is False:
            issues.append(f"worker_cleanup_incomplete:{index}")
        events = events_from_result(result)
        started = [e for e in events if e["kind"] == "request_started"]
        responses = [e for e in events if e["kind"] == "response"]
        calls.extend(started)
        if [e.get("code") for e in started] != list(CODES):
            issues.append(f"request_plan_incomplete_or_changed:{index}")
        if [e.get("code") for e in responses] != list(CODES):
            issues.append(f"responses_incomplete_or_changed:{index}")
        previous_end = None
        for response in responses:
            code, request = response["code"], response.get("request")
            expected = {"symbol": code, "frequency": "1m", "start": 0, "offset": 800}
            if request != expected or response.get("request_hash") != digest({"request": expected, "endpoint": ENDPOINT}):
                issues.append(f"request_identity_invalid:{index}:{code}")
            rows = response.get("native_rows", [])
            if response.get("source_version") != SOURCE_VERSION or response.get("native_rows_hash") != digest(rows):
                issues.append(f"native_response_hash_or_version_invalid:{index}:{code}")
            if not rows or len(rows) > 800 or response.get("native_row_count") != len(rows):
                issues.append(f"one_page_response_invalid:{index}:{code}")
            for body in response.get("wire_bodies", []):
                raw = base64.b64decode(body["base64"], validate=True)
                if sha256(raw).hexdigest() != body["sha256"] or len(raw) != body["size"]:
                    issues.append(f"wire_body_hash_invalid:{index}:{code}")
            if not response.get("wire_bodies"):
                issues.append(f"wire_body_missing:{index}:{code}")
            begin = datetime.fromisoformat(response["request_started_at"])
            end = datetime.fromisoformat(response["response_read_completed_at"])
            wave_target = datetime.fromisoformat(f"{trade_date}T{CLOCKS[index]}+08:00")
            if (begin.utcoffset() != timedelta(hours=8) or end.utcoffset() != timedelta(hours=8)
                    or begin.date().isoformat() != trade_date or not wave_target <= begin <= end
                    or end > wave_target + timedelta(seconds=2)):
                issues.append(f"response_time_invalid_or_late:{index}:{code}")
            if previous_end is not None and response["call_monotonic"] < previous_end:
                issues.append(f"node_requests_overlap:{index}:{code}")
            previous_end = response["response_read_monotonic"]
            labels, selected = [], {}
            for row in rows:
                try:
                    day, label, text = native_label(row)
                except ValueError:
                    issues.append(f"label_invalid:{index}:{code}")
                    continue
                # Preserve every row, including other dates, in native_rows. Only the
                # same-day candidate window belongs in this audit view.
                if day != trade_date or label not in frozen_plan()["candidate_native_end_labels"]:
                    continue
                labels.append(label)
                if label in selected:
                    issues.append(f"duplicate_or_overlapping_native_label:{index}:{code}:{label}")
                selected[label] = row
                values = [row.get(k) for k in ("open", "high", "low", "close", "vol", "amount")]
                if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
                    issues.append(f"native_values_invalid:{index}:{code}:{label}")
                elif min(values) < 0 or row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"]):
                    issues.append(f"native_values_invalid:{index}:{code}:{label}")
                if "interval_start" in row or "interval_end" in row:
                    # Additional native annotations must not contradict each other;
                    # the tool never synthesizes these annotations itself.
                    try:
                        left, right = (datetime.fromisoformat(row[k]) for k in ("interval_start", "interval_end"))
                        if right - left != timedelta(seconds=60):
                            raise ValueError("interval_length")
                    except (ValueError, KeyError, TypeError):
                        issues.append(f"native_interval_annotation_invalid:{index}:{code}:{label}")
                history.setdefault((code, label), []).append({"wave": index, "native_label": text,
                    "native_row_hash": digest(row), "response_read_completed_at": response["response_read_completed_at"]})
            if sorted(set(labels)) != frozen_plan()["candidate_native_end_labels"]:
                issues.append(f"target_labels_missing_no_extra_page_authorized:{index}:{code}")
            intervals = [r for r in selected.values() if "interval_start" in r and "interval_end" in r]
            try:
                ordered = sorted(intervals, key=lambda r: r["interval_start"])
                if any(datetime.fromisoformat(a["interval_end"]) > datetime.fromisoformat(b["interval_start"])
                       for a, b in zip(ordered, ordered[1:])):
                    issues.append(f"native_interval_annotations_overlap:{index}:{code}")
            except (ValueError, TypeError):
                pass
    stability = []
    for (code, label), observations in sorted(history.items()):
        last = observations[-1]["native_row_hash"]
        tail = []
        for observation in reversed(observations):
            if observation["native_row_hash"] != last:
                break
            tail.append(observation)
        changed = sum(a["native_row_hash"] != b["native_row_hash"] for a, b in zip(observations, observations[1:]))
        stability.append({"code": code, "native_minute_label": label, "observations": observations,
                          "changes_observed": changed, "consecutive_identical_observations": len(tail),
                          "observed_stable": len(tail) >= 3,
                          "stability_criterion_met_at": tail[-3]["response_read_completed_at"] if len(tail) >= 3 else None,
                          "finality": "UNPROVEN", "is_final": False,
                          "reason": "observation_stability_does_not_prove_upstream_finality_or_interval_semantics"})
    if len(waves) != 4 or len(calls) != 20:
        issues.append("four_waves_twenty_bars_requests_not_complete")
    cleanup = ({"cleanup_complete": all(w["result"].get("cleanup_complete") is True for w in waves),
                "residual_process_state": "NO_ACTIVE_PROCESS_IN_TRACKED_SCOPE" if all(
                    w["result"].get("cleanup_complete") is True for w in waves) else "UNCONFIRMED"}
               if any("cleanup_complete" in w["result"] for w in waves) else {})
    return safe(status="MINUTE_CAPTURE_AUDIT_FAILED" if issues else "MINUTE_CAPTURE_AUDIT_COMPLETED_SEMANTICS_UNPROVEN",
                issues=list(dict.fromkeys(issues)), requests_attempted=len(calls),
                captured_target_views=len(history), row_stability=stability,
                volume_unit_status="EXISTING_SINGLE_LABEL_APPROVAL_NOT_RECHECKED",
                amount_and_interval_semantics="UNPROVEN", source_finality="UNPROVEN", **cleanup)


def verify(evidence, expected_file_sha256, actual_file_sha256):
    errors = []
    if not expected_file_sha256 or expected_file_sha256 != actual_file_sha256:
        errors.append("external_file_sha256_mismatch")
    if evidence.get("schema") != VERSION or evidence.get("plan") != frozen_plan():
        errors.append("evidence_contract_mismatch")
    unsigned = {k: v for k, v in evidence.items() if k != "evidence_hash"}
    if evidence.get("evidence_hash") != digest(unsigned):
        errors.append("evidence_hash_mismatch")
    try:
        summary = analyse(evidence["waves"], evidence["trade_date"])
        if evidence.get("capture_error"):
            summary = safe(**{**summary, "status": "MINUTE_CAPTURE_AUDIT_FAILED",
                              "capture_error": evidence["capture_error"]})
        if evidence.get("summary") != summary:
            errors.append("recomputed_capture_summary_mismatch")
    except (KeyError, ValueError, TypeError, IndexError):
        errors.append("capture_structure_invalid")
        summary = None
    if any(evidence.get(key) != value for key, value in safe().items()):
        errors.append("unsafe_or_promoted_output")
    return safe(status="MINUTE_CAPTURE_EVIDENCE_VERIFIED" if not errors else "MINUTE_CAPTURE_EVIDENCE_INVALID",
                integrity_verified=not errors, errors=errors, summary=summary,
                evidence_hash=evidence.get("evidence_hash"))


def _authorization(auth, trade_date, plan, current):
    if (auth.get("schema") != "minute_capture_manual_authorization_v1"
            or auth.get("status") != "PM_APPROVED_SINGLE_AUDIT" or auth.get("trade_date") != trade_date
            or auth.get("plan_hash") != plan["plan_hash"] or auth.get("single_use") is not True):
        raise ValueError("date_specific_PM_authorization_required")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=ROOT, text=True).strip()
    if head != auth.get("code_sha") or status:
        raise ValueError("authorized_code_or_worktree_mismatch")
    from .market_source_go_nogo import _completed_calendar_errors
    from .market_session_confirmation import verify_market_session_confirmation_evidence
    from overnight_quant.scripts.run_complete_industry_audit_manual import _verify_preflight, _verify_environment
    inputs = {}
    for key in ("calendar", "session", "preflight", "environment"):
        ref = auth[key]
        inputs[key] = read_anchored(ref["path"], ref["sha256"])
    cal_sha = auth["calendar"]["sha256"]
    if _completed_calendar_errors(inputs["calendar"], trade_date,
                                   expected_file_sha256=cal_sha, actual_file_sha256=cal_sha):
        raise ValueError("completed_calendar_rejected")
    session_sha = auth["session"]["sha256"]
    session = verify_market_session_confirmation_evidence(inputs["session"],
        expected_file_sha256=session_sha, actual_file_sha256=session_sha)
    if (session.get("evidence_integrity_verified") is not True or inputs["session"].get("trade_date") != trade_date
            or inputs["session"].get("status") != "MARKET_SESSION_CONFIRMED"):
        raise ValueError("session_confirmation_rejected")
    _verify_environment(inputs["environment"])
    _verify_preflight(inputs["preflight"], current)
    if current.utcoffset() != timedelta(hours=8):
        raise ValueError("current_timezone_invalid")
    return inputs["preflight"]


def capture(plan, trade_date, *, authorization, audit_root=AUDIT_ROOT, _test=None):
    check_plan(plan)
    date.fromisoformat(trade_date)
    clock = _test["clock"] if _test else lambda: datetime.now(CN)
    sleep = _test["sleep"] if _test else time.sleep
    current = clock()
    if current.date().isoformat() != trade_date:
        raise ValueError("current_date_mismatch")
    if _test:
        if _test.get("test_only") is not True:
            raise ValueError("test_context_not_explicit")
    else:
        if Path(audit_root).resolve() != AUDIT_ROOT.resolve():
            raise ValueError("isolated_ignored_audit_root_required")
    cleanup_guard = Path(audit_root) / "cleanup-incomplete.json"
    if cleanup_guard.exists():
        raise ValueError("prior_worker_cleanup_unconfirmed_preserve_guard_no_new_worker")
    directory = Path(audit_root) / trade_date
    # Permanent write-once receipt is both the same-date single-run lock and audit.
    write(directory / "intent.json", safe(trade_date=trade_date, plan_hash=plan["plan_hash"],
          started_at=current.isoformat(), test_only=bool(_test), authorization=authorization))
    waves, error = [], None
    try:
        preflight = None if _test else _authorization(authorization, trade_date, plan, current)
        for index, text in enumerate(CLOCKS):
            target = datetime.fromisoformat(f"{trade_date}T{text}+08:00")
            while clock() < target:
                sleep(min(.05, max(0, (target - clock()).total_seconds())))
            mono = time.perf_counter()
            actual = clock()
            if index == 0 and preflight is not None:
                from overnight_quant.scripts.run_complete_industry_audit_manual import _verify_preflight
                _verify_preflight(preflight, actual)
            remaining_ms = (target + timedelta(milliseconds=1500) - actual).total_seconds() * 1000
            if remaining_ms <= 0:
                raise ValueError("observation_window_missed_zero_new_worker")
            task = {"worker_version": WORKER_VERSION, "codes": list(CODES), "endpoint": ENDPOINT,
                    "source_version": SOURCE_VERSION, "trade_date": trade_date, "wave": index,
                    "io_deadline_monotonic": mono + remaining_ms / 1000,
                    "scheduled_at": target.isoformat(), "parent_started_at": actual.isoformat(),
                    "parent_started_monotonic": mono}
            command = _test["worker_command"] if _test else [sys.executable, "-m", "overnight_quant.data.minute_capture_worker"]
            result = run_probe_worker_process(task, int(remaining_ms + 500), worker_command=command,
                retain_streams=True, bounded_cleanup_ms=500,
                hard_deadline_monotonic=mono + (remaining_ms + 500) / 1000)
            wave = {"wave": index, "target_at": target.isoformat(), "parent_started_at": actual.isoformat(),
                    "parent_completed_at": clock().isoformat(), "result": result}
            waves.append(wave)
            publish_started = time.perf_counter()
            if result.get("cleanup_complete") is not True:
                write(cleanup_guard, safe(status="WORKER_CLEANUP_INCOMPLETE", trade_date=trade_date,
                    wave=index, pid=result.get("pid"), result=result,
                    residual_process_state="UNCONFIRMED", automatic_reset_allowed=False))
            write(directory / f"wave-{index}.json", wave)
            if result.get("cleanup_complete") is not True:
                raise ValueError(f"worker_cleanup_incomplete_no_new_worker:{index}")
            if not result.get("ok") or result.get("elapsed_ms", math.inf) > 2000:
                raise ValueError(f"wave_process_or_total_budget_failed:{index}")
            partial = analyse([*waves], trade_date)
            publish_ms = (time.perf_counter() - publish_started) * 1000
            write(directory / f"wave-{index}.publication.json",
                  {"wave": index, "published_at": clock().isoformat(), "publication_and_validation_ms": publish_ms})
            if publish_ms > plan["per_wave_publish_and_validation_budget_ms"]:
                raise ValueError("wave_capture_publication_budget_exhausted")
            structural = [issue for issue in partial["issues"] if issue != "four_waves_twenty_bars_requests_not_complete"]
            if structural:
                raise ValueError(structural[0])
    except Exception as exc:
        error = f"{type(exc).__name__}:{exc}"
    confirmation_started = clock()
    confirmation_mono = time.perf_counter()
    summary = analyse(waves, trade_date)
    confirmation_completed = clock()
    confirmation_ms = (time.perf_counter() - confirmation_mono) * 1000
    if confirmation_ms > plan["final_confirmation_budget_ms"]:
        error = error or "final_confirmation_budget_exhausted"
    if confirmation_completed > datetime.fromisoformat(f"{trade_date}T14:51:05+08:00"):
        error = error or "confirmation_after_collection_deadline"
    if error:
        summary = safe(**{**summary, "status": "MINUTE_CAPTURE_AUDIT_FAILED", "capture_error": error})
    evidence = safe(schema=VERSION, trade_date=trade_date, plan=plan, test_only=bool(_test), waves=waves,
                    summary=summary, confirmation_started_at=confirmation_started.isoformat(),
                    confirmation_evaluated_at=confirmation_completed.isoformat(),
                    confirmation_elapsed_ms=confirmation_ms,
                    source_finality="UNPROVEN", capture_error=error)
    evidence["evidence_hash"] = digest(evidence)
    seal_started = clock()
    seal_mono = time.perf_counter()
    path = write(directory / "evidence.json", evidence)
    write(directory / "file-anchor.json", {"file": path.name, "sha256": sha256(path.read_bytes()).hexdigest()})
    sealed_at = clock()
    late = sealed_at > datetime.fromisoformat(f"{trade_date}T14:51:05+08:00")
    seal_ms = (time.perf_counter() - seal_mono) * 1000
    seal_budget_exceeded = seal_ms > plan["seal_budget_ms"]
    write(directory / "seal-receipt.json", safe(
        status="MINUTE_CAPTURE_AUDIT_FAILED" if late or seal_budget_exceeded else summary["status"],
        evidence_file=path.name, sealed_at=sealed_at.isoformat(),
        seal_started_at=seal_started.isoformat(), seal_elapsed_ms=seal_ms,
        boundary="evidence_and_external_anchor_published;receipt_publication_is_not_source_availability",
        late_seal=late, seal_budget_exceeded=seal_budget_exceeded,
        source_available_at="UNPROVEN", finality="UNPROVEN",
        cleanup_complete=summary.get("cleanup_complete"),
        residual_process_state=summary.get("residual_process_state", "NOT_EVALUATED")))
    return evidence, path
