"""Immutable seal material and a separately anchored post-write completion witness."""
from datetime import datetime, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import time

from . import minute_capture_audit as legacy
from . import minute_local_prepare_contract as contract

EVIDENCE_NAME = "evidence-v3.json"
RECEIPT_NAME = "seal-receipt-v1.json"
COMPLETION_NAME = "seal-completion-v1.json"
FAILED = "MINUTE_CAPTURE_AUDIT_FAILED"


class SealWriteError(RuntimeError):
    def __init__(self, stage, path, error):
        self.evidence_path = path
        super().__init__(f"seal_write_unconfirmed:{stage}:{type(error).__name__}:{error}")


def binding(evidence, file_sha):
    return {"batch_id": evidence["batch_id"], "trade_date": evidence["trade_date"],
        "code_sha": evidence["code_sha"], "plan_hash": evidence["plan"]["plan_hash"],
        "evidence_version": evidence["schema"], "verifier": evidence["verifier"],
        "evidence_file": EVIDENCE_NAME, "evidence_file_sha256": file_sha,
        "evidence_hash": evidence["evidence_hash"], "test_only": evidence["test_only"]}


def signed(value, name):
    return {**value, name: legacy.digest(value)}


def seal(directory, evidence, *, clock, write):
    """Measure through receipt close/readback, not a pre-write success timestamp.

    The completion witness is an after-the-boundary verification artifact. It
    attests the receipt's observed completion, not its own publication time, and
    is not a source available_at. Failure to publish/read it never returns GO.
    """
    directory = Path(directory)
    path = directory / EVIDENCE_NAME
    started_at, started = clock(), time.perf_counter()
    stage = "evidence"
    try:
        write(path, evidence)
        raw_sha = sha256(path.read_bytes()).hexdigest()
        identity = binding(evidence, raw_sha)
        stage = "file_anchor"
        write(directory / "file-anchor-v3.json", {"file": path.name, "sha256": raw_sha})
        stage = "receipt"
        receipt = signed(legacy.safe(schema=contract.SEAL_RECEIPT_VERSION, **identity,
            seal_started_at=started_at.isoformat(), seal_started_monotonic=started,
            seal_budget_ms=evidence["plan"]["seal_budget_ms"],
            collection_deadline=f"{evidence['trade_date']}T{evidence['plan']['collection_deadline']}+08:00",
            source_available_at="UNPROVEN"), "receipt_hash")
        receipt_path = write(directory / RECEIPT_NAME, receipt)
        receipt_raw = receipt_path.read_bytes()
        if json.loads(receipt_raw) != receipt or sha256(path.read_bytes()).hexdigest() != raw_sha:
            raise ValueError("seal_material_readback_mismatch")
        # Both clocks are sampled only AFTER the receipt's atomic writer closed
        # and the published receipt/evidence were read back successfully.
        completed_at, completed = clock(), time.perf_counter()
        elapsed = (completed - started) * 1000
        late = completed_at > datetime.fromisoformat(receipt["collection_deadline"])
        over = elapsed > receipt["seal_budget_ms"]
        completion = signed(legacy.safe(schema=contract.SEAL_COMPLETION_VERSION, **identity,
            receipt_file=RECEIPT_NAME, receipt_file_sha256=sha256(receipt_raw).hexdigest(),
            receipt_persisted_and_readback_confirmed=True,
            seal_completed_at=completed_at.isoformat(), seal_completed_monotonic=completed,
            seal_elapsed_ms=elapsed, late_seal=late, seal_budget_exceeded=over,
            seal_status="SEAL_FAILED" if late or over else "SEAL_COMPLETED_WITHIN_BUDGET",
            source_available_at="UNPROVEN"), "completion_hash")
        stage = "completion_witness"
        witness_path = write(directory / COMPLETION_NAME, completion)
        if json.loads(witness_path.read_bytes()) != completion:
            raise ValueError("seal_completion_readback_mismatch")
    except Exception as exc:
        raise SealWriteError(stage, path, exc) from exc
    return path


def _anchor(expected, actual):
    return (type(expected) is str and len(expected) == 64
            and all(c in "0123456789abcdef" for c in expected) and expected == actual)


def verify(evidence, file_sha, receipt, receipt_expected, receipt_actual,
           completion, completion_expected, completion_actual):
    errors, seal_status = [], "SEAL_UNCONFIRMED"
    for name, value, expected, actual in (("receipt", receipt, receipt_expected, receipt_actual),
                                         ("completion", completion, completion_expected, completion_actual)):
        if not isinstance(value, dict) or not _anchor(expected, actual):
            errors.append(f"seal_{name}_missing_or_external_sha256_mismatch")
    if errors:
        return errors, seal_status
    try:
        identity = binding(evidence, file_sha)
        for value, version, hash_name in ((receipt, contract.SEAL_RECEIPT_VERSION, "receipt_hash"),
                                          (completion, contract.SEAL_COMPLETION_VERSION, "completion_hash")):
            if value.get("schema") != version or value.get(hash_name) != legacy.digest(
                    {k: v for k, v in value.items() if k != hash_name}):
                errors.append("seal_version_or_internal_hash_invalid")
            if any(value.get(k) != v for k, v in identity.items()):
                errors.append("seal_evidence_batch_identity_mismatch")
            if any(value.get(k) != v for k, v in legacy.safe().items()):
                errors.append("seal_unsafe_or_promoted_output")
            if value.get("source_available_at") != "UNPROVEN":
                errors.append("seal_not_source_availability")
        if (completion.get("receipt_file") != RECEIPT_NAME
                or completion.get("receipt_file_sha256") != receipt_actual
                or completion.get("receipt_persisted_and_readback_confirmed") is not True):
            errors.append("seal_receipt_completion_not_bound_or_confirmed")
        start, end, elapsed = (receipt["seal_started_monotonic"], completion["seal_completed_monotonic"],
                               completion["seal_elapsed_ms"])
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end, elapsed)):
            raise ValueError("seal_nonfinite_timing")
        started_at = datetime.fromisoformat(receipt["seal_started_at"])
        completed_at = datetime.fromisoformat(completion["seal_completed_at"])
        deadline = f"{evidence['trade_date']}T{evidence['plan']['collection_deadline']}+08:00"
        if (receipt["collection_deadline"] != deadline
                or receipt["seal_budget_ms"] != evidence["plan"]["seal_budget_ms"]
                or start > end or abs(elapsed - (end - start) * 1000) > 1e-6
                or started_at.utcoffset() != timedelta(hours=8) or completed_at.utcoffset() != timedelta(hours=8)
                or started_at.date().isoformat() != evidence["trade_date"] or completed_at < started_at):
            errors.append("seal_deadline_or_accounting_invalid")
        late = completed_at > datetime.fromisoformat(deadline)
        over = elapsed > evidence["plan"]["seal_budget_ms"]
        expected_status = "SEAL_FAILED" if late or over else "SEAL_COMPLETED_WITHIN_BUDGET"
        if (type(completion.get("late_seal")) is not bool or completion["late_seal"] != late
                or type(completion.get("seal_budget_exceeded")) is not bool
                or completion["seal_budget_exceeded"] != over or completion.get("seal_status") != expected_status):
            errors.append("seal_recomputed_status_mismatch")
        if not errors:
            seal_status = expected_status
    except (KeyError, TypeError, ValueError, OverflowError):
        errors.append("seal_receipt_or_completion_structure_invalid")
    return list(dict.fromkeys(errors)), seal_status
