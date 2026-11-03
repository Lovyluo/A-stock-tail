"""One isolated quote request. This is not a public or scheduled collection entry."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request


def execute(task, *, network_attempts=None):
    from overnight_quant.data.quote_observation_refresh import (
        CODES, HTTP_SECONDS, NETWORK_SWITCH, VERSION, decode_response, utc_now,
    )
    from overnight_quant.data.offline_price_observation import _json, write_once
    from overnight_quant.data.tencent_direct_http_providers import (
        TENCENT_SOURCE_VERSION, TencentDirectHttpProviders, TencentHttpResponse, build_tencent_request_url,
    )
    if task.get("contract") != VERSION or task.get("codes") != list(CODES):
        raise ValueError("worker_contract_invalid")
    test_only = task.get("synthetic_test_only")
    if type(test_only) is not bool or (not test_only and os.environ.get(NETWORK_SWITCH) != "1"):
        raise ValueError("worker_network_not_enabled")
    if test_only != bool(task.get("fixture")):
        raise ValueError("worker_scope_invalid")
    attempts = network_attempts if network_attempts is not None else []
    target = Path(task["output"])
    fixture = _json(Path(task["fixture"]).read_bytes()) if test_only else None
    envelope = None

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise ValueError("redirect_forbidden")

    class Transport:
        count = 0

        def request(self, method, url, *, headers, timeout_seconds):
            nonlocal envelope
            self.count += 1
            if self.count != 1 or url != build_tencent_request_url(CODES) or method != "GET":
                raise ValueError("single_quote_request_only")
            if fixture:
                time.sleep(fixture.get("delay_seconds", 0))
                if fixture.get("error"):
                    raise OSError(fixture["error"])
                raw = base64.b64decode(fixture["raw_base64"], validate=True)
                status, response_url = fixture.get("http_status", 200), fixture.get("response_url", url)
            else:
                request = urllib.request.Request(url, headers=dict(headers), method="GET")
                try:
                    response = urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout_seconds)
                except urllib.error.HTTPError as exc:
                    response = exc  # Retain the body, then let the existing Provider reject its status.
                with response:
                    raw = response.read(262145)
                    status, response_url = response.status, response.geturl()
            completed = utc_now().isoformat()
            envelope = dict(contract=VERSION, batch_id=task["batch_id"], codes=list(CODES),
                source_version=TENCENT_SOURCE_VERSION,
                response_complete=len(raw) <= 262144,
                synthetic_test_only=test_only, request_started_at=request_started,
                response_completed_at=completed, completed_at=completed,
                request_url=url, response_url=response_url, http_status=status, request_count=self.count,
                raw_base64=base64.b64encode(raw).decode("ascii"), raw_sha256=hashlib.sha256(raw).hexdigest())
            # Retain even an invalid response before Provider/Adapter validation.
            write_once(target / "response-received.json", envelope)
            if len(raw) > 262144:
                raise ValueError("response_size_exceeded_partial_material")
            return TencentHttpResponse(raw, status, response_url)

    transport = Transport()
    request_started = utc_now().isoformat()
    provider = TencentDirectHttpProviders(CODES, transport=transport, clock=utc_now, timeout_seconds=HTTP_SECONDS)
    provider.collect_quote_records()
    envelope["completed_at"] = utc_now().isoformat()
    decode_response(envelope)
    if attempts:
        raise ValueError("test_only_network_attempt")
    write_once(target / "response.json", envelope)
    return {"request_count": transport.count, "network_attempts": len(attempts)}


def main():
    try:
        task = json.loads(sys.stdin.read())
        attempts = []
        if task.get("synthetic_test_only") is True:
            def deny(event, args):
                if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname"}:
                    attempts.append(event)
                    raise RuntimeError("test_only_network_forbidden")
            sys.addaudithook(deny)
        result = execute(task, network_attempts=attempts)
        print(json.dumps({"ok": True, "payload": result}))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error_code": "QUOTE_REFRESH_FAILED",
                          "error": f"{type(exc).__name__}:{exc}"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
