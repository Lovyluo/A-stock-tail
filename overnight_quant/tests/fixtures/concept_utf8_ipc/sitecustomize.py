"""Isolated launcher fixture: keep real workers, replace only the remote response."""
from datetime import datetime
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit
from urllib.request import OpenerDirector

root = Path(os.environ["CONCEPT_UTF8_FIXTURE_ROOT"]).resolve()
if root != Path.cwd().resolve() or (root / ".git").exists():
    os._exit(97)


def audit(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
                 "socket.gethostbyaddr", "socket.getnameinfo"}:
        (root / "network-attempt.txt").write_text(event, encoding="utf-8")
        raise AssertionError("fixture_network_forbidden")


sys.addaudithook(audit)
from overnight_quant.data import concept_batch_observation as concept

fake_sha = "a" * 40
state = root / "overnight_quant/data/cache/concept-batch-observation"
concept.STATE = state


class FixedClock(datetime):
    @classmethod
    def now(cls, tz):
        return cls(2030, 1, 2, 10, 1, tzinfo=concept.CN_TZ).astimezone(tz)


concept.datetime = FixedClock
git_run = subprocess.run


def local_git(args, **kwargs):
    if args == ["git", "rev-parse", "HEAD"]:
        return subprocess.CompletedProcess(args, 0, fake_sha + "\n", "")
    if args == ["git", "status", "--porcelain"]:
        return subprocess.CompletedProcess(args, 0, "", "")
    return git_run(args, **kwargs)


subprocess.run = local_git


def describe(permit):
    quote = permit["approval_evidence"]["verbatim_quote"]
    try:
        body = quote.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        return {"first_surrogate": hex(ord(quote[exc.start]))}
    return {"quote_sha256": hashlib.sha256(body).hexdigest(), "quote_bytes": len(body)}


def trace(role, permit, **extra):
    marker = root / (role + ".json")
    if not marker.exists():
        try:
            proof = describe(permit)
        except (KeyError, TypeError):
            proof = {"quote_missing": True}
        marker.write_text(json.dumps({**proof, **extra, "pid": os.getpid(),
                                     "stdin_encoding": sys.stdin.encoding,
                                     "stdin_errors": sys.stdin.errors}), encoding="utf-8")


capture = concept.capture
coordinate = concept.coordinate
check_auth = concept._check_auth


def trace_capture(**kwargs):
    if kwargs.get("network") is not True or kwargs.get("approved_sha") != fake_sha:
        raise AssertionError("fixture_capture_identity_invalid")
    path = Path(kwargs["authorization"])
    if not path.resolve().is_relative_to(root):
        raise AssertionError("fixture_authorization_path_invalid")
    try:
        permit = json.loads(path.read_bytes().decode("utf-8", "strict"))
    except (UnicodeDecodeError, ValueError):
        permit = None
    trace("capture", permit, file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
          supplied_sha256=kwargs["authorization_sha"])
    return capture(**{**kwargs, "state": state})


def trace_coordinate(task):
    trace("coordinate", task["authorization"])
    return coordinate(task)


def trace_auth(permit, *args, **kwargs):
    caller = sys._getframe(1)
    if caller.f_code.co_name == "page":
        task = caller.f_locals["task"]
        trace(f"page-{task['index']}", permit, wire_quote=describe(task["authorization"]))
    return check_auth(permit, *args, **kwargs)


concept.capture = trace_capture
concept.coordinate = trace_coordinate
concept._check_auth = trace_auth


class Response(BytesIO):
    code = 200
    url = concept.ENDPOINT


def fixture_open(self, request, timeout):
    url = urlsplit(request.full_url)
    if request.full_url.split("?", 1)[0] != concept.ENDPOINT:
        raise AssertionError("fixture_endpoint_invalid")
    fs = parse_qs(url.query)["fs"]
    if fs == [concept.PLAN["list_fs"]]:
        kind, row = "list", {"f12": "BK000001", "f14": "fixture", "f3": 1.0}
    elif fs == [concept.PLAN["members_fs"].format(bk="BK000001")]:
        kind, row = "members", {"f12": "000001", "f13": 0, "f14": "fixture", "f3": 1.0, "f6": 2}
    else:
        raise AssertionError("fixture_scope_invalid")
    with (root / f"transport-{kind}.json").open("x", encoding="utf-8") as marker:
        json.dump({"kind": kind, "fixture_only": True}, marker)
    return Response(json.dumps({"data": {"total": 1, "diff": [row]}}).encode("utf-8"))


OpenerDirector.open = fixture_open
