"""Local approval and durable reservations for limited manual quote observation."""
from __future__ import annotations

import argparse
import base64
from datetime import timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess

from overnight_quant.data.offline_price_observation import _json, _time, write_once

LIMITED_VERSION = "manual_quote_limited_live_v3"
LIMITED_SWITCH = "OBSERVATION_LIMITED_QUOTE_ENABLED"
PERMIT_FILE = "OBSERVATION_LIMITED_QUOTE_PERMIT_FILE"
PERMIT_SHA = "OBSERVATION_LIMITED_QUOTE_PERMIT_SHA256"
PERMIT_SCHEMA = "manual_quote_limited_permit_v1"
LOCAL_PERMIT_SCHEMA = "manual_quote_limited_permit_v2"
LOCAL_REFERENCE_KIND = "CODEX_LOCAL_MESSAGE_V1"
CN = timezone(timedelta(hours=8))
MAX_PERMIT_BYTES = 16384
SHA256 = r"[a-f0-9]{64}"
ID = r"[a-z0-9][a-z0-9_-]{7,79}"
CODEX_ID = r"[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}"
CODE_ROOT = Path(__file__).resolve().parents[2]


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _git_output(*args):
    return subprocess.check_output(
        ["git", "-C", str(CODE_ROOT), *args],
        timeout=1, text=True, encoding="utf-8").strip()


def _startup_head():
    if os.environ.get(LIMITED_SWITCH) == "1":
        try:
            return _git_output("rev-parse", "HEAD")
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return None


STARTUP_HEAD = _startup_head()


def _code_matches(approved_sha):
    try:
        if STARTUP_HEAD == approved_sha == _git_output("rev-parse", "HEAD"):
            if not _git_output("status", "--porcelain", "--untracked-files=all", "--", "overnight_quant"):
                return
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    raise ValueError("limited_permit_code_mismatch")


def _check_window(permit, stamp):
    start, end = (_time(permit[key]) for key in ("window_start", "window_end"))
    if (start.utcoffset() != CN.utcoffset(None) or end.utcoffset() != CN.utcoffset(None)
            or not start < end or start.date() != end.date()
            or permit.get("date") != start.date().isoformat()):
        raise ValueError("limited_permit_window_invalid")
    if stamp is not None and not start <= stamp < end:
        raise ValueError("limited_permit_outside_window")
    if (stamp is not None and permit["schema"] == LOCAL_PERMIT_SCHEMA
            and _time(permit["approval_reference"]["operator_check"]["checked_at"]) > stamp):
        raise ValueError("limited_permit_approval_check_after_evaluation")


def _check_approval_reference(schema, ref, approved_sha):
    shared = {"verbatim_quote", "quote_sha256"}
    if not isinstance(ref, dict) or not isinstance(ref.get("verbatim_quote"), str) or not ref["verbatim_quote"].strip():
        raise ValueError("limited_permit_approval_reference_invalid")
    try:
        quote = ref["verbatim_quote"].encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError("limited_permit_approval_reference_invalid_utf8") from None
    if _sha(quote) != ref.get("quote_sha256"):
        raise ValueError("limited_permit_approval_reference_invalid")
    if schema == PERMIT_SCHEMA:
        if (set(ref) != shared | {"locator"} or not isinstance(ref["locator"], str)
                or not re.fullmatch(r"https://[^\s]{8,500}", ref["locator"])):
            raise ValueError("limited_permit_approval_reference_invalid")
        return
    if (set(ref) != shared | {"kind", "thread_id", "message_id", "local_original_path",
                              "local_original_sha256", "operator_check"}
            or ref["kind"] != LOCAL_REFERENCE_KIND
            or any(not isinstance(ref[key], str) or not re.fullmatch(CODEX_ID, ref[key])
                   for key in ("thread_id", "message_id"))
            or ref["thread_id"] == ref["message_id"]):
        raise ValueError("limited_permit_local_message_reference_invalid")
    if not re.search(r"(?<![0-9a-fA-F])" + approved_sha + r"(?![0-9a-fA-F])", ref["verbatim_quote"]):
        raise ValueError("limited_permit_local_approval_code_unbound")
    check = ref["operator_check"]
    if (not isinstance(check, dict) or set(check) != {"status", "operator", "checked_at"}
            or check["status"] != "MATCHED_CURRENT_USER_MESSAGE"
            or not isinstance(check["operator"], str) or not 1 <= len(check["operator"].strip()) <= 128
            or _time(check["checked_at"]).utcoffset() != CN.utcoffset(None)):
        raise ValueError("limited_permit_local_operator_check_invalid")
    # A local attestation binds bytes; it cannot authenticate a user or look up a message.
    try:
        json.dumps(ref, ensure_ascii=False).encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError("limited_permit_approval_reference_invalid_utf8") from None
    if not isinstance(ref["local_original_path"], str):
        raise ValueError("limited_permit_local_original_path_invalid")
    original = Path(ref["local_original_path"])
    if not original.is_absolute() or original.as_posix().startswith("//"):
        raise ValueError("limited_permit_local_original_path_invalid")
    if os.name == "nt":
        from ctypes import windll

        if windll.kernel32.GetDriveTypeW(original.anchor) != 3:  # DRIVE_FIXED
            raise ValueError("limited_permit_local_original_path_invalid")
    original = original.resolve(strict=True)
    if (original.as_posix().startswith("//") or not original.is_file()
            or original.is_relative_to(CODE_ROOT) or any((parent / ".git").exists() for parent in original.parents)):
        raise ValueError("limited_permit_local_original_path_invalid")
    with original.open("rb") as source:
        raw = source.read(MAX_PERMIT_BYTES + 1)
    if raw != quote or _sha(raw) != ref["local_original_sha256"]:
        raise ValueError("limited_permit_local_original_mismatch")


