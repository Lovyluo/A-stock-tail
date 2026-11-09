"""Internal, bounded child for the concept-batch controller."""
import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path
import socket
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from overnight_quant.data.concept_batch_observation import (
    CONTRACT, ENDPOINT, PLAN, _check_auth, capture, coordinate, now, parameters, sha, write,
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


MAX_RESPONSE_BYTES = PLAN["max_response_bytes"]


def configured_opener():
    # Default ProxyHandler respects configured environment/system proxies and bypass rules.
    return build_opener(NoRedirect())


def page(task):
    directory = Path(task["output"])
    intent = json.loads((directory / "intent.json").read_text(encoding="utf-8"))
    if intent["contract_hash"] != CONTRACT or intent["batch_id"] != task["batch_id"]:
        raise ValueError("intent_identity_invalid")
    if (task["test_only"] is not intent["test_only"] or task["fixture"] != intent["fixture"]
            or task["params"] != parameters(task["kind"], task["page"], task["bk"])
            or type(task["index"]) is not int or not 0 <= task["index"] < 6):
        raise ValueError("page_task_identity_invalid")
    if time.perf_counter() >= task["request_deadline"]:
        raise TimeoutError("page_deadline_before_dispatch")
    auth = permission = None
    if not task["test_only"]:
        if task.get("fixture") or not intent.get("authorization"):
            raise ValueError("live_authorization_required")
        authorization_path = task.get("authorization_path")
        if not authorization_path or sha(authorization_path) != intent.get("authorization_sha"):
            raise ValueError("authorization_anchor_mismatch")
        auth = json.loads(Path(authorization_path).read_text(encoding="utf-8"))
        if auth != intent["authorization"]:
            raise ValueError("authorization_content_mismatch")
        permission = _check_auth(auth, task["day"], task["approved_sha"], directory.resolve(), task["batch_id"])
    index = task["index"]
    write(directory / f"request-{index}.json", {"batch_id": task["batch_id"],
          "index": index, "kind": task["kind"], "page": task["page"],
          "params": task["params"], "created_at": now(), "synthetic_test_only": task["test_only"],
          "experiment_permission": permission})
    started, started_monotonic = now(), time.perf_counter()
    if task["test_only"]:
        def denied(*args, **kwargs):
            raise RuntimeError("fixture_network_forbidden")
        socket.create_connection = socket.getaddrinfo = socket.socket = denied
        fixture = json.loads(Path(task["fixture"]).read_text(encoding="utf-8"))
        item = fixture["responses"][index]
        if item.get("sleep"):
            time.sleep(item["sleep"])
        if item.get("network_error"):
            raise OSError("fixture_no_response")
        body = base64.b64decode(item["body_base64"], validate=True)[:MAX_RESPONSE_BYTES + 1]
        status, final_url = item.get("status", 200), ENDPOINT
    else:
        opener = configured_opener()
        request = Request(ENDPOINT + "?" + urlencode(task["params"]))
        started, started_monotonic = now(), time.perf_counter()
        _check_auth(auth, task["day"], task["approved_sha"], directory.resolve(), task["batch_id"],
                    stamp=datetime.fromisoformat(started))
        if time.perf_counter() >= task["request_deadline"]:
            raise TimeoutError("page_deadline_before_dispatch")
        try:
            response = opener.open(request, timeout=max(.001, task["request_deadline"] - time.perf_counter()))
        except HTTPError as exc:
            response = exc
        with response:
            body, status, final_url = response.read(MAX_RESPONSE_BYTES + 1), response.code, response.url
    received, received_monotonic = now(), time.perf_counter()
    raw = {"batch_id": task["batch_id"], "kind": task["kind"], "page": task["page"],
           "index": index, "url": ENDPOINT, "response_url": final_url, "params": task["params"],
           "status": status, "request_started_at": started, "response_completed_at": received,
           "dispatch_monotonic": task["request_started"], "request_started_monotonic": started_monotonic,
           "response_completed_monotonic": received_monotonic, "raw_write_started_monotonic": time.perf_counter(),
           "raw_sha256": hashlib.sha256(body).hexdigest(), "body_base64": base64.b64encode(body).decode(),
           "byte_count": len(body), "body_complete": len(body) <= MAX_RESPONSE_BYTES,
           "synthetic_test_only": task["test_only"], "experiment_permission": permission}
    write(directory / f"raw-{index}.json", raw)
    if not raw["body_complete"]:
        raise ValueError("response_size_limit_exceeded_partial_raw")
    if time.perf_counter() >= task["request_deadline"]:
        raise TimeoutError("page_seal_deadline_exceeded")
    return {"ok": True, "payload": {"raw_sha256": raw["raw_sha256"]}}


def main():
    try:
        task = json.loads(sys.stdin.read())
        if task.get("contract_hash") != CONTRACT:
            raise ValueError("worker_contract_mismatch")
        if task.get("mode") == "capture":
            captured = capture(**task["capture_args"])
            result = {"ok": True, "payload": captured}
        elif task.get("mode") == "coordinate":
            result = coordinate(task)
        elif task.get("mode") == "page":
            result = page(task)
        else:
            raise ValueError("worker_mode_invalid")
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
