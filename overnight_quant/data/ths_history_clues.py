"""Read-only attribution browsing, never a current market or security action gate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from overnight_quant.data.ths_single_sample import verify

VERSION = "ths_historical_clues_v1"


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _read(path, anchor, limit):
    if not isinstance(anchor, str) or not re.fullmatch(r"[a-f0-9]{64}", anchor):
        raise ValueError("external_anchor_required")
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit or _sha(raw) != anchor:
        raise ValueError("historical_file_anchor_changed")
    return raw


def split_reason(reason):
    """Only an unambiguous outer ASCII plus is a delimiter; offsets bind raw text."""
    if not isinstance(reason, str) or not reason.strip():
        return {"mode": "NO_READABLE_TEXT", "segments": []}
    pairs = {"(": ")", "（": "）", "[": "]", "【": "】", "“": "”", "「": "」", "『": "』"}
    stack, cuts, ambiguous = [], [-1], False
    for index, char in enumerate(reason):
        if char in pairs:
            stack.append(pairs[char])
        elif char in pairs.values():
            if not stack or stack.pop() != char:
                ambiguous = True
        elif char in "\"'":
            ambiguous = True
        elif char == "+":
            if stack:
                ambiguous = True
            cuts.append(index)
    cuts.append(len(reason))
    pieces = [(a + 1, b) for a, b in zip(cuts, cuts[1:])]
    if stack or ambiguous or any(not reason[a:b].strip() or "\n" in reason[a:b] for a, b in pieces):
        pieces = [(0, len(reason))]
    segments = []
    for start, end in pieces:
        token = reason[start:end]
        left = len(token) - len(token.lstrip())
        right = len(token.rstrip())
        segments.append({"label": token.strip(), "start": start + left, "end": start + right})
    return {"mode": "ASCII_PLUS_DELIMITED" if len(pieces) > 1 else "WHOLE_TEXT", "segments": segments}


def _identity(values, catalog):
    code, name = values.get("code"), values.get("name")
    result = {"status": "UNKNOWN", "canonical_identity": None, "source_market_semantics": "UNKNOWN"}
    entry = (catalog or {}).get(code) if isinstance(code, str) else None
    if not isinstance(entry, dict) or not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
        return result
    if entry.get("code") != code or entry.get("name") != name:
        return {**result, "status": "CATALOG_NAME_OR_CODE_MISMATCH"}
    market = entry.get("market")
    if market not in ("sh", "sz"):
        return result
    from overnight_quant.data.offline_price_observation import normalize_pool
    try:
        identity = normalize_pool([market.upper() + code])[0]
    except ValueError:
        return result
    return {**result, "status": "MATCHED_EXISTING_TRUSTED_CATALOG", "canonical_identity": identity,
            "catalog_updated_at": entry.get("updated_at"), "historical_name_as_of_row_date": "UNKNOWN"}


def explain_sample(sample, request, response, *, trusted_catalog=None):
    records, seen, mentions = [], set(), {}
    for clue in sample["clues"]:
        values = clue["source_values"]
        record_id = _sha(json.dumps(values, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8"))
        if record_id in seen:
            continue
        seen.add(record_id)
        reason = values.get("reason")
        split = split_reason(reason)
        labels = list(dict.fromkeys(item["label"] for item in split["segments"]))
        record = {"record_id": record_id, "source_row": clue["row"], "code_raw": values.get("code"),
                  "name_raw": values.get("name"), "market_raw": values.get("market"),
                  "row_date_raw": values.get("date"), "reason_raw": reason, "tags": labels,
                  "tag_trace": split, "identity_check": _identity(values, trusted_catalog)}
        records.append(record)
        for label in labels:
            mentions.setdefault(label, []).append(record_id)
    return {"version": VERSION, "received_records": sample["received_rows"], "unique_records": len(records),
            "duplicate_records": sample["received_rows"] - len(records), "records": records,
            "tags": [{"label": label, "mention_records": len(ids), "record_ids": ids}
                     for label, ids in sorted(mentions.items())],
            "counting_rule": "EXACT_FULL_SOURCE_RECORD_DEDUP_AND_ONE_MENTION_PER_TAG_PER_RECORD",
            "request_target_date": request["target_date"], "request_started_at": request["started_at"],
            "source_top_level_time_raw": sample["source_time_raw"],
            "response_completed_at": response["response_completed_at"],
            "row_dates_raw": sorted({str(row["row_date_raw"]) for row in records}),
            "coverage": "UNKNOWN", "source_time_semantics": "UNKNOWN", "source_use_basis": "UNKNOWN",
            "market_enum_semantics": "UNKNOWN", "risk_assessment": "NOT_ASSESSED",
            "hotspot_ranking": [], "core_stock_candidates": [], "records_output": [],
            "candidates": [], "tickets": [], "orders": [], "data_ready": False,
            "hard_gate_authorized": False, "qualification": "0/3"}


def load_history(index_path, index_sha, *, trusted_catalog=None):
    raw = _read(index_path, index_sha, 262144)
    index = json.loads(raw.decode("utf-8-sig", "strict"))
    directory = Path(index["output"]).resolve(strict=True)
    files, snapshots = {}, {}
    for entry in index["files"]:
        name = entry["file"]
        if (not isinstance(name, str) or Path(name).name != name or name in files
                or Path(entry["path"]).resolve() != directory / name):
            raise ValueError("historical_file_identity_invalid")
        value = _read(entry["path"], entry["sha256"], 4 * 1048576)
        if type(entry["bytes"]) is not int or len(value) != entry["bytes"]:
            raise ValueError("historical_file_length_invalid")
        files[name], snapshots[name] = entry, value
    if not {"request.json", "response.json", "completion.json", "intent.json"} <= files.keys():
        raise ValueError("historical_files_missing")
    if files["completion.json"]["sha256"] != index["completion_sha256"]:
        raise ValueError("historical_completion_anchor_conflict")
    returned = _read(index["returned_result"], index["returned_sha256"], 262144)
    verified = verify(directory, index["completion_sha256"], index["returned_result"], index["returned_sha256"])
    if (verified["evidence_integrity_verified"] is not True or verified["capture_execution_result"] != "PASSED"
            or verified["execution_time_verified"] is not True or verified["synthetic_test_only"] is not False):
        raise ValueError("real_confirmed_historical_sample_required")
    # Re-read on every interaction; neither page state nor mtime is an integrity cache.
    if _read(index_path, index_sha, 262144) != raw:
        raise ValueError("historical_index_changed")
    for name, entry in files.items():
        if _read(entry["path"], entry["sha256"], 4 * 1048576) != snapshots[name]:
            raise ValueError("historical_file_changed_during_read")
    if _read(index["returned_result"], index["returned_sha256"], 262144) != returned:
        raise ValueError("historical_returned_result_changed")
    intent = json.loads(snapshots["intent.json"].decode("utf-8-sig"))
    result = explain_sample(verified["sample"], json.loads(snapshots["request.json"].decode("utf-8-sig")),
                            json.loads(snapshots["response.json"].decode("utf-8-sig")),
                            trusted_catalog=trusted_catalog)
    return {**result, "batch_id": intent["batch_id"], "evidence_integrity_verified": True,
            "capture_execution_result": "PASSED", "index_sha256": index_sha}
