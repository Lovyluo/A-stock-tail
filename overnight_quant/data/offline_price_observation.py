"""Versioned, offline-only price conditions; never emits trading records."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

from overnight_quant.data.market_session_confirmation import (
    verify_market_session_confirmation_evidence,
    write_market_session_confirmation_json_atomic as write_once,
)
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.s2_integration_contract import security_identity

VERSION = "offline_price_observation_v1"
TEST_SCHEMA = "offline_price_snapshot_test_v1"
PRICE_BASIS = "UNADJUSTED_QUOTE_CNY_PER_SHARE"
SCOPES = ("historical", "synthetic_test_only")
INITIAL_POOL = ("000001", "000333", "600000", "600519", "601318")
STATES = {"UNSET": "未设置", "MET": "满足", "NOT_MET": "不满足", "INSUFFICIENT": "数据不足"}


def _identity(value):
    if isinstance(value, str) and re.fullmatch(r"(SH|SZ|BJ):\d{6}", value):
        value = value.replace(":", "")
    return security_identity(value)


def normalize_pool(values):
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("watchlist_required")
    return sorted({_identity(value) for value in values})


def _positive(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("positive_finite_price_required")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("positive_finite_price_required") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("positive_finite_price_required")
    return number


def make_rule(identity, *, lower=None, upper=None):
    values = {"version": VERSION, "security_identity": _identity(identity)}
    for name, value in (("lower", lower), ("upper", upper)):
        values[name] = None if value in (None, "") else str(_positive(value))
    if values["lower"] is not None and values["upper"] is not None:
        if Decimal(values["lower"]) >= Decimal(values["upper"]):
            raise ValueError("lower_must_be_less_than_upper")
    return {**values, "rule_version": stable_hash(values)}


def _check_rule(rule, identity):
    if not isinstance(rule, dict):
        raise ValueError("rule_invalid")
    rebuilt = make_rule(identity, lower=rule.get("lower"), upper=rule.get("upper"))
    if rule != rebuilt:
        raise ValueError("rule_identity_or_version_invalid")


def _time(value):
    if not isinstance(value, str):
        raise ValueError("timestamp_missing")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp_invalid") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp_timezone_required")
    return result


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite_json")))


def import_snapshot(data, *, expected_sha256, test_only=False, now=None):
    """Only existing session evidence or an explicit, segregated test contract."""
    if type(test_only) is not bool or not isinstance(data, bytes) or len(data) > 5_000_000:
        raise ValueError("snapshot_input_invalid")
    digest = hashlib.sha256(data).hexdigest()
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("external_sha256_required")
    if digest != expected_sha256:
        raise ValueError("external_sha256_mismatch")
    payload = _json(data)
    if not isinstance(payload, dict):
        raise ValueError("snapshot_object_required")
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("inspection_timezone_required")
    if test_only:
        if payload.get("schema") != TEST_SCHEMA or payload.get("scope") != "synthetic_test_only":
            raise ValueError("test_only_contract_required")
        batch = payload.get("batch_id")
        captured = payload.get("captured_at")
        source = "synthetic_test_only"
        records = payload.get("rows")
    else:
        if payload.get("evidence_schema_version") != "market_session_confirmation_v1":
            raise ValueError("historical_session_evidence_required_no_qfq")
        result = verify_market_session_confirmation_evidence(
            payload, expected_file_sha256=expected_sha256, actual_file_sha256=digest)
        if not result.get("evidence_integrity_verified") or not result.get("session_confirmed"):
            raise ValueError("historical_session_rejected:" + ",".join(result.get("errors", [])))
        batch = payload.get("market_session_confirmation_evidence_hash")
        captured = payload.get("completed_at")
        source = payload["source_version"]
        records = []
        for record in payload["records"]:
            quote = record.get("payload") or {}
            records.append({
                "code": quote.get("code"), "security_identity": _identity(quote.get("code")),
                "price": quote.get("price"), "price_basis": PRICE_BASIS,
                "unit": (quote.get("field_units") or {}).get("price"),
                "source_version": record.get("source_version"), "batch_id": batch,
                "event_time": record.get("event_time"), "observed_at": record.get("observed_at"),
                "available_at": record.get("available_at"), "raw_hash": record.get("raw_hash"),
            })
    if not isinstance(batch, str) or not batch.strip() or len(batch) > 160:
        raise ValueError("batch_identity_required")
    if _time(captured) > clock:
        raise ValueError("snapshot_in_future")
    if not isinstance(records, list) or not records or len(records) > 5000:
        raise ValueError("snapshot_rows_required")
    rows = {}
    for record in records:
        if not isinstance(record, dict) or record.get("batch_id") != batch:
            raise ValueError("mixed_batch_or_row_invalid")
        identity = _identity(record.get("code"))
        if record.get("security_identity") != identity:
            raise ValueError("security_identity_mismatch")
        if record.get("source_version") != source:
            raise ValueError("source_version_mismatch")
        if identity in rows and rows[identity] != record:
            raise ValueError("conflicting_duplicate_quote")
        rows[identity] = record
    return {"version": VERSION, "scope": SCOPES[int(test_only)], "batch_id": batch,
            "captured_at": captured, "source_version": source, "file_sha256": digest,
            "rows": {key: rows[key] for key in sorted(rows)}, "risk_assessment": "NOT_ASSESSED"}


def _quote(snapshot, identity, now):
    if snapshot.get("version") != VERSION or snapshot.get("scope") not in SCOPES:
        raise ValueError("snapshot_contract_invalid")
    row = snapshot["rows"].get(identity)
    if row is None:
        raise ValueError("quote_missing_in_selected_batch")
    if _identity(row.get("code")) != identity or row.get("security_identity") != identity:
        raise ValueError("security_identity_mismatch")
    if row.get("batch_id") != snapshot["batch_id"]:
        raise ValueError("mixed_batch")
    if row.get("source_version") != snapshot["source_version"]:
        raise ValueError("source_version_mismatch")
    if row.get("price_basis") != PRICE_BASIS or row.get("unit") != "CNY_per_share":
        raise ValueError("actual_unadjusted_quote_required")
    event, observed, available, captured = (
        _time(row.get("event_time")), _time(row.get("observed_at")),
        _time(row.get("available_at")), _time(snapshot.get("captured_at")))
    if not event <= observed <= available <= captured <= now:
        raise ValueError("quote_time_order_or_future_invalid")
    if len({value.astimezone(_time(snapshot["captured_at"]).tzinfo).date()
            for value in (event, observed, available, captured)}) != 1:
        raise ValueError("quote_trade_date_mismatch")
    if not isinstance(row.get("raw_hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", row["raw_hash"]):
        raise ValueError("quote_evidence_reference_missing")
    return row, _positive(row.get("price"))


def evaluate(snapshot, identity, rule, *, now=None, require_realtime=False):
    identity = _identity(identity)
    _check_rule(rule, identity)
    reason, row, price = "", {}, None
    try:
        if require_realtime is not False:
            raise ValueError("realtime_not_connected")
        if snapshot is None:
            raise ValueError("historical_snapshot_not_selected")
        row, price = _quote(snapshot, identity, now or datetime.now(timezone.utc))
    except (ValueError, KeyError, TypeError) as exc:
        reason = str(exc)
    conditions = {}
    for side in ("lower", "upper"):
        threshold = rule[side]
        state = "UNSET" if threshold is None else "INSUFFICIENT" if reason else (
            "MET" if (price <= Decimal(threshold) if side == "lower" else price >= Decimal(threshold)) else "NOT_MET")
        conditions[side] = {"state": state, "label": STATES[state], "threshold": threshold,
                            "comparison_price": None if reason else str(price)}
    return {"version": VERSION, "security_identity": identity, "rule_version": rule["rule_version"],
            "conditions": conditions, "reason": reason, "quote": row,
            "batch_id": snapshot.get("batch_id") if snapshot else None,
            "evidence_sha256": snapshot.get("file_sha256") if snapshot else None,
            "risk_assessment": "NOT_ASSESSED", "historical_only": True,
            "qualification": "0/3", "data_ready": False, "hard_gate_authorized": False,
            "records": [], "candidates": [], "tickets": [], "orders": []}


def compare(first, second, identity, rule_first, rule_second, *, now=None):
    left = evaluate(first, identity, rule_first, now=now)
    right = evaluate(second, identity, rule_second, now=now)
    result = {"before": left, "after": right, "comparable": False, "price_change": None,
              "rule_changed": rule_first["rule_version"] != rule_second["rule_version"],
              "message": "暂无可比较数据"}
    if first is None or second is None or first["batch_id"] == second["batch_id"]:
        return result
    if (first["scope"], first["source_version"]) != (second["scope"], second["source_version"]):
        return result
    if left["reason"] or right["reason"]:
        return result
    if _time(left["quote"]["available_at"]) > _time(right["quote"]["event_time"]):
        return result
    result.update(comparable=True,
                  price_change=str(_positive(right["quote"]["price"]) - _positive(left["quote"]["price"])),
                  message="规则版本改变，状态差异不能归因为行情触发" if result["rule_changed"] else
                  "仅比较两次选定历史快照，不证明连续路径、突破或当前触发")
    return result


class ObservationStore:
    """Append-only local settings and imports, separate from positions/orders."""
    def __init__(self, root, scope):
        if scope not in SCOPES:
            raise ValueError("scope_invalid")
        self.scope = scope
        self.root = Path(root) / VERSION / scope

    def import_bytes(self, data, expected_sha256, *, now=None):
        snapshot = import_snapshot(data, expected_sha256=expected_sha256,
                                   test_only=self.scope == SCOPES[1], now=now)
        path = self.root / "snapshots" / (stable_hash(snapshot["batch_id"]) + ".json")
        envelope = {"data": base64.b64encode(data).decode("ascii"), "external_sha256": expected_sha256}
        if path.exists():
            if _json(path.read_bytes()) != envelope:
                raise ValueError("batch_already_imported_with_different_bytes")
            return snapshot
        write_once(path, envelope)
        return snapshot

    def snapshots(self, *, now=None):
        items = []
        for path in sorted((self.root / "snapshots").glob("*.json")):
            envelope = _json(path.read_bytes())
            item = import_snapshot(base64.b64decode(envelope["data"], validate=True),
                                   expected_sha256=envelope["external_sha256"],
                                   test_only=self.scope == SCOPES[1], now=now)
            if path.stem != stable_hash(item["batch_id"]):
                raise ValueError("stored_batch_identity_mismatch")
            items.append(item)
        return items

    def save_settings(self, pool, rules, tracking):
        identities = normalize_pool(pool)
        tracking = sorted({_identity(value) for value in tracking})
        if not set(tracking) <= set(identities):
            raise ValueError("tracking_outside_explicit_pool")
        checked = {}
        for identity in identities:
            rule = rules.get(identity, make_rule(identity))
            _check_rule(rule, identity)
            checked[identity] = rule
        body = {"version": VERSION, "scope": self.scope, "pool": identities,
                "rules": checked, "tracking": tracking}
        revision = stable_hash(body)
        path = self.root / "settings" / (revision + ".json")
        if not path.exists():
            write_once(path, body)
        elif _json(path.read_bytes()) != body:
            raise ValueError("settings_hash_mismatch")
        return revision

    def settings(self):
        result = {}
        for path in sorted((self.root / "settings").glob("*.json")):
            body = _json(path.read_bytes())
            if body.get("version") != VERSION or body.get("scope") != self.scope or stable_hash(body) != path.stem:
                raise ValueError("settings_hash_or_scope_invalid")
            for identity in normalize_pool(body["pool"]):
                _check_rule(body["rules"][identity], identity)
            if not set(body["tracking"]) <= set(body["pool"]):
                raise ValueError("tracking_outside_explicit_pool")
            result[path.stem] = body
        return result

    def save_refresh(self, envelope):
        from overnight_quant.data.quote_observation_refresh import VERSION as contract, decode_response
        snapshot = decode_response(envelope)
        if snapshot["scope"] != self.scope:
            raise ValueError("refresh_scope_mismatch")
        path = self.root / contract / "snapshots" / (stable_hash(snapshot["batch_id"]) + ".json")
        body = {"response": envelope, "sha256": stable_hash(envelope)}
        write_once(path, body)
        return snapshot

    def refresh_snapshots(self):
        from overnight_quant.data.quote_observation_refresh import VERSION as contract, decode_response
        result = []
        for path in sorted((self.root / contract / "snapshots").glob("*.json")):
            body = _json(path.read_bytes())
            if stable_hash(body["response"]) != body["sha256"]:
                raise ValueError("refresh_stored_sha_mismatch")
            snapshot = decode_response(body["response"])
            if snapshot["scope"] != self.scope or path.stem != stable_hash(snapshot["batch_id"]):
                raise ValueError("refresh_stored_identity_mismatch")
            # A candidate written before final receipt is not a successful refresh.
            receipt_path = self.root / contract / "attempts" / snapshot["batch_id"] / "receipt.json"
            if not receipt_path.exists():
                continue
            receipt = _json(receipt_path.read_bytes())
            if (receipt.get("status") != "SUCCEEDED" or receipt.get("batch_id") != snapshot["batch_id"]
                    or receipt.get("cleanup_complete") is not True
                    or type(receipt.get("elapsed_ms")) not in (int, float)
                    or not 0 <= receipt["elapsed_ms"] <= 10000):
                continue
            lock = self.root / contract / "control" / "active.lock"
            if lock.exists() and _json(lock.read_bytes()).get("batch_id") == snapshot["batch_id"]:
                continue  # This controller has not confirmed publication completion.
            result.append(snapshot)
        return result