def _parse(raw, digest, test_only):
    from overnight_quant.data.quote_observation_refresh import normalize_selection

    if not isinstance(digest, str) or not re.fullmatch(SHA256, digest) or len(raw) > MAX_PERMIT_BYTES or _sha(raw) != digest:
        raise ValueError("limited_permit_anchor_invalid")
    permit = _json(raw.decode("utf-8", errors="strict"))
    expected = {
        "contract": LIMITED_VERSION,
        "status": "TEST_ONLY" if test_only else "USER_APPROVED",
        "purpose": "PERSONAL_INTERNAL_OBSERVATION",
        "source_use_basis": "UNKNOWN",
        "known_use_constraint": "NO_KNOWN_PROHIBITION",
    }
    if (not isinstance(permit, dict) or permit.get("schema") not in (PERMIT_SCHEMA, LOCAL_PERMIT_SCHEMA)
            or any(permit.get(k) != v for k, v in expected.items())
            or not isinstance(permit.get("permit_id"), str) or not re.fullmatch(ID, permit["permit_id"])
            or not isinstance(permit.get("approved_sha"), str) or not re.fullmatch(r"[a-f0-9]{40}", permit["approved_sha"])
            or not isinstance(permit.get("worktree"), str) or not Path(permit["worktree"]).is_absolute()
            or permit.get("user_approved") is not True or permit.get("acknowledges_unknown_basis") is not True
            or permit.get("public_distribution") is not False or permit.get("access_control_bypass") is not False
            or type(permit.get("max_requests")) is not int or permit["max_requests"] != 2
            or permit.get("manual_clicks_only") is not True
            or normalize_selection(permit.get("selection")) != permit["selection"]):
        raise ValueError("limited_permit_invalid")
    _check_approval_reference(permit["schema"], permit.get("approval_reference"), permit["approved_sha"])
    _check_window(permit, None)
    return permit


def read_permit(ref, digest, *, test_only=False):
    if not ref or not digest:
        raise ValueError("limited_permit_required")
    with Path(ref).open("rb") as source:
        raw = source.read(MAX_PERMIT_BYTES + 1)
    permit = _parse(raw, digest, test_only)
    return permit, {"sha256": digest, "base64": base64.b64encode(raw).decode("ascii")}


def configured_permit(*, stamp=None):
    if os.environ.get(LIMITED_SWITCH) != "1":
        raise ValueError("limited_network_disabled")
    permit, archive = read_permit(os.environ.get(PERMIT_FILE), os.environ.get(PERMIT_SHA))
    if Path(permit["worktree"]).resolve() != CODE_ROOT:
        raise ValueError("limited_permit_worktree_mismatch")
    _check_window(permit, stamp)
    _code_matches(permit["approved_sha"])
    return permit, archive


