"""One dated THS response, not a hotspot ranking or a qualified data source."""
from __future__ import annotations

import base64
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from overnight_quant.data.mootdx_node_benchmark import write_benchmark_json_atomic as write_once
from overnight_quant.data.probe_worker_process import run_probe_worker_process

VERSION = "ths_single_day_sample_v1"
PERMISSION = "ths_single_day_permission_v1"
ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "overnight_quant/data/cache/ths-single-sample"
CN = timezone(timedelta(hours=8))
PLAN = {
    "version": VERSION, "method": "GET", "max_requests": 1, "max_pages": 1,
    "url": "http://zx.10jqka.com.cn/event/api/getharden/date/{D}/orderby/date/orderway/desc/charset/GBK/",
    "max_response_bytes": 1048576, "io_seconds": 5, "execution_seconds": 10,
    "worker_hard_seconds": 8, "cleanup_ms": 1000,
    "redirects": 0, "retries": 0, "fallbacks": 0,
    "transport_authentication": "CLEARTEXT_HTTP_NOT_TLS_AUTHENTICATED",
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


CONTRACT = digest(json.dumps(PLAN, sort_keys=True).encode("utf-8"))


def now():
    return datetime.now(CN).isoformat()


def safe(**values):
    return {"qualification": "0/3", "data_ready": False, "hard_gate_authorized": False,
            "records": [], "candidates": [], "tickets": [], "orders": [], **values}


def stamp(value):
    result = datetime.fromisoformat(value)
    if result.utcoffset() != timedelta(hours=8):
        raise ValueError("beijing_timestamp_required")
    return result


def _load(path, limit=65536):
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("input_byte_limit")
    return raw, json.loads(raw.decode("utf-8-sig", "strict"))


def check_permission(path, anchor, *, target, head, output, batch, evaluated_at):
    from overnight_quant.data.quote_observation_permit import (
        LOCAL_PERMIT_SCHEMA, _check_approval_reference,
    )
    raw, auth = _load(path)
    if digest(raw) != anchor:
        raise ValueError("approval_external_sha_mismatch")
    expected = {"schema": PERMISSION, "status": "USER_APPROVED", "contract_hash": CONTRACT,
                "target_date": target, "approved_sha": head, "batch_id": batch,
                "output": str(output), "worktree": str(ROOT), "max_requests": 1,
                "purpose": "PERSONAL_INTERNAL_SINGLE_DAY_STRUCTURE_SAMPLE",
                "source_use_basis": "UNKNOWN", "known_use_constraint": "NO_KNOWN_PROHIBITION",
                "user_approved": True, "acknowledges_unknown_basis": True,
                "non_public": True, "single_use": True, "no_access_control_bypass": True}
    if (not isinstance(auth, dict) or set(auth) != set(expected) | {"window_start", "window_end", "approval_reference"}
            or any(auth.get(k) != v or (isinstance(v, bool) and type(auth.get(k)) is not bool)
                   or (isinstance(v, int) and not isinstance(v, bool) and type(auth.get(k)) is not int)
                   for k, v in expected.items())):
        raise ValueError("ths_permission_identity_or_scope_invalid")
    start, end = stamp(auth["window_start"]), stamp(auth["window_end"])
    if not start <= evaluated_at < end or start.date() != end.date():
        raise ValueError("permission_window_expired_or_invalid")
    if date.fromisoformat(target) >= start.date():
        raise ValueError("target_must_precede_execution_date")
    _check_approval_reference(LOCAL_PERMIT_SCHEMA, auth["approval_reference"], head)
    if stamp(auth["approval_reference"]["operator_check"]["checked_at"]) > evaluated_at:
        raise ValueError("approval_check_in_future")
    return {k: auth[k] for k in ("schema", "source_use_basis", "purpose", "window_start", "window_end")}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def parse_body(body, content_type):
    # URL text is not an encoding declaration. No lossy/guessing fallback.
    match = re.search(r"charset\s*=\s*[\"']?([a-zA-Z0-9_-]+)", content_type, re.I)
    encoding = match.group(1).lower() if match else "utf-8-sig" if body.startswith(b"\xef\xbb\xbf") else "utf-8"
    if encoding not in {"utf-8", "utf8", "utf-8-sig", "gbk", "gb18030", "gb2312"}:
        raise ValueError("unsupported_declared_encoding")
    text = body.decode(encoding, "strict")
    text.encode("utf-8", "strict")
    document = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")))
    json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")
    if (not isinstance(document, dict) or type(document.get("errocode")) not in (int, str)
            or document["errocode"] not in (0, "0")):
        raise ValueError("source_error_or_envelope_unknown")
    rows = document.get("data")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("rows_structure_unknown")
    clues = []
    for index, row in enumerate(rows):
        reason = row.get("reason")
        readable = isinstance(reason, str) and bool(reason.strip()) and all(ord(c) >= 32 or c in "\r\n\t" for c in reason)
        clues.append({"row": index, "source_values": row, "readable_attribution": readable,
                      "identity_semantics": "UNKNOWN", "date_semantics": "UNKNOWN",
                      "eligible_for_current_ranking": False})
    return {"encoding": encoding, "encoding_basis": "HTTP_CONTENT_TYPE" if match else "BOM" if body.startswith(b"\xef\xbb\xbf") else "STRICT_UTF8_ONLY_NOT_UPSTREAM_DECLARED",
            "received_rows": len(rows), "readable_attribution_rows": sum(x["readable_attribution"] for x in clues),
            "returned_total_raw": document.get("total"), "coverage_declaration_present": "total" in document,
            "coverage": "UNKNOWN",
            "source_time_raw": {k: document[k] for k in ("date", "time", "datetime", "timestamp") if k in document},
            "source_time_semantics": "UNKNOWN", "clues": clues,
            "hotspot_ranking": [], "core_stock_candidates": [],
            "empty_does_not_mean_no_hotspots": True}


def _raw_save(path, body):
    # Store bytes in the existing exclusive, fsynced JSON envelope, before decoding.
    write_once(path, {"layer": "HTTP_ENTITY_BYTES_AS_RECEIVED_NOT_TLS_AUTHENTICATED",
                      "byte_count": len(body), "sha256": digest(body),
                      "body_b64": base64.b64encode(body).decode("ascii")})
    raw, saved = _load(path, 2 * PLAN["max_response_bytes"])
    if base64.b64decode(saved["body_b64"], validate=True) != body:
        raise ValueError("raw_readback_mismatch")
    return digest(raw)


def request_sample(task):
    """Called in the existing bounded child, never sends approval content."""
    output = Path(task["output"])
    if task.get("contract_hash") != CONTRACT or task.get("url") != PLAN["url"].format(D=task.get("target_date")):
        raise ValueError("worker_route_mismatch")
    if not task.get("fixture"):
        from overnight_quant.data.minute_local_prepare_worker import parent_chain
        if task.get("controller_pid") not in parent_chain()[1:3]:
            raise ValueError("supervising_controller_required")
        if (STATE / "cleanup-incomplete.json").exists():
            raise ValueError("persistent_cleanup_block")
        actual = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], timeout=1, text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"], timeout=1, text=True)
        if actual != task.get("approved_sha") or dirty.strip():
            raise ValueError("worker_code_identity_invalid")
        permission = check_permission(task.get("approval"), task.get("approval_sha"), target=task["target_date"],
                         head=task["approved_sha"], output=output, batch=task["batch_id"],
                         evaluated_at=datetime.now(CN))
        _, intent = _load(output / "intent.json")
        if (any(intent.get(key) != value for key, value in task.items())
                or intent.get("approval_sha256") != task["approval_sha"]
                or intent.get("approved_sha") != task["approved_sha"]
                or not output.is_relative_to(STATE.resolve())
                or (STATE / "active.lock").read_text(encoding="utf-8") != str(output)):
            raise ValueError("worker_intent_binding_invalid")
    started = time.perf_counter()
    deadline = min(task["work_deadline"], started + PLAN["io_seconds"])
    stage = "before_dispatch"
    body = bytearray()
    complete = False
    response = None
    result = {"status": "FAILED", "requests_attempted": 0, "actual_http_requests": 0,
              "started_at": now(), "started_monotonic": started, "source_use_basis": "UNKNOWN"}
    try:
        if task["fixture"]:
            _, fixture = _load(task["fixture"], 2 * PLAN["max_response_bytes"])
        else:
            fixture = None
            if any(urlsplit(value).username is not None or urlsplit(value).password is not None
                   for key, value in urllib.request.getproxies().items() if key in ("http", "https", "all")):
                raise ValueError("credential_bearing_proxy_not_allowed")
        if time.perf_counter() >= deadline:
            raise TimeoutError("io_absolute_deadline")
        write_once(output / "request.json", {"method": "GET", "url": task["url"],
                   "headers": {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"},
                   "target_date": task["target_date"], "started_at": now(),
                   "dispatch_status": "ATTEMPT_MAY_HAVE_BEEN_SENT", "synthetic_test_only": bool(fixture)})
        result["requests_attempted"] = 1
        result["actual_http_requests"] = 0 if fixture else None
        stage = "connect_read"
        if fixture:
            if fixture.get("error"):
                raise TimeoutError("fixture_timeout")
            encoded = base64.b64decode(fixture["body_b64"], validate=True)
            result.update(http_status=fixture.get("status", 200), content_type=fixture.get("content_type", ""),
                          location=fixture.get("location"), content_encoding=fixture.get("content_encoding", "identity"),
                          declared_length=fixture.get("declared_length"))
            body.extend(encoded[:PLAN["max_response_bytes"]])
            complete = len(encoded) <= PLAN["max_response_bytes"]
        else:
            if not stamp(permission["window_start"]) <= datetime.now(CN) < stamp(permission["window_end"]):
                raise ValueError("permission_expired_before_dispatch")
            req = urllib.request.Request(task["url"], headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}, method="GET")
            # Standard proxy configuration remains in force; never retry direct.
            opener = urllib.request.build_opener(NoRedirect())
            try:
                response = opener.open(req, timeout=min(5, max(.001, deadline - time.perf_counter())))
            except urllib.error.HTTPError as exc:
                response = exc
            result["actual_http_requests"] = 1
            result.update(http_status=response.code, content_type=response.headers.get("Content-Type", ""),
                          location=response.headers.get("Location"), content_encoding=response.headers.get("Content-Encoding", "identity"),
                          declared_length=response.headers.get("Content-Length"))
            while len(body) < PLAN["max_response_bytes"]:
                if time.perf_counter() >= deadline:
                    raise TimeoutError("io_absolute_deadline")
                chunk = response.read1(min(16384, PLAN["max_response_bytes"] - len(body)))
                if not chunk:
                    complete = True
                    break
                body.extend(chunk)
                # Incremental fsynced chunks survive later read/worker failure.
                _raw_save(output / f"received-{len(body):07d}.json", chunk)
            if len(body) == PLAN["max_response_bytes"]:
                complete = str(result.get("declared_length")) == str(len(body)) and getattr(response, "length", None) == 0
        result["response_completed_at"] = now()
        result["response_completed_monotonic"] = time.perf_counter()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}:{exc}"
        result["failure_stage"] = stage
    finally:
        if response is not None:
            try:
                response.close()
            except Exception as exc:
                result.update(error=f"{type(exc).__name__}:{exc}", failure_stage="response_close")
        if body or "http_status" in result:
            try:
                stage = "raw_seal"
                result["raw_sha256"] = _raw_save(output / "response-bytes.json", bytes(body))
                result["raw_sealed_at"] = now()
                result["raw_sealed_monotonic"] = time.perf_counter()
            except Exception as exc:
                result.update(error=f"{type(exc).__name__}:{exc}", failure_stage=stage)
        result.update(byte_count=len(body), body_complete=complete,
                      io_elapsed_ms=(time.perf_counter() - started) * 1000)
    try:
        if result.get("error"):
            raise ValueError(result["error"])
        stage = "response_limits"
        if not complete or len(body) > PLAN["max_response_bytes"]:
            raise ValueError("response_byte_limit_or_incomplete")
        declared = result.get("declared_length")
        if declared is not None and (not re.fullmatch(r"[0-9]+", str(declared)) or int(declared) != len(body)):
            raise ValueError("declared_response_length_mismatch")
        if result["http_status"] != 200:
            raise ValueError("redirect_stopped" if 300 <= result["http_status"] < 400 else "http_status_rejected")
        if result["content_encoding"].lower() not in ("", "identity"):
            raise ValueError("content_encoding_not_supported")
        if time.perf_counter() >= deadline:
            raise TimeoutError("io_and_raw_seal_deadline")
        stage = "decode_json"
        result["parsed"] = parse_body(bytes(body), result["content_type"])
        if time.perf_counter() >= task["work_deadline"]:
            raise TimeoutError("worker_work_deadline")
        result["status"] = "SAMPLE_PARSED"
    except Exception as exc:
        result.setdefault("failure_stage", stage)
        result.setdefault("error", f"{type(exc).__name__}:{exc}")
    result.update(completed_at=now(), completed_monotonic=time.perf_counter())
    write_once(output / "response.json", result)
    return result


