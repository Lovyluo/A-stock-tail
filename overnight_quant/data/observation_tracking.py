"""Explicit historical comparisons and unverified user notes; no source calls."""
from datetime import datetime, timezone

from overnight_quant.data import offline_price_observation as obs
from overnight_quant.data.point_in_time import stable_hash

VERSION = "observation_tracking_v1"
DISCLAIMER = "用户记录，系统未核实"
LABELS = {"BECAME_MET": "不满足→满足", "BECAME_NOT_MET": "满足→不满足",
          "UNCHANGED": "保持不变", "INSUFFICIENT": "数据不足", "RULE_CHANGED": "规则已修改"}


def current_time():
    return datetime.now(timezone.utc)


def _time(value):
    return obs._time(value)


def change(first, second, identity, left_rule, right_rule, *, now=None):
    result = obs.compare(first, second, identity, left_rule, right_rule, now=now)
    states = {}
    for side in ("upper", "lower"):
        before = result["before"]["conditions"][side]["state"]
        after = result["after"]["conditions"][side]["state"]
        state = "INSUFFICIENT"
        if result["comparable"]:
            state = ("RULE_CHANGED" if result["rule_changed"] else
                     "BECAME_MET" if (before, after) == ("NOT_MET", "MET") else
                     "BECAME_NOT_MET" if (before, after) == ("MET", "NOT_MET") else "UNCHANGED")
        states[side] = {"state": state, "label": LABELS[state], "before": before, "after": after}
    return {**result, "transitions": states, "version": VERSION,
            "risk_assessment": "NOT_ASSESSED", "tradability": "NOT_ASSESSED",
            "historical_only": True, "records": [], "candidates": [], "tickets": [], "orders": []}


def _directory(store, kind):
    return store.root / VERSION / kind


def _read(path, store):
    body = obs._json(path.read_bytes())
    key = body.pop("revision")
    if (key != path.stem or stable_hash(body) != key or body["version"] != VERSION
            or body["scope"] != store.scope):
        raise ValueError("tracking_hash_or_scope_invalid")
    return {**body, "revision": key}


def _append(store, kind, body):
    body = {"version": VERSION, "scope": store.scope, **body}
    key = stable_hash(body)
    path = _directory(store, kind) / (key + ".json")
    if not path.exists():
        try:
            obs.write_once(path, {**body, "revision": key})
        except FileExistsError:
            pass
    return _read(path, store)


def events(store):
    return sorted((_read(p, store) for p in _directory(store, "changes").glob("*.json")),
                  key=lambda b: (b["recorded_at"], b["revision"]))


def save_change(store, first_id, second_id, identity, left_rule, right_rule):
    available = {s["batch_id"]: s for s in [*store.snapshots(), *store.refresh_snapshots()]}
    if first_id not in available or second_id not in available or first_id == second_id:
        raise ValueError("two_distinct_confirmed_snapshots_required")
    result = change(available[first_id], available[second_id], identity, left_rule, right_rule)
    if not result["comparable"]:
        raise ValueError("compatible_ordered_snapshots_required")
    identity = obs._identity(identity)
    event_key = stable_hash([VERSION, store.scope, identity, first_id, second_id,
                             left_rule["rule_version"], right_rule["rule_version"]])
    for old in events(store):
        if old["event_key"] == event_key:
            return old
    # Timestamp is evidence of this explicit save, not an inferred historic trigger time.
    body = {"event_key": event_key, "security_identity": identity,
            "recorded_at": current_time().isoformat(), "comparison": result}
    # Stable event filename prevents simultaneous identical saves creating two events.
    marker = _directory(store, "event_keys") / (event_key + ".json")
    if marker.exists():
        raise ValueError("change_save_incomplete_or_concurrent")
    obs.write_once(marker, {"event_key": event_key})
    return _append(store, "changes", body)


def notes(store, identity=None):
    identity = obs._identity(identity) if identity else None
    return sorted((b for b in (_read(p, store) for p in _directory(store, "notes").glob("*.json"))
                   if identity is None or b["security_identity"] == identity),
                  key=lambda b: (b["recorded_at"], b["revision"]))


def save_note(store, identity, item, conclusion, evidence, *, due_at=None, review=False, previous=None):
    identity = obs._identity(identity)
    fields = [item, conclusion, evidence]
    if any(not isinstance(x, str) or len(x) > 4000 for x in fields) or not item.strip() or not conclusion.strip():
        raise ValueError("note_item_and_conclusion_required_max_4000_characters")
    if type(review) is not bool:
        raise ValueError("review_flag_invalid")
    if previous and previous not in {n["revision"] for n in notes(store, identity)}:
        raise ValueError("previous_note_identity_invalid")
    now = current_time()
    due = _time(due_at).isoformat() if due_at else None
    return _append(store, "notes", {"security_identity": identity, "item": item.strip(),
        "conclusion": conclusion.strip(), "evidence": evidence.strip(), "due_at": due,
        "review_requested": review, "previous_revision": previous,
        "recorded_at": now.isoformat(), "disclaimer": DISCLAIMER,
        "risk_assessment": "NOT_ASSESSED", "tradability": "NOT_ASSESSED"})


def note_status(note, *, now=None):
    if note is None:
        return "未记录"
    tick = now or current_time()
    if note["due_at"] and _time(note["due_at"]) <= tick:
        return "已到期"
    return "待复核" if note["review_requested"] else "已记录"


def note_summary(revisions, *, now=None):
    superseded = {n["previous_revision"] for n in revisions if n["previous_revision"]}
    statuses = {note_status(n, now=now) for n in revisions if n["revision"] not in superseded}
    return next((s for s in ("已到期", "待复核", "已记录") if s in statuses), "未记录")