def _control(root):
    from overnight_quant.data.quote_observation_refresh import VERSION

    return Path(root) / VERSION / "control"


def _read(path):
    return _json(path.read_bytes())


def _permit_file(state, permit_id):
    return state / f"limited-permit-{permit_id}.json"


def _slot(state, permit_id, number):
    return state / f"limited-slot-{permit_id}-{number}.json"


def _closed(state, permit_id):
    return state / f"limited-closed-{permit_id}.json"


def _permit_status(root, permit, archive):
    from overnight_quant.data.point_in_time import stable_hash
    from overnight_quant.data.quote_observation_refresh import committed_refresh_outcome

    state = _control(root)
    permit_id = permit["permit_id"]
    saved = _permit_file(state, permit_id)
    if saved.exists() and _read(saved) != archive:
        raise ValueError("limited_permit_id_reused")
    paths = [_slot(state, permit_id, n) for n in (1, 2)]
    used = 0
    reason = None
    for number, path in enumerate(paths, 1):
        if not path.exists():
            continue
        used += 1
        intent = _read(path)
        batch = intent.get("batch_id")
        if (not isinstance(batch, str) or not re.fullmatch(r"[a-f0-9]{32}", batch)
                or intent.get("permit_slot") != number or intent.get("permit_sha256") != archive["sha256"]
                or not saved.exists() or used != number):
            raise ValueError("limited_reservation_invalid")
        try:
            response = _read(Path(root) / LIMITED_VERSION / "attempts" / batch / "response.json")
            outcome = committed_refresh_outcome(root, batch, stable_hash(response), contract=LIMITED_VERSION)
            if outcome["status"] == "SUCCEEDED":
                continue
        except (OSError, ValueError, KeyError, TypeError):
            pass
        reason = "limited_permit_frozen_after_failure"
    if _closed(state, permit_id).exists():
        reason = "limited_permit_closed"
    elif not reason and used == 2:
        reason = "limited_permit_quota_exhausted"
    return 2 - used, reason


def reserve(root, permit, archive, intent):
    """Called only under the shared in-flight lock, before any worker can start."""
    state = _control(root)
    permit_id = permit["permit_id"]
    saved = _permit_file(state, permit_id)
    if not saved.exists():
        write_once(saved, archive)
    write_once(_slot(state, permit_id, intent["permit_slot"]), intent)


def verify_reservation(root, intent):
    state = _control(root)
    permit_id, slot = intent.get("permit_id"), intent.get("permit_slot")
    if (not isinstance(permit_id, str) or not re.fullmatch(ID, permit_id)
            or type(slot) is not int or slot not in (1, 2)
            or type(intent.get("synthetic_test_only")) is not bool):
        raise ValueError("limited_reservation_invalid")
    archive = _read(_permit_file(state, permit_id))
    raw = base64.b64decode(archive["base64"], validate=True)
    permit = _parse(raw, archive["sha256"], intent["synthetic_test_only"])
    if (_read(_slot(state, permit_id, slot)) != intent or intent.get("contract") != LIMITED_VERSION
            or permit_id != permit["permit_id"] or intent.get("approved_sha") != permit["approved_sha"]
            or intent.get("permit_sha256") != archive["sha256"] or intent.get("selection") != permit["selection"]):
        raise ValueError("limited_reservation_binding_invalid")
    _check_window(permit, _time(intent["created_at"]))
    if slot == 2:
        from overnight_quant.data.point_in_time import stable_hash
        from overnight_quant.data.quote_observation_refresh import INTERVAL_SECONDS, committed_refresh_outcome

        previous = _read(_slot(state, permit_id, 1))
        batch = previous.get("batch_id")
        if (not isinstance(batch, str) or not re.fullmatch(r"[a-f0-9]{32}", batch)
                or batch == intent["batch_id"] or previous.get("permit_id") != permit_id
                or previous.get("permit_sha256") != intent["permit_sha256"] or previous.get("permit_slot") != 1):
            raise ValueError("limited_previous_reservation_invalid")
        directory = Path(root) / LIMITED_VERSION / "attempts" / batch
        if _read(directory / "intent.json") != previous:
            raise ValueError("limited_previous_reservation_invalid")
        release = _read(state / f"release-{batch}.json")["dispatch_monotonic"]
        response = _read(directory / "response.json")
        current = intent["dispatch_monotonic"]
        if (any(type(tick) not in (int, float) or not math.isfinite(tick) for tick in (release, current))
                or not 0 <= release <= current - INTERVAL_SECONDS
                or _time(intent["created_at"]) < _time(response["completed_at"])
                or committed_refresh_outcome(root, batch, stable_hash(response), contract=LIMITED_VERSION)["status"]
                != "SUCCEEDED"):
            raise ValueError("limited_previous_attempt_not_confirmed")
    return permit