def capture(*, target=None, head=None, output=None, batch=None, fixture=None,
            execute=False, approval=None, approval_sha=None, state=STATE, started=None):
    started = time.perf_counter() if started is None else started
    deadline = started + PLAN["execution_seconds"]
    state = Path(state).resolve()
    directory = Path(output).resolve() if output else None
    owned = dispatched = False
    cleaned = True
    lock = state / "active.lock"
    report = safe(status="FAILED", requests_attempted=0, actual_http_requests=0)
    try:
        if not fixture and not execute:
            return safe(status="NETWORK_NOT_REQUESTED", plan=PLAN, contract_hash=CONTRACT,
                        permission_version=PERMISSION, requests_attempted=0, actual_http_requests=0, workers=0)
        if bool(fixture) == bool(execute):
            raise ValueError("choose_fixture_or_execute")
        if not isinstance(target, str) or date.fromisoformat(target).isoformat() != target:
            raise ValueError("target_date_required")
        if not re.fullmatch(r"[a-f0-9]{40}", head or "") or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", batch or ""):
            raise ValueError("code_sha_and_batch_required")
        if directory is None or directory.exists() or directory.is_relative_to(ROOT) and not directory.is_relative_to(STATE.resolve()):
            raise ValueError("new_isolated_output_required")
        actual = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], timeout=1, text=True).strip()
        if head != actual:
            raise ValueError("fixed_code_mismatch")
        if execute:
            if state != STATE.resolve() or not directory.is_relative_to(state):
                raise ValueError("live_state_or_output_mismatch")
            dirty = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"], timeout=1, text=True)
            if dirty.strip():
                raise ValueError("clean_worktree_required")
            permission = check_permission(approval, approval_sha, target=target, head=head,
                                          output=directory, batch=batch, evaluated_at=datetime.now(CN))
        else:
            fixture = str(Path(fixture).resolve(strict=True))
            permission = {"status": "TEST_ONLY", "source_use_basis": "UNKNOWN"}
        state.mkdir(parents=True, exist_ok=True)
        if (state / "cleanup-incomplete.json").exists():
            raise ValueError("persistent_cleanup_block")
        with lock.open("x", encoding="utf-8") as handle:
            handle.write(str(directory))
        owned = True
        write_once(state / "intents" / f"{batch}.json", {"batch_id": batch, "output": str(directory), "created_at": now()})
        directory.mkdir(parents=True, exist_ok=False)
        io_started = time.perf_counter()
        task = {"contract_hash": CONTRACT, "target_date": target, "output": str(directory),
                "url": PLAN["url"].format(D=target), "fixture": fixture,
                "work_deadline": min(started + 7, io_started + 5), "approved_sha": head, "batch_id": batch,
                "controller_pid": os.getpid(),
                "approval": str(Path(approval).resolve()) if approval else None,
                "approval_sha": approval_sha}
        write_once(directory / "intent.json", {**task, "approved_sha": head, "batch_id": batch,
                   "approval_sha256": approval_sha, "permission": permission, "started_at": now(),
                   "started_monotonic": started, "execution_deadline_monotonic": deadline,
                   "synthetic_test_only": bool(fixture)})
        if time.perf_counter() >= started + 1:
            raise TimeoutError("preparation_budget_exceeded")
        cleaned = False
        dispatched = True
        process = run_probe_worker_process(task, 8000, bounded_cleanup_ms=PLAN["cleanup_ms"],
            hard_deadline_monotonic=task["work_deadline"] + 1, strict_utf8=True, retain_streams=True,
            worker_command=[sys.executable, "-m", "overnight_quant.scripts.run_ths_single_sample", "--worker"])
        cleaned = process.get("cleanup_complete") is True
        if not cleaned:
            write_once(state / "cleanup-incomplete.json", {"output": str(directory), "process": process})
        response = None
        if (directory / "response.json").is_file():
            _, response = _load(directory / "response.json", 4 * PLAN["max_response_bytes"])
        report.update(version=VERSION, contract_hash=CONTRACT, batch_id=batch, target_date=target,
                      approved_sha=head, synthetic_test_only=bool(fixture), source_use_basis="UNKNOWN",
                      process=process, cleanup_complete=cleaned, permission=permission,
                      requests_attempted=(response or {}).get("requests_attempted"),
                      actual_http_requests=0 if fixture else (response or {}).get("actual_http_requests"),
                      response_sha256=digest((directory / "response.json").read_bytes()) if response else None)
        report["intent_sha256"] = digest((directory / "intent.json").read_bytes())
        report["request_sha256"] = digest((directory / "request.json").read_bytes()) if (directory / "request.json").exists() else None
        report["partial_artifacts"] = [{"file": path.name, "sha256": digest(path.read_bytes())}
            for path in sorted(directory.glob("received-*.json"))]
        if process.get("ok") is not True or not cleaned or not response or response["status"] != "SAMPLE_PARSED":
            raise ValueError((response or {}).get("error", process.get("error") or "sample_failed"))
        if time.perf_counter() >= deadline:
            raise TimeoutError("execution_deadline")
        report["status"] = "SAMPLE_PARSED"
    except Exception as exc:
        report.update(status="FAILED", error=f"{type(exc).__name__}:{exc}")
        if dispatched and "process" not in report:
            report.update(requests_attempted=None, actual_http_requests=0 if fixture else None)
    finally:
        if owned:
            try:
                if not cleaned:
                    if not (state / "cleanup-incomplete.json").exists():
                        write_once(state / "cleanup-incomplete.json", {"output": str(directory), "status": "UNCONFIRMED"})
                elif lock.read_text(encoding="utf-8") == str(directory):
                    lock.unlink()
                else:
                    raise ValueError("lock_identity_mismatch")
            except Exception as exc:
                report.update(status="FAILED", error=f"cleanup:{type(exc).__name__}:{exc}", cleanup_complete=False)
    if owned and directory and directory.exists():
        try:
            report["completed_at"] = now()
            write_once(directory / "execution.json", report)
            actual_check = time.perf_counter()
            if actual_check >= deadline:
                report.update(status="FAILED", error="execution_deadline_after_report")
            # One completion record, not a recursive confirmation chain.
            completion = {"version": VERSION, "status": report["status"],
                "execution_sha256": digest((directory / "execution.json").read_bytes()),
                "checked_monotonic": actual_check, "execution_elapsed_ms": (actual_check - started) * 1000,
                "deadline_monotonic": deadline, "checked_at": now()}
            write_once(directory / "completion.json", completion)
            completion_sha = digest((directory / "completion.json").read_bytes())
            final_check = time.perf_counter()
            if final_check >= deadline:
                return safe(status="FAILED", error="completion_write_deadline", actual_http_requests=report["actual_http_requests"], output=str(directory))
            return {**report, "output": str(directory), "execution_elapsed_ms": completion["execution_elapsed_ms"],
                    "final_check_elapsed_ms": (final_check - started) * 1000,
                    "completion_sha256": completion_sha}
        except Exception as exc:
            report.update(status="FAILED", error=f"final_save:{type(exc).__name__}:{exc}")
    return report


