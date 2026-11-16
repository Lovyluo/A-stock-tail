"""Limited v3 through retained fixture bytes, never a live quote transport."""
from datetime import datetime, timedelta
import hashlib
import json
import time

import pytest

from overnight_quant.data import offline_price_observation as obs
from overnight_quant.data import quote_observation_permit as permit
from overnight_quant.data import quote_observation_refresh as refresh
from overnight_quant.data.quote_observation_worker import execute
from overnight_quant.data.tencent_direct_http_providers import TencentProviderContractError
from overnight_quant.tests.test_concept_observation_handoff import captured, navigate, open_page
from overnight_quant.tests.test_manual_quote_observation import NOW, local_runner, raw_quote
from overnight_quant.tests.test_manual_quote_selection import fixture

SELECTION = ["SZ:000002"]
POOL = [*SELECTION, "SZ:000001"]


def approval(tmp_path, now, **updates):
    quote = "Offline fixture only. Not an approval for external requests."
    data = dict(schema=permit.PERMIT_SCHEMA, contract=permit.LIMITED_VERSION, status="TEST_ONLY",
                permit_id="fixture-permit", approved_sha="a" * 40, worktree=str(permit.CODE_ROOT),
                purpose="PERSONAL_INTERNAL_OBSERVATION", source_use_basis="UNKNOWN",
                known_use_constraint="NO_KNOWN_PROHIBITION", user_approved=True,
                acknowledges_unknown_basis=True, public_distribution=False,
                access_control_bypass=False, max_requests=2, manual_clicks_only=True, selection=SELECTION,
                date=now.date().isoformat(), window_start=(now - timedelta(minutes=1)).isoformat(),
                window_end=(now + timedelta(minutes=3)).isoformat(),
                approval_reference={"locator": "https://example.invalid/test-only",
                                    "verbatim_quote": quote, "quote_sha256": permit._sha(quote.encode())})
    path = tmp_path / "permit.json"
    path.write_text(json.dumps({**data, **updates}), encoding="utf-8")
    return path, permit._sha(path.read_bytes())


def controller(tmp_path, now, *, scope="synthetic_test_only", **updates):
    path = fixture(tmp_path / "quote.json", ["000002"], event=now - timedelta(seconds=2))
    store = obs.ObservationStore(tmp_path / "settings", scope)
    return refresh.RefreshController(store, contract=permit.LIMITED_VERSION,
                                     fixture=path, test_permit=approval(tmp_path, now, **updates))


def target(control, result):
    return control.store.root / permit.LIMITED_VERSION / "attempts" / result["batch_id"]