def check_dispatch(root, intent, stamp, *, check_code=False):
    state = _control(root)
    batch = intent["batch_id"]
    if not isinstance(batch, str) or not re.fullmatch(r"[a-f0-9]{32}", batch):
        raise ValueError("limited_batch_invalid")
    permit = verify_reservation(root, intent)
    if (_closed(state, permit["permit_id"]).exists() or (state / "cleanup-incomplete.json").exists()
            or _read(state / "active.lock").get("batch_id") != batch
            or _read(state / f"attempt-{batch}.json") != intent):
        raise ValueError("limited_dispatch_blocked")
    if not intent["synthetic_test_only"]:
        if (os.environ.get(LIMITED_SWITCH) != "1" or os.environ.get(PERMIT_SHA) != intent["permit_sha256"]):
            raise ValueError("limited_network_disabled_or_permit_changed")
        if check_code:
            live, _ = configured_permit(stamp=stamp)
            if live != permit:
                raise ValueError("limited_live_permit_changed")
    _check_window(permit, stamp)


def close_permit(root, permit, archive):
    state = _control(root)
    state.mkdir(parents=True, exist_ok=True)
    path = _closed(state, permit["permit_id"])
    if not path.exists():
        write_once(path, {"permit_id": permit["permit_id"], "permit_sha256": archive["sha256"]})


def verify_batch(root, batch, permit_sha256, confirmation_sha256, *, test_only=False):
    from overnight_quant.data.point_in_time import stable_hash
    from overnight_quant.data.quote_observation_refresh import committed_refresh_outcome, decode_response

    if not isinstance(batch, str) or not re.fullmatch(r"[a-f0-9]{32}", batch):
        raise ValueError("limited_batch_invalid")
    directory = Path(root) / LIMITED_VERSION / "attempts" / batch
    intent = _read(directory / "intent.json")
    if intent.get("synthetic_test_only") is not test_only or intent.get("permit_sha256") != permit_sha256:
        raise ValueError("limited_replay_permit_anchor_mismatch")
    verify_reservation(root, intent)
    response = _read(directory / "response.json")
    snapshot = decode_response(response)
    confirmation = (directory / "final-confirmation.json").read_bytes()
    if _sha(confirmation) != confirmation_sha256:
        raise ValueError("limited_replay_confirmation_anchor_mismatch")
    outcome = committed_refresh_outcome(root, batch, stable_hash(response), contract=LIMITED_VERSION)
    if outcome["status"] != "SUCCEEDED" or snapshot["batch_id"] != batch:
        raise ValueError("limited_replay_not_successful")
    return {"status": "OBSERVATION_VERIFIED", "batch": batch, "selection": snapshot["selection"],
            "synthetic_test_only": test_only}


def main(argv=None):
    from overnight_quant.data.offline_price_observation import DEFAULT_STORE, ObservationStore

    parser = argparse.ArgumentParser(description="Offline verification or permanent closure of a limited quote permit")
    parser.add_argument("operation", choices=("verify", "close"))
    parser.add_argument("--permit-sha256", required=True)
    parser.add_argument("--permit-file")
    parser.add_argument("--batch")
    parser.add_argument("--confirmation-sha256")
    args = parser.parse_args(argv)
    root = ObservationStore(DEFAULT_STORE, "historical").root
    try:
        if args.operation == "close":
            permit, archive = read_permit(args.permit_file, args.permit_sha256)
            if Path(permit["worktree"]).resolve() != CODE_ROOT:
                raise ValueError("limited_permit_worktree_mismatch")
            close_permit(root, permit, archive)
            print("PERMIT_CLOSED")
            return 0
        print(json.dumps(verify_batch(root, args.batch, args.permit_sha256, args.confirmation_sha256)))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "REJECTED", "error": f"{type(exc).__name__}:{exc}"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
