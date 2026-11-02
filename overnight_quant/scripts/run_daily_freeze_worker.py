"""Internal worker; only a versioned controller may dispatch its fixed task."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import socket
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request

from overnight_quant.data.daily_freeze import (
    IDENTITY, PLAN, TENCENT_KLINE_URL, check_inputs, coordinate, now, sha, sha_bytes, write,
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(task):
    if task["contract_hash"] != IDENTITY or time.perf_counter() >= task["request_deadline"]:
        raise ValueError("worker_contract_or_deadline_invalid")
    index, directory = task["index"], Path(task["output"])
    intent = json.loads((directory / "intent.json").read_text(encoding="utf-8"))
    if intent["batch_id"] != task["batch_id"] or intent["contract_hash"] != IDENTITY:
        raise ValueError("worker_intent_mismatch")
    check_inputs(task["day"], task["codes"], task["approved_sha"], task["output"],
                 intent["authorization"], test_only=task["test_only"], reserved=True)
    write(directory / f"request-{index}.json", {"batch_id": task["batch_id"], "index": index,
                                               "created_at": now(), 'synthetic_test_only': task['test_only']})
    started_at, started = now(), time.perf_counter()
    if task["test_only"]:
        def denied(*args, **kwargs):
            raise RuntimeError("test_only_external_network_attempt")
        socket.create_connection = socket.getaddrinfo = denied
        fixture = json.loads(Path(task["fixture"]).read_text())
        item = fixture["responses"][index]
        if item.get("sleep"):
            time.sleep(item["sleep"])
        if item.get("network_error"):
            raise OSError("fixture_no_response")
        body = base64.b64decode(item["content_base64"])
        status, headers = item.get("status", 200), {}
        final_url = TENCENT_KLINE_URL
    else:
        if task.get("fixture"):
            raise ValueError("fixture_on_real_path")
        opener = build_opener(ProxyHandler({}), NoRedirect())
        req = Request(TENCENT_KLINE_URL + "?" + urlencode({"param": PLAN[index]}), headers=task["headers"])
        try:
            response = opener.open(req, timeout=max(.001, task["request_deadline"] - time.perf_counter()))
        except HTTPError as exc:
            response = exc
        with response:
            body, status, headers, final_url = response.read(), response.code, dict(response.headers), response.url
    completed_at, completed = now(), time.perf_counter()
    raw = {"batch_id": task["batch_id"], "run_date": task["day"], "index": index, 'synthetic_test_only': task['test_only'],
           "method": "GET", "url": TENCENT_KLINE_URL, "params": [["param", PLAN[index]]], "data": [],
           "source_version": "ifzq_fqkline_day_v2026-07-30" if index == 0 else "ifzq_fqkline_qfqday_v2026-07-30",
           "content_base64": base64.b64encode(body).decode(), "byte_count": len(body), "raw_hash": sha_bytes(body),
           "http_status_code": status, "response_headers": headers, "response_url": final_url,
           "request_started_at": started_at, "response_completed_at": completed_at,
           "dispatch_monotonic": task["request_started_monotonic"], "http_started_monotonic": started,
           "response_completed_monotonic": completed, "raw_write_started_monotonic": time.perf_counter()}
    # The post-write timestamp is in a separate immutable completion receipt.
    path = directory / f"raw-{index}.json"
    if task["test_only"] and item.get("seal_failure"):
        raise OSError("fixture_raw_seal_failure")
    write(path, raw)
    written_at, written = now(), time.perf_counter()
    write(directory / f"raw-written-{index}.json", {"raw_file_sha256": sha(path),
          "written_at": written_at, "written_monotonic": written, "batch_id": task["batch_id"]})
    if time.perf_counter() >= task["request_deadline"]:
        raise TimeoutError("raw_seal_deadline_exceeded")
    return {"ok": True, "payload": {"raw_file_sha256": sha(path)}}


def main():
    try:
        task = json.loads(sys.stdin.read())
        if task.get("contract_hash") != IDENTITY:
            raise ValueError("internal_task_identity_invalid")
        result = coordinate(task) if task["mode"] == "coordinate" else request(task)
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}
    print(json.dumps(result))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