def test_two_clicks_quota_reentry_shared_lock_and_restart(tmp_path, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(refresh, "utc_now", lambda: clock[0])
    control = controller(tmp_path, NOW)
    def reopen():
        return refresh.RefreshController(control.store, contract=permit.LIMITED_VERSION,
                                         fixture=control.fixture, test_permit=control.test_permit)
    calls = []
    def runner(task, *args, **kwargs):
        calls.append(task)
        assert "FileExistsError" in reopen().refresh(POOL, selection=SELECTION)["error"]
        return local_runner(task)
    monkeypatch.setattr(refresh, "run_probe_worker_process", runner)
    first = control.refresh(POOL, selection=SELECTION)
    assert first["status"] == "SUCCEEDED", first
    assert "cooldown" in reopen().refresh(POOL, selection=SELECTION)["error"]
    ticks = time.monotonic
    monkeypatch.setattr(refresh.time, "monotonic", lambda: ticks() + 11)
    clock[0] += timedelta(seconds=11)
    fixture(control.fixture, ["000002"], event=clock[0] - timedelta(seconds=2))
    second = reopen().refresh(POOL, selection=SELECTION)
    assert second["status"] == "SUCCEEDED", second
    clock[0] += timedelta(seconds=11)
    monkeypatch.setattr(refresh.time, "monotonic", lambda: ticks() + 22)
    assert "quota_exhausted" in reopen().refresh(POOL, selection=SELECTION)["error"]
    assert len(calls) == 2 and reopen().limited_status()[1] == 0
    legacy = refresh.RefreshController(control.store, fixture=control.fixture)
    monkeypatch.setattr(refresh.time, "monotonic", lambda: ticks() + 11)
    assert "cooldown" in legacy.refresh(POOL)["error"]
    for outcome in (first, second):
        directory = target(control, outcome)
        sha = permit._sha((directory / "final-confirmation.json").read_bytes())
        assert permit.verify_batch(control.store.root, outcome["batch_id"], control.test_permit[1], sha,
                                   test_only=True)["selection"] == SELECTION
        with pytest.raises(ValueError, match="anchor"):
            permit.verify_batch(control.store.root, outcome["batch_id"], control.test_permit[1], sha)
        assert not outcome["hard_gate_authorized"] and not outcome["data_ready"]
        assert not any(outcome[k] for k in ("records", "tickets", "orders", "candidates"))


@pytest.mark.parametrize("updates", [
    {"status": "EXAMPLE"},
    {"acknowledges_unknown_basis": "true"},
    {"source_use_basis": "PROHIBITED"},
    {"known_use_constraint": "PROHIBITED"},
    {"access_control_bypass": True},
    {"contract": refresh.SELECTION_VERSION},
    {"max_requests": 3},
    {"manual_clicks_only": False},
    {"public_distribution": True},
    {"window_end": NOW.isoformat()},
    {"selection": ["SZ:000001"]},
])
def test_invalid_permission_does_not_reserve_or_dispatch(tmp_path, monkeypatch, updates):
    monkeypatch.setattr(refresh, "utc_now", lambda: NOW)
    monkeypatch.setattr(refresh, "run_probe_worker_process", lambda *args, **kw: pytest.fail("dispatched"))
    control = controller(tmp_path, NOW, **updates)
    rejected = control.refresh(POOL, selection=SELECTION)
    assert rejected["status"] == "REJECTED" and rejected["requests_made"] == 0
    assert not list(control.state.glob("limited-slot-*"))


def test_live_requires_separate_switch_real_approval_and_code(tmp_path, monkeypatch):
    store = obs.ObservationStore(tmp_path, "historical")
    monkeypatch.setattr(obs, "DEFAULT_STORE", tmp_path)
    monkeypatch.setattr(refresh, "utc_now", lambda: NOW)
    monkeypatch.setattr(refresh, "run_probe_worker_process", lambda *args, **kw: pytest.fail("dispatched"))
    control = refresh.RefreshController(store, contract=permit.LIMITED_VERSION)
    monkeypatch.setenv(refresh.NETWORK_SWITCH, "1")
    monkeypatch.delenv(permit.LIMITED_SWITCH, raising=False)
    assert "disabled" in control.refresh(POOL, selection=SELECTION)["error"]
    monkeypatch.setenv(permit.LIMITED_SWITCH, "1")
    monkeypatch.delenv(permit.PERMIT_FILE, raising=False)
    assert "required" in control.refresh(POOL, selection=SELECTION)["error"]
    for status, worktree, error in (("TEST_ONLY", permit.CODE_ROOT, "invalid"),
                                    ("USER_APPROVED", tmp_path, "worktree_mismatch"),
                                    ("USER_APPROVED", permit.CODE_ROOT, "code_mismatch")):
        path, sha = approval(tmp_path, NOW, status=status, worktree=str(worktree))
        monkeypatch.setenv(permit.PERMIT_FILE, str(path))
        monkeypatch.setenv(permit.PERMIT_SHA, sha)
        assert error in control.refresh(POOL, selection=SELECTION)["error"]
    assert not control.state.exists()


@pytest.mark.parametrize("kind", ["missing", "extra", "duplicate", "wrong_market", "stale"])
def test_bad_response_is_archived_then_freezes(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(refresh, "utc_now", lambda: NOW)
    control = controller(tmp_path, NOW)
    raw = raw_quote(NOW - timedelta(seconds=2), codes=["000002"])
    if kind == "missing":
        raw = b""
    elif kind == "extra":
        raw += raw_quote(NOW - timedelta(seconds=2), codes=["000001"])
    elif kind == "duplicate":
        raw += raw
    elif kind == "wrong_market":
        raw = raw.replace(b"sz000002", b"sh000002")
    else:
        raw = raw_quote(NOW - timedelta(seconds=61), codes=["000002"])
    fixture(control.fixture, [], raw=raw)
    # The local fixture runner reports a real cleanup on parse failure.
    def runner(task, *args, **kwargs):
        try:
            return local_runner(task)
        except (ValueError, TencentProviderContractError) as exc:
            return dict(ok=False, cleanup_complete=True, error_code="fixture_invalid", error=str(exc))
    monkeypatch.setattr(refresh, "run_probe_worker_process", runner)
    outcome = control.refresh(POOL, selection=SELECTION)
    assert outcome["status"] == "FAILED" and outcome["requests_made"] == 1, outcome
    assert (target(control, outcome) / "response-received.json").exists()
    assert not (target(control, outcome) / "response.json").exists()
    assert control.limited_status()[1:] == (1, "limited_permit_frozen_after_failure")
    assert control.store.refresh_snapshots() == []


def test_cleanup_unknown_never_refunds_and_shared_block_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh, "utc_now", lambda: NOW)
    control = controller(tmp_path, NOW)
    def runner(task, *args, **kwargs):
        assert list(control.state.glob("limited-slot-*-1.json"))
        return dict(ok=False, cleanup_complete=False, error_code="timeout", error="unknown")
    monkeypatch.setattr(refresh, "run_probe_worker_process", runner)
    outcome = control.refresh(POOL, selection=SELECTION)
    assert outcome["status"] == "FAILED" and outcome["requests_made"] is None
    assert (control.state / "cleanup-incomplete.json").exists()
    assert control.limited_status()[1] == 1
    assert "cleanup_incomplete_block" in control.refresh(POOL, selection=SELECTION)["error"]
    assert "cleanup_incomplete_block" in refresh.RefreshController(
        control.store, fixture=control.fixture).refresh(POOL)["error"]


def test_unconfirmed_publication_is_not_replayed_or_refunded(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh, "utc_now", lambda: NOW)
    monkeypatch.setattr(refresh, "run_probe_worker_process", local_runner)
    control = controller(tmp_path, NOW)
    publish = refresh.os.link

    def broken(source, destination):
        if destination.name == "final-confirmation.json":
            raise OSError("fixture confirmation failure")
        return publish(source, destination)

    monkeypatch.setattr(refresh.os, "link", broken)
    result = control.refresh(POOL, selection=SELECTION)
    directory = target(control, result)
    assert result["status"] == "UNCONFIRMED" and result["requests_made"] == 1
    assert (directory / "response-received.json").exists()
    assert not (directory / "final-confirmation.json").exists()
    assert control.store.refresh_snapshots() == []
    assert control.limited_status()[1:] == (1, "limited_permit_frozen_after_failure")


def test_real_isolated_worker_fixture_close_and_tampered_replay(tmp_path):
    now = datetime.now(permit.CN)
    if now.hour == 0 and now.minute == 0 or now.hour == 23 and now.minute >= 57:
        # Keep the fixture window in this Beijing date, even across a CI midnight.
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        updates = dict(window_start=start.isoformat(), window_end=end.isoformat())
    else:
        updates = {}
    control = controller(tmp_path, now, **updates)
    # Event may not be from yesterday at the Beijing midnight boundary.
    event = max(now - timedelta(seconds=2), now.replace(hour=0, minute=0, second=0, microsecond=0))
    fixture(control.fixture, ["000002"], event=event)
    outcome = control.refresh(POOL, selection=SELECTION)
    assert outcome["status"] == "SUCCEEDED", outcome
    assert outcome["process"]["cleanup_complete"]
    assert outcome["process"]["payload"]["network_attempts"] == 0
    directory = target(control, outcome)
    sha = permit._sha((directory / "final-confirmation.json").read_bytes())
    args = (control.store.root, outcome["batch_id"], control.test_permit[1], sha)
    permit.close_permit(control.store.root, *permit.read_permit(*control.test_permit, test_only=True))
    assert control.limited_status()[2] == "limited_permit_closed"
    assert permit.verify_batch(*args, test_only=True)["status"] == "OBSERVATION_VERIFIED"
    intent = permit._read(directory / "intent.json")
    task = dict(intent, output=str(directory), fixture=str(control.fixture))
    with pytest.raises(ValueError, match="dispatch_blocked"):
        execute(task)
    original = directory / "response-received.json"
    original.write_bytes(original.read_bytes() + b" ")
    with pytest.raises(ValueError, match="not_successful"):
        permit.verify_batch(*args, test_only=True)
    assert control.store.refresh_snapshots() == []


def test_dashboard_two_explicit_clicks_and_unchanged_price(tmp_path, monkeypatch, captured):
    from overnight_quant.ui import offline_observation as ui
    from overnight_quant.ui.quote_observation_refresh import render_refresh

    clock = [NOW]
    control = controller(tmp_path, NOW)
    calls = []
    def runner(task, *args, **kwargs):
        calls.append(task)
        return local_runner(task)
    def render(st, store, state, prefix, **kwargs):
        if store.scope == "synthetic_test_only" and kwargs.get("contract") == permit.LIMITED_VERSION:
            kwargs["controller"] = control
        return render_refresh(st, store, state, prefix, **kwargs)
    monkeypatch.setattr(refresh, "run_probe_worker_process", runner)
    monkeypatch.setattr(refresh, "utc_now", lambda: clock[0])
    monkeypatch.setattr(ui, "utc_now", lambda: clock[0])
    monkeypatch.setattr(ui, "render_refresh", render)
    app, guard = open_page(monkeypatch, tmp_path, captured)
    navigate(app, "SZ:000002")
    next(x for x in app.selectbox if x.label == "加入前采用的设置").select("当前会话设置").run()
    next(x for x in app.button if x.label == "确认加入自选池").click().run()
    next(x for x in app.radio if x.label == "观察方式").set_value("有限真实观察").run()
    button = lambda: next(x for x in app.button if x.label == "刷新一次报价")
    assert button().disabled and calls == []
    app.multiselect[0].set_value(SELECTION).run()
    assert not button().disabled and calls == []
    button().click().run()
    assert len(calls) == 1 and not app.error and not app.exception
    assert any("尚无可比较快照" in x.value for x in app.info)
    table = next(x.value for x in app.dataframe if "本次报价" in x.value.columns).set_index("证券身份")
    assert table.at["SZ:000001", "本次报价"] == "未获取"
    next(x for x in app.selectbox if x.label == "设置条件的证券").select("SZ:000002").run()
    next(x for x in app.text_input if x.label == "上沿：价格达到或高于").input("10")
    next(x for x in app.button if x.label == "保存条件").click().run()
    next(x for x in app.button if x.label == "加入跟踪").click().run()
    assert len(calls) == 1
    clock[0] += timedelta(seconds=15)
    ticks = time.monotonic
    monkeypatch.setattr(refresh.time, "monotonic", lambda: ticks() + 15)
    fixture(control.fixture, ["000002"], event=clock[0] - timedelta(seconds=2))
    button().click().run()
    assert len(calls) == 2 and not app.error and not app.exception
    assert button().disabled and any("剩余额度：0/2" in x.value for x in app.caption)
    next(x for x in app.selectbox if x.label == "观察快照").select(calls[0]["batch_id"]).run()
    next(x for x in app.selectbox if x.label == "对比快照").select(calls[1]["batch_id"]).run()
    assert any(float(x.value) == 0 for x in app.metric if x.value not in ("无", "不可比较"))
    assert len(calls) == 2 and not any(guard.business_attempts.values())
    real = obs.ObservationStore(tmp_path / "settings", "historical")
    assert real.refresh_snapshots() == [] and real.settings() == {}
