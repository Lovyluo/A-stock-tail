"""Bounded, isolated concept-batch capture and offline observation.

This is not a Provider binding, a source qualification, or a trading route.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .market_calendar import CN_TZ
from .mootdx_node_benchmark import write_benchmark_json_atomic as write
from .point_in_time import stable_hash
from .probe_worker_process import encode_bounded_worker_input, run_probe_worker_process
from .s2_integration_contract import security_identity

VERSION = "concept_batch_observation_v2"
AUTH_VERSION = "concept_batch_authorization_v3"
LAUNCH_VERSION = "concept_batch_launcher_v2"
CONFIRM_VERSION = "concept_batch_outer_confirmation_v2"
ENDPOINT = "https://push2.eastmoney.com/api/qt/clist/get"
BUDGET = {"page_ms": 10000, "page_cleanup_ms": 1000,
          "batch_ms": 90000, "batch_cleanup_ms": 2000, "finish_reserve_ms": 3000}
PLAN = {"list_fs": "m:90 t:3 f:!50", "members_fs": "b:{bk} f:!50",
        "pages_per_kind": 3, "page_size": 100, "sort": "f12",
        "selection": "lexicographically_smallest_valid_bk", "retry": False,
        "max_response_bytes": 1048576, "proxy_policy": "system_and_environment",
        "outer_contract": "powershell_launcher_v2",
        "fields": "f2,f3,f6,f12,f13,f14,f104,f105,f124"}
CONTRACT = stable_hash({"version": VERSION, "plan": PLAN, "budget": BUDGET})
STATE = Path(__file__).resolve().parents[1] / "data/cache/concept-batch-observation"
AUTH_PURPOSE = "PERSONAL_INTERNAL_STRUCTURE_VALIDATION"
MAX_APPROVAL_QUOTE_BYTES = 4096
AUTH_SCOPE = {"max_list_pages": PLAN["pages_per_kind"], "max_member_pages": PLAN["pages_per_kind"],
              "max_http_requests": 2 * PLAN["pages_per_kind"], "selection": PLAN["selection"],
              "single_concept": True, "public_distribution": False}


def now():
    return datetime.now(CN_TZ).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe(**fields):
    return {"schema_version": VERSION, "qualification": "0/3", "data_ready": False,
            "hard_gate_authorized": False, "records": [], "candidates": [],
            "tickets": [], "orders": [], **fields}


def parameters(kind, page, bk=None):
    if kind not in ("list", "members") or type(page) is not int or not 1 <= page <= 3:
        raise ValueError("invalid_page_identity")
    if kind == "members" and not re.fullmatch(r"BK\d{4,6}", bk or ""):
        raise ValueError("invalid_bk")
    fs = PLAN["list_fs"] if kind == "list" else PLAN["members_fs"].format(bk=bk)
    return {"pn": str(page), "pz": "100", "po": "1", "fltt": "2", "fid": "f12",
            "fs": fs, "fields": PLAN["fields"]}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def decode_page(body, kind, page, expected_total, seen):
    obj = json.loads(body)
    data = obj.get("data") if isinstance(obj, dict) else None
    if not isinstance(data, dict) or type(data.get("total")) is not int or data["total"] < 0:
        raise ValueError("invalid_total_or_structure")
    total, diff = data["total"], data.get("diff")
    if expected_total is not None and total != expected_total:
        raise ValueError("total_drift")
    if isinstance(diff, dict):
        diff = list(diff.values())
    if not isinstance(diff, list) or any(not isinstance(row, dict) for row in diff):
        raise ValueError("invalid_rows")
    required = max(0, min(100, total - (page - 1) * 100))
    if page > 1 and required == 0:
        raise ValueError("unexpected_empty_page")
    if len(diff) != required:
        raise ValueError("page_incomplete_or_excess")
    for row in diff:
        code = row.get("f12")
        pattern = r"BK\d{4,6}" if kind == "list" else r"\d{6}"
        if not isinstance(code, str) or not re.fullmatch(pattern, code):
            raise ValueError("invalid_native_identity")
        if kind == "members":
            market = row.get("f13")
            if type(market) is not int or market not in (0, 1):
                raise ValueError("member_market_unknown")
            security_identity(("SH" if market == 1 else "SZ") + code)
        if code in seen:
            raise ValueError("duplicate_native_identity")
        seen.add(code)
    return total, diff


def summarize(list_rows, members, *, total_list, total_members, synthetic):
    # Fixture semantics are explicitly scoped to test-only data, never to a real provider.
    basis = "SYNTHETIC_TEST_ONLY" if synthetic else "UNPROVEN"
    positive = negative = flat = invalid = 0
    for row in members:
        value = _number(row.get("f3"))
        if value is None:
            invalid += 1
        elif value > 0:
            positive += 1
        elif value < 0:
            negative += 1
        else:
            flat += 1
    amounts = [(row, _number(row.get("f6"))) for row in members]
    amount_valid = all(v is not None and v >= 0 for _, v in amounts)
    list_valid = sum(_number(row.get("f3")) is not None for row in list_rows)
    leaders = []
    if synthetic and amount_valid:
        leaders = [{"code": security_identity(("SH" if row["f13"] == 1 else "SZ") + row["f12"]),
                    "name": row.get("f14"), "amount": value,
                    "reason": "完整测试成分中按合成成交额降序，同值按代码升序"}
                   for row, value in sorted(amounts, key=lambda item: (-item[1], item[0]["f12"]))[:3]]
    return {"list_coverage": {"received": len(list_rows), "total": total_list,
                              "complete": len(list_rows) == total_list},
            "member_coverage": {"received": len(members), "total": total_members,
                                "complete": len(members) == total_members},
            "request_success": True, "structure_parseable": True,
            "identity_basis": basis, "field_basis": basis, "event_time_basis": "UNPROVEN",
            "metrics_computable": {"constituent_counts": bool(synthetic),
                                   "active_representatives": bool(synthetic and amount_valid)},
            "current_observation_allowed": False,
            "list_valid_numeric_rows": list_valid,
            "list_invalid_numeric_rows": len(list_rows) - list_valid,
            "counts": {"up": positive, "down": negative, "flat": flat, "invalid": invalid}
                      if synthetic else None,
            "valid_numeric_rows": positive + negative + flat,
            "invalid_numeric_rows": invalid,
            "active_representatives": leaders,
            "amount_basis": basis if amount_valid else "MISSING_OR_INVALID",
            "limitations": ["原生事件时间未证明；接收时间不代表行情时间", "不是实时、当前强势、龙头或买卖建议",
                            "概念身份及字段单位须独立验证", "风险与可交易性未评估"]}


def _permission_context(auth):
    if auth is None:
        return None
    # The intent keeps the original excerpt; page files carry its traceable reference.
    return {**{key: auth[key] for key in (
        "schema_version", "source_use_basis", "known_use_constraint", "access_control_bypass",
        "user_approved", "user_acknowledges_unknown_basis", "purpose", "scope")},
        "approval_evidence": {key: auth["approval_evidence"][key]
                              for key in ("thread_id", "message_id", "quote_sha256")}}


def read_authorization(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="strict"))
    except UnicodeDecodeError:
        raise ValueError("authorization_file_invalid_utf8") from None


def _check_auth(auth, day, approved_sha, output, batch, *, stamp=None):
    expected = {"schema_version": AUTH_VERSION, "status": "USER_APPROVED_LIMITED_EXPERIMENT",
                "run_date": day, "approved_sha": approved_sha, "contract_hash": CONTRACT,
                "output": str(output), "batch_id": batch, "budget": BUDGET,
                "purpose": AUTH_PURPOSE, "scope": AUTH_SCOPE}
    if not isinstance(auth, dict) or any(auth.get(k) != v for k, v in expected.items()):
        raise ValueError("authorization_identity_mismatch")
    if (any(type(auth["scope"][key]) is not type(value) for key, value in AUTH_SCOPE.items())
            or any(type(auth["budget"][key]) is not int for key in BUDGET)
            or auth.get("single_use") is not True or auth.get("user_approved") is not True
            or auth.get("user_acknowledges_unknown_basis") is not True):
        raise ValueError("authorization_confirmation_invalid")
    if auth.get("source_use_basis") == "PROHIBITED" or auth.get("known_use_constraint") == "PROHIBITED":
        raise ValueError("authorization_source_use_prohibited")
    if (auth.get("source_use_basis") != "UNKNOWN" or auth.get("known_use_constraint") != "NO_KNOWN_PROHIBITION"
            or auth.get("access_control_bypass") is not False):
        raise ValueError("authorization_source_use_constraint_invalid")
    evidence = auth.get("approval_evidence")
    if (not isinstance(evidence, dict) or set(evidence) != {"thread_id", "message_id", "verbatim_quote", "quote_sha256"}
            or any(not isinstance(evidence[key], str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", evidence[key])
                   for key in ("thread_id", "message_id"))):
        raise ValueError("authorization_approval_reference_invalid")
    quote = evidence["verbatim_quote"]
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError("authorization_approval_quote_invalid")
    try:
        quote_bytes = quote.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError("authorization_approval_quote_invalid_unicode") from None
    if len(quote_bytes) > MAX_APPROVAL_QUOTE_BYTES:
        raise ValueError("authorization_approval_quote_exceeds_4096_bytes")
    if hashlib.sha256(quote_bytes).hexdigest() != evidence["quote_sha256"]:
        raise ValueError("authorization_approval_quote_anchor_mismatch")
    if not all(isinstance(auth.get(k), str) for k in ("window_start", "window_end")):
        raise ValueError("authorization_window_invalid")
    stamp = datetime.now(CN_TZ) if stamp is None else stamp
    start = datetime.fromisoformat(auth["window_start"])
    end = datetime.fromisoformat(auth["window_end"])
    if (start.utcoffset() != timedelta(hours=8) or end.utcoffset() != timedelta(hours=8)
            or stamp.utcoffset() != timedelta(hours=8) or end - start != timedelta(minutes=10)
            or not start <= stamp < end or start.date().isoformat() != day or end.date().isoformat() != day):
        raise ValueError("authorization_window_invalid")
    return _permission_context(auth)


def capture(*, day, approved_sha, output, batch_id, fixture=None, network=False,
            authorization=None, authorization_sha=None, state=STATE):
    started = time.perf_counter()
    directory = Path(output).resolve() if output else None
    state = Path(state).resolve()
    lock = state / "active.lock"
    owned = False
    cleaned = True
    dispatched = False
    permission = None
    try:
        if not fixture and not network:
            return safe(status="NETWORK_NOT_REQUESTED", requests_dispatched=0)
        if bool(fixture) == bool(network):
            raise ValueError("choose_fixture_or_network")
        if datetime.fromisoformat(day).date().isoformat() != day:
            raise ValueError("invalid_day")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", batch_id or ""):
            raise ValueError("invalid_batch_id")
        actual = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=2)
        if actual.returncode or not re.fullmatch(r"[0-9a-f]{40}", approved_sha or "") or actual.stdout.strip() != approved_sha:
            raise ValueError("code_identity_mismatch")
        if directory is None or directory.exists():
            raise ValueError("output_exists_or_missing")
        auth = None
        if network:
            if state != STATE.resolve():
                raise ValueError("live_state_override_forbidden")
            if datetime.now(CN_TZ).date().isoformat() != day:
                raise ValueError("run_date_not_today")
            dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=2)
            if dirty.returncode or dirty.stdout.strip():
                raise ValueError("clean_worktree_required")
            if not directory.is_relative_to(STATE.resolve()):
                raise ValueError("isolated_output_required")
            if not authorization or not authorization_sha or sha(authorization) != authorization_sha:
                raise ValueError("external_authorization_anchor_required")
            auth = read_authorization(authorization)
            permission = _check_auth(auth, day, approved_sha, directory, batch_id)
        else:
            fixture = str(Path(fixture).resolve(strict=True))
        task = {"mode": "coordinate", "day": day, "approved_sha": approved_sha,
                "batch_id": batch_id, "output": str(directory), "fixture": fixture,
                "test_only": bool(fixture), "contract_hash": CONTRACT, "started": started, "state": str(state),
                "work_deadline": started + 85, "authorization": auth,
                "authorization_path": str(Path(authorization).resolve()) if network else None}
        encode_bounded_worker_input(task)
        # Reserve the largest page envelope before any HTTP request. A 24-character
        # float covers the serialized width of both runtime monotonic fields.
        wide_float = -1.2345678901234567e-308
        widest_bk = "BK" + "\U0001d7ce" * 6  # The existing digit matcher also accepts non-ASCII digits.
        encode_bounded_worker_input(_page_task(task, "members", 3, widest_bk, 5, wide_float, wide_float))
        state.mkdir(parents=True, exist_ok=True)
        if (state / "cleanup-incomplete.json").exists():
            raise ValueError("persistent_cleanup_block")
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(str(directory))
        owned = True
        write(state / "intents" / f"{batch_id}.json", {"batch_id": batch_id, "output": str(directory),
              "contract_hash": CONTRACT, "created_at": now(), "synthetic_test_only": bool(fixture)})
        directory.mkdir(parents=True, exist_ok=False)
        write(directory / "intent.json", {**task, "created_at": now(), "budget": BUDGET,
                                          "authorization_sha": authorization_sha})
        if time.perf_counter() >= started + 85:
            raise TimeoutError("insufficient_batch_budget")
        cleaned = False
        dispatched = True
        process = run_probe_worker_process(task, max(0, int((started + 87 - time.perf_counter()) * 1000)),
            bounded_cleanup_ms=2000, hard_deadline_monotonic=started + 87, retain_streams=True, strict_utf8=True,
            worker_command=[sys.executable, "-m", "overnight_quant.scripts.run_concept_batch_worker"])
        cleaned = process.get("cleanup_complete") is True
        if not cleaned:
            write(state / "cleanup-incomplete.json", safe(batch_id=batch_id, process=process))
        elapsed = (time.perf_counter() - started) * 1000
        success = (process.get("ok") is True and cleaned and elapsed < BUDGET["batch_ms"]
                   and not (state / "cleanup-incomplete.json").exists())
        if success:
            result = json.loads((directory / "batch-result.json").read_text(encoding="utf-8"))
            success = result.get("batch_id") == batch_id and result.get("status") == "CAPTURE_COMPLETE"
        attempts = len(list(directory.glob("request-*.json")))
        raw_count = len(list(directory.glob("raw-*.json")))
        observed_count = attempts if cleaned and attempts == raw_count else None
        receipt = safe(status="PASSED" if success else "FAILED", batch_id=batch_id,
                       experiment_permission=permission,
                       synthetic_test_only=bool(fixture), elapsed_ms=elapsed, process=process,
                       requests_dispatched=result["requests_dispatched"] if success else observed_count,
                       request_count_status="KNOWN_RESPONDED" if observed_count is not None else "UNKNOWN",
                       request_attempt_markers=attempts, responses_saved=raw_count,
                       result_sha256=sha(directory / "batch-result.json") if success else None,
                       completed_at=now())
        write(directory / "execution.json", receipt)
        if owned and cleaned and not (state / "cleanup-incomplete.json").exists():
            if not lock.exists() or lock.read_text(encoding="utf-8") != str(directory):
                write(state / "cleanup-incomplete.json", safe(status="LOCK_IDENTITY_UNKNOWN", output=str(directory)))
                raise RuntimeError("lock_identity_invalid")
            lock.unlink()
            owned = False
        execution_sha = sha(directory / "execution.json")
        completion_at = now()
        total_elapsed = (time.perf_counter() - started) * 1000
        if total_elapsed >= 90000:
            return safe(status="FAILED", error="batch_deadline_exceeded", output=str(directory),
                        experiment_permission=permission,
                        requests_dispatched=None, request_count_status="UNKNOWN")
        return {**receipt, "output": str(directory), "execution_sha256": execution_sha,
                "contract_hash": CONTRACT, "total_elapsed_ms": total_elapsed,
                "completion_checked_at": completion_at}
    except Exception as exc:
        failure = safe(status="FAILED", error=f"{type(exc).__name__}:{exc}", output=str(directory) if directory else None,
                       experiment_permission=permission,
                       requests_dispatched=None if dispatched else 0,
                       request_count_status="UNKNOWN" if dispatched else "KNOWN_ZERO")
        if owned and directory and directory.exists():
            try:
                write(directory / "controller-failure.json", failure)
            except Exception:
                pass
        return failure
    finally:
        try:
            if owned and not cleaned and not (state / "cleanup-incomplete.json").exists():
                write(state / "cleanup-incomplete.json", safe(status="CLEANUP_UNCONFIRMED", output=str(directory)))
            if owned and cleaned and not (state / "cleanup-incomplete.json").exists() and lock.exists() and lock.read_text(encoding="utf-8") == str(directory):
                lock.unlink()
        except Exception:
            # The existing lock is itself a persistent stop if cleanup cannot be confirmed.
            pass


def _page_task(task, kind, page, bk, index, started, deadline):
    return {**{key: value for key, value in task.items() if key != "state"},
            "mode": "page", "kind": kind, "page": page, "bk": bk, "index": index,
            "params": parameters(kind, page, bk), "request_started": started, "request_deadline": deadline}


def page_request(task, kind, page, bk, index):
    if time.perf_counter() + 10 > task["work_deadline"]:
        raise TimeoutError("insufficient_page_and_cleanup_budget")
    directory = Path(task["output"])
    started = time.perf_counter()
    page_task = _page_task(task, kind, page, bk, index, started, started + 9)
    result = run_probe_worker_process(page_task, 10000, bounded_cleanup_ms=1000,
        hard_deadline_monotonic=started + 10, retain_streams=True, strict_utf8=True,
        worker_command=[sys.executable, "-m", "overnight_quant.scripts.run_concept_batch_worker"])
    write(directory / f"process-{index}.json", result)
    if not result.get("cleanup_complete"):
        write(Path(task["state"]) / "cleanup-incomplete.json", safe(batch_id=task["batch_id"], process=result))
        raise RuntimeError("page_cleanup_incomplete")
    if time.perf_counter() >= started + 10:
        raise TimeoutError("page_receipt_deadline_exceeded")
    if not result.get("ok"):
        raise RuntimeError("page_request_failed:" + str(result.get("error_code")))
    raw = json.loads((directory / f"raw-{index}.json").read_text(encoding="utf-8"))
    if raw["batch_id"] != task["batch_id"] or raw["params"] != page_task["params"]:
        raise ValueError("raw_identity_mismatch")
    if raw["status"] != 200:
        raise ValueError("http_status_not_success")
    return base64.b64decode(raw["body_base64"], validate=True)


def coordinate(task):
    directory = Path(task["output"])
    index = 0
    permission = None
    try:
        permission = _permission_context(task.get("authorization"))
        results = {}
        for kind in ("list", "members"):
            seen, rows, total = set(), [], None
            bk = results.get("selected_bk") if kind == "members" else None
            for page in range(1, 4):
                body = page_request(task, kind, page, bk, index)
                index += 1
                total, received = decode_page(body, kind, page, total, seen)
                rows.extend(received)
                if kind == "members" and total > 300:
                    raise ValueError("member_page_limit_exceeded")
                if [row["f12"] for row in rows] != sorted(row["f12"] for row in rows):
                    raise ValueError("page_order_drift")
                if len(rows) == total:
                    break
            if not rows:
                raise ValueError("empty_" + kind)
            if kind == "list" and len(rows) != min(total, 300):
                raise ValueError("list_coverage_invalid")
            if kind == "members" and len(rows) != total:
                raise ValueError("member_page_limit_exceeded")
            results[kind] = rows
            results["total_" + kind] = total
            if kind == "list":
                results["selected_bk"] = min(row["f12"] for row in rows)
        summary = summarize(results["list"], results["members"], total_list=results["total_list"],
                            total_members=results["total_members"], synthetic=task["test_only"])
        selected = next(row for row in results["list"] if row["f12"] == results["selected_bk"])
        payload = safe(status="CAPTURE_COMPLETE", batch_id=task["batch_id"], run_date=task["day"],
                       experiment_permission=permission,
                       synthetic_test_only=task["test_only"], contract_hash=CONTRACT,
                       selected_bk=results["selected_bk"], selected_name=selected.get("f14"),
                       selected_daily_change=_number(selected.get("f3")) if task["test_only"] else None,
                       selected_raw=selected, list_raw=results["list"], member_raw=results["members"],
                       selection_rule=PLAN["selection"], requests_dispatched=index,
                       capture_completed_at=now(), intent_sha256=sha(directory / "intent.json"),
                       raw_anchors=[sha(directory / f"raw-{i}.json") for i in range(index)],
                       process_anchors=[sha(directory / f"process-{i}.json") for i in range(index)],
                       request_anchors=[sha(directory / f"request-{i}.json") for i in range(index)], **summary)
        write(directory / "batch-result.json", payload)
        if time.perf_counter() >= task["work_deadline"]:
            raise TimeoutError("batch_work_deadline_exceeded")
        return {"ok": True, "payload": {"requests_dispatched": index}}
    except Exception as exc:
        failure = safe(status="FAILED", batch_id=task["batch_id"], error=f"{type(exc).__name__}:{exc}",
                       experiment_permission=permission,
                       requests_completed=index, request_count_status="UNKNOWN_IF_DISPATCH_FAILED", completed_at=now())
        write(directory / "failure.json", failure)
        return {"ok": False, "error": failure["error"]}


def load_batch(directory, report_path, report_sha, confirmation_sha):
    """Accept only an externally anchored report and the final launcher confirmation."""
    path = Path(directory).resolve()
    if not re.fullmatch(r"[0-9a-f]{64}", report_sha or "") or sha(report_path) != report_sha:
        raise ValueError("returned_report_anchor_mismatch")
    confirmation_path = path / "final-confirmation.json"
    if not re.fullmatch(r"[0-9a-f]{64}", confirmation_sha or "") or sha(confirmation_path) != confirmation_sha:
        raise ValueError("final_confirmation_anchor_mismatch")
    confirmation = json.loads(confirmation_path.read_text(encoding="utf-8"))
    start, checked, frequency = (confirmation.get(k) for k in
                                 ("launcher_started_ticks", "report_checked_ticks", "stopwatch_frequency"))
    if (confirmation.get("schema_version") != CONFIRM_VERSION or confirmation.get("status") != "EXECUTION_SUCCEEDED"
            or confirmation.get("contract_hash") != CONTRACT or confirmation.get("output") != str(path)
            or confirmation.get("report_sha256") != report_sha
            or any(type(value) is not int for value in (start, checked, frequency))
            or start < 0 or frequency <= 0 or checked < start or checked - start >= 90 * frequency):
        raise ValueError("final_confirmation_invalid")
    elapsed = (checked - start) * 1000 / frequency
    stated_elapsed = confirmation.get("execution_elapsed_ms")
    if (_number(stated_elapsed) is None or not math.isclose(float(stated_elapsed), elapsed, abs_tol=0.000001)):
        raise ValueError("final_confirmation_elapsed_invalid")
    ticket_path = Path(confirmation.get("ticket_path") or "")
    ticket_sha = confirmation.get("ticket_sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", ticket_sha or "") or sha(ticket_path) != ticket_sha:
        raise ValueError("launcher_ticket_anchor_mismatch")
    ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
    if (ticket.get("schema_version") != LAUNCH_VERSION or ticket.get("contract_hash") != CONTRACT
            or ticket.get("output") != str(path) or ticket.get("batch_id") != confirmation.get("batch_id")
            or ticket.get("launcher_started_ticks") != start or ticket.get("stopwatch_frequency") != frequency
            or type(ticket.get("launcher_pid")) is not int or ticket["launcher_pid"] <= 0
            or not re.fullmatch(r"[0-9a-f]{32}", ticket.get("nonce") or "")
            or ticket.get("synthetic_test_only") is not confirmation.get("synthetic_test_only")):
        raise ValueError("launcher_ticket_mismatch")
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    if (report.get("status") != "EXECUTION_CANDIDATE" or report.get("candidate_status") != "PASSED"
            or report.get("contract_hash") != CONTRACT or report.get("batch_id") != confirmation.get("batch_id")
            or report.get("synthetic_test_only") is not confirmation.get("synthetic_test_only")
            or report.get("output") != str(path) or _number(report.get("total_elapsed_ms")) is None
            or not 0 <= report["total_elapsed_ms"] < 90000
            or report.get("supervisor_cleanup_complete") is not True
            or _number(report.get("supervisor_started_monotonic")) is None
            or _number(report.get("supervisor_elapsed_ms")) is None
            or not report["total_elapsed_ms"] <= report["supervisor_elapsed_ms"] < 90000):
        raise ValueError("returned_report_not_confirmed")
    execution_sha = report.get("execution_sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", execution_sha or "") or sha(path / "execution.json") != execution_sha:
        raise ValueError("execution_anchor_mismatch")
    receipt = json.loads((path / "execution.json").read_text(encoding="utf-8"))
    if any(report.get(k) != receipt.get(k) for k in
           ("schema_version", "batch_id", "synthetic_test_only", "elapsed_ms", "process", "result_sha256",
            "requests_dispatched", "request_count_status", "request_attempt_markers", "responses_saved",
            "experiment_permission")):
        raise ValueError("returned_report_receipt_mismatch")
    if (receipt.get("status") != "PASSED" or not receipt.get("process", {}).get("ok")
            or not receipt.get("process", {}).get("cleanup_complete")
            or _number(receipt.get("elapsed_ms")) is None or not 0 <= receipt["elapsed_ms"] < 90000):
        raise ValueError("batch_not_confirmed")
    if report["total_elapsed_ms"] < receipt["elapsed_ms"]:
        raise ValueError("completion_time_order_invalid")
    if report["supervisor_elapsed_ms"] > (checked - start) * 1000 / frequency:
        raise ValueError("outer_elapsed_mismatch")
    result_path = path / "batch-result.json"
    if sha(result_path) != receipt.get("result_sha256"):
        raise ValueError("batch_result_anchor_mismatch")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if (result.get("status") != "CAPTURE_COMPLETE" or result.get("batch_id") != receipt.get("batch_id")
            or result.get("batch_id") != report.get("batch_id") or result.get("contract_hash") != CONTRACT
            or type(result.get("synthetic_test_only")) is not bool):
        raise ValueError("batch_identity_mismatch")
    count = result.get("requests_dispatched")
    if (type(count) is not int or not 2 <= count <= 6
            or any(len(result.get(key, [])) != count for key in ("raw_anchors", "process_anchors", "request_anchors"))):
        raise ValueError("raw_catalog_invalid")
    if receipt.get("requests_dispatched") != count or receipt.get("request_attempt_markers") != count:
        raise ValueError("request_count_mismatch")
    for prefix in ("raw", "request", "process"):
        actual = {item.name for item in path.glob(f"{prefix}-*.json")}
        expected = {f"{prefix}-{index}.json" for index in range(count)}
        if actual != expected:
            raise ValueError("unexpected_page_material")
    if (path / "failure.json").exists() or (path / "controller-failure.json").exists():
        raise ValueError("failed_material_cannot_be_success")
    if sha(path / "intent.json") != result.get("intent_sha256"):
        raise ValueError("intent_anchor_mismatch")
    intent = json.loads((path / "intent.json").read_text(encoding="utf-8"))
    if (intent.get("batch_id") != result["batch_id"] or intent.get("contract_hash") != CONTRACT
            or intent.get("output") != str(path) or intent.get("day") != result.get("run_date")
            or intent.get("test_only") is not result["synthetic_test_only"]
            or receipt.get("synthetic_test_only") is not result["synthetic_test_only"]
            or ticket.get("approved_sha") != intent.get("approved_sha") or ticket.get("day") != intent.get("day")
            or ticket_path.resolve() != (Path(intent["state"]) / "launcher-tickets" / result["batch_id"] / "ticket.json").resolve()):
        raise ValueError("intent_identity_mismatch")
    auth = intent.get("authorization")
    permission = None
    if result["synthetic_test_only"]:
        if auth is not None:
            raise ValueError("fixture_authorization_forbidden")
    else:
        if not re.fullmatch(r"[0-9a-f]{64}", intent.get("authorization_sha") or ""):
            raise ValueError("authorization_anchor_missing")
        permission = _check_auth(auth, result["run_date"], intent["approved_sha"], path, result["batch_id"],
                                 stamp=datetime.fromisoformat(intent["created_at"]))
    permission_hash = stable_hash(permission)
    if any(stable_hash(item.get("experiment_permission")) != permission_hash for item in (result, receipt, report)):
        raise ValueError("experiment_permission_mismatch")
    intent_started = _number(intent.get("started"))
    supervisor_started = _number(report.get("supervisor_started_monotonic"))
    outer_started, outer_checked = start / frequency, checked / frequency
    if (intent_started is None or supervisor_started is None
            or not outer_started <= supervisor_started <= intent_started
            or intent_started + report["total_elapsed_ms"] / 1000 > outer_checked
            or supervisor_started + report["supervisor_elapsed_ms"] / 1000 > outer_checked):
        raise ValueError("outer_time_order_invalid")
    seen = {"list": set(), "members": set()}
    rows = {"list": [], "members": []}
    totals = {"list": None, "members": None}
    pages = {"list": 0, "members": 0}
    for index, digest in enumerate(result["raw_anchors"]):
        raw_path = path / f"raw-{index}.json"
        if sha(raw_path) != digest:
            raise ValueError("raw_anchor_mismatch")
        process_path, request_path = path / f"process-{index}.json", path / f"request-{index}.json"
        if sha(process_path) != result["process_anchors"][index] or sha(request_path) != result["request_anchors"][index]:
            raise ValueError("request_or_process_anchor_mismatch")
        page_process = json.loads(process_path.read_text(encoding="utf-8"))
        if (not page_process.get("ok") or not page_process.get("cleanup_complete")
                or _number(page_process.get("elapsed_ms")) is None
                or not 0 <= page_process["elapsed_ms"] <= BUDGET["page_ms"]):
            raise ValueError("page_process_invalid")
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        kind = raw.get("kind")
        if kind not in rows or (kind == "list" and pages["members"]):
            raise ValueError("raw_order_invalid")
        pages[kind] += 1
        bk = result["selected_bk"] if kind == "members" else None
        if (raw.get("batch_id") != result["batch_id"] or raw.get("index") != index
                or raw.get("page") != pages[kind] or raw.get("params") != parameters(kind, pages[kind], bk)
                or raw.get("url") != ENDPOINT or raw.get("status") != 200 or raw.get("body_complete") is not True
                or raw.get("synthetic_test_only") is not result["synthetic_test_only"]):
            raise ValueError("raw_contract_mismatch")
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if any(request.get(k) != raw.get(k) for k in ("batch_id", "kind", "page", "index", "params", "synthetic_test_only")):
            raise ValueError("request_binding_mismatch")
        if any(stable_hash(item.get("experiment_permission")) != permission_hash for item in (request, raw)):
            raise ValueError("experiment_permission_mismatch")
        start, received = (datetime.fromisoformat(raw[k]) for k in ("request_started_at", "response_completed_at"))
        if (start.utcoffset() != timedelta(hours=8) or received.utcoffset() != timedelta(hours=8)
                or start > received or (not result["synthetic_test_only"] and received.date().isoformat() != result["run_date"])):
            raise ValueError("capture_time_invalid")
        if not result["synthetic_test_only"]:
            _check_auth(auth, result["run_date"], intent["approved_sha"], path, result["batch_id"], stamp=start)
        clocks = [raw.get(k) for k in ("dispatch_monotonic", "request_started_monotonic",
                                      "response_completed_monotonic", "raw_write_started_monotonic")]
        if (any(type(value) not in (int, float) or not math.isfinite(value) for value in clocks)
                or clocks != sorted(clocks) or clocks[-1] >= clocks[0] + 9
                or clocks[0] < intent_started or clocks[-1] > outer_checked):
            raise ValueError("page_timing_invalid")
        byte_count = raw.get("byte_count")
        if type(byte_count) is not int or not 0 <= byte_count <= PLAN["max_response_bytes"]:
            raise ValueError("raw_byte_count_invalid")
        body = base64.b64decode(raw["body_base64"], validate=True)
        if len(body) > PLAN["max_response_bytes"]:
            raise ValueError("raw_body_size_exceeded")
        if len(body) != byte_count or hashlib.sha256(body).hexdigest() != raw["raw_sha256"]:
            raise ValueError("raw_bytes_mismatch")
        totals[kind], received = decode_page(body, kind, pages[kind], totals[kind], seen[kind])
        rows[kind].extend(received)
        if [row["f12"] for row in rows[kind]] != sorted(row["f12"] for row in rows[kind]):
            raise ValueError("page_order_drift")
    if (not rows["list"] or totals["list"] is None
            or len(rows["list"]) != min(totals["list"], 300)
            or pages["list"] != (min(totals["list"], 300) + 99) // 100):
        raise ValueError("list_coverage_invalid")
    if (not rows["members"] or totals["members"] is None or totals["members"] > 300
            or len(rows["members"]) != totals["members"]
            or pages["members"] != (totals["members"] + 99) // 100):
        raise ValueError("batch_coverage_invalid")
    if min(row["f12"] for row in rows["list"]) != result["selected_bk"]:
        raise ValueError("selection_not_deterministic")
    selected = next(row for row in rows["list"] if row["f12"] == result["selected_bk"])
    if (result.get("selected_raw") != selected or result.get("list_raw") != rows["list"]
            or result.get("member_raw") != rows["members"]
            or result.get("selected_name") != selected.get("f14")
            or result.get("selected_daily_change") != (_number(selected.get("f3")) if result["synthetic_test_only"] else None)):
        raise ValueError("raw_projection_mismatch")
    summary = summarize(rows["list"], rows["members"], total_list=totals["list"],
                        total_members=totals["members"], synthetic=result["synthetic_test_only"])
    if any(result.get(k) != value for k, value in summary.items()):
        raise ValueError("replayed_summary_mismatch")
    return result