def verify(directory, completion_sha, returned_path, returned_sha):
    directory = Path(directory)
    returned_raw, returned = _load(returned_path)
    if digest(returned_raw) != returned_sha or returned.get("output") != str(directory.resolve()):
        raise ValueError("returned_result_external_anchor_invalid")
    launcher_elapsed = returned.get("launcher_elapsed_ms")
    if (returned.get("launcher_version") != VERSION or type(launcher_elapsed) not in (int, float)
            or not math.isfinite(launcher_elapsed) or not 0 <= launcher_elapsed < 10000):
        raise ValueError("launcher_budget_unconfirmed")
    report_bytes = (directory / "python-result.json").read_bytes()
    if digest(report_bytes) != returned.get("launcher_report_sha256"):
        raise ValueError("launcher_report_anchor_invalid")
    child_returned = json.loads(report_bytes.decode("utf-8", "strict"))
    identity_fields = ("version", "contract_hash", "batch_id", "target_date", "approved_sha", "output",
                       "synthetic_test_only", "completion_sha256", "actual_http_requests", "requests_attempted")
    if any(returned.get(k) != child_returned.get(k) for k in identity_fields):
        raise ValueError("launcher_result_mismatch")
    raw, completion = _load(directory / "completion.json")
    if digest(raw) != completion_sha or completion.get("version") != VERSION:
        raise ValueError("completion_external_anchor_invalid")
    raw, execution = _load(directory / "execution.json")
    if digest(raw) != completion["execution_sha256"] or execution.get("contract_hash") != CONTRACT:
        raise ValueError("execution_anchor_invalid")
    raw, intent = _load(directory / "intent.json")
    if digest(raw) != execution.get("intent_sha256") or any(intent.get(k) != execution.get(k) for k in ("approved_sha", "batch_id")):
        raise ValueError("intent_identity_or_anchor_invalid")
    if execution.get("request_sha256"):
        raw, request = _load(directory / "request.json")
        if (digest(raw) != execution["request_sha256"] or request.get("target_date") != execution["target_date"]
                or request.get("url") != PLAN["url"].format(D=execution["target_date"]) or request.get("method") != "GET"
                or request.get("headers") != {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}):
            raise ValueError("request_identity_or_anchor_invalid")
    for artifact in execution.get("partial_artifacts", []):
        if (not re.fullmatch(r"received-[0-9]{7}\.json", artifact["file"])
                or digest((directory / artifact["file"]).read_bytes()) != artifact["sha256"]):
            raise ValueError("partial_artifact_anchor_invalid")
    elapsed = completion.get("execution_elapsed_ms")
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or not 0 <= elapsed < 10000:
        raise ValueError("execution_budget_unproven")
    started, checked, deadline = intent["started_monotonic"], completion["checked_monotonic"], completion["deadline_monotonic"]
    if (any(type(x) not in (int, float) or not math.isfinite(x) for x in (started, checked, deadline))
            or deadline != started + 10 or not started <= checked < deadline
            or elapsed != (checked - started) * 1000):
        raise ValueError("absolute_execution_budget_unproven")
    response = None
    parsed = None
    if execution.get("response_sha256"):
        raw, response = _load(directory / "response.json", 4 * PLAN["max_response_bytes"])
        if digest(raw) != execution["response_sha256"]:
            raise ValueError("response_anchor_invalid")
        if response.get("raw_sha256"):
            raw, envelope = _load(directory / "response-bytes.json", 2 * PLAN["max_response_bytes"])
            if digest(raw) != response["raw_sha256"]:
                raise ValueError("raw_anchor_invalid")
            body = base64.b64decode(envelope["body_b64"], validate=True)
            if envelope["byte_count"] != len(body) or envelope["sha256"] != digest(body):
                raise ValueError("raw_length_or_sha_invalid")
            if response["status"] == "SAMPLE_PARSED":
                if (len(body) > PLAN["max_response_bytes"] or response.get("body_complete") is not True
                        or response.get("http_status") != 200):
                    raise ValueError("response_limit_or_incomplete")
                declared = response.get("declared_length")
                if declared is not None and (not re.fullmatch(r"[0-9]+", str(declared)) or int(declared) != len(body)):
                    raise ValueError("declared_response_length_mismatch")
                stages = [response.get(k) for k in ("started_monotonic", "response_completed_monotonic",
                          "raw_sealed_monotonic", "completed_monotonic")]
                if (any(type(x) not in (int, float) or not math.isfinite(x) for x in stages)
                        or not started <= stages[0] <= stages[1] <= stages[2] <= stages[3] <= checked
                        or stages[2] - stages[0] >= 5):
                    raise ValueError("response_time_evidence_invalid")
                parsed = parse_body(body, response["content_type"])
                if parsed != response["parsed"]:
                    raise ValueError("parse_replay_mismatch")
    passed = (completion["status"] == execution["status"] == "SAMPLE_PARSED"
              and execution.get("cleanup_complete") is True and parsed is not None)
    if passed:
        elapsed = child_returned.get("final_check_elapsed_ms")
        passed = (returned.get("status") == child_returned.get("status") == "SAMPLE_PARSED"
                  and returned.get("serialized_before_deadline") is True
                  and child_returned.get("supervisor_cleanup_complete") is True
                  and returned.get("completion_sha256") == completion_sha
                  and type(elapsed) in (int, float) and math.isfinite(elapsed) and 0 <= elapsed < 10000)
    return safe(evidence_integrity_verified=True, capture_execution_result="PASSED" if passed else "FAILED",
                source_use_basis="UNKNOWN", sample=parsed, observation_input_ready=False,
                current_analysis_usable=False, synthetic_test_only=execution.get("synthetic_test_only"))
