"""Deterministic synthetic task boundaries; no source or NTP access."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import math
import socket
from pathlib import Path

import pytest

from overnight_quant.data import minute_protocol_contract as contract
from overnight_quant.tests.test_phase107_minute_capture_audit import Clock
from overnight_quant.tests.test_phase108_minute_preflight import case


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*a, **kw):
        pytest.fail('external network/NTP forbidden')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)


def task(tmp_path, *, start=1000.0, lead=5.0):
    target = datetime.fromisoformat('2026-10-21T14:47:55+08:00')
    return contract.task_for(target, actual=target - timedelta(seconds=lead), started=start,
        batch_id='synthetic', code_sha='a' * 40,
        config={'path': str(tmp_path / 'test-only-config.json'), 'sha256': 'b' * 64}, test_only=True)


@pytest.mark.parametrize('start', [0.0, 1000.0, math.nextafter(1020.0, -math.inf),
    math.nextafter(2048.0, -math.inf), 1e6, 1e9])
@pytest.mark.parametrize('lead', [5.0, 4.999999, 0.000001])
def test_valid_lead_and_serialization_different_bases(tmp_path, start, lead):
    value = task(tmp_path, start=start, lead=lead)
    contract.check_task(json.loads(json.dumps(value)))
    assert value['target_monotonic'] == start + lead
    assert value['io_deadline_monotonic'] == value['target_monotonic'] + 1.5
    assert value['hard_deadline_monotonic'] == value['target_monotonic'] + 2


def test_pre_fix_cancellation_counterexample(tmp_path):
    value = task(tmp_path, start=math.nextafter(1020.0, -math.inf))
    # The old expression rejects a task generated from exactly five seconds.
    assert value['target_monotonic'] - value['preparation_started_monotonic'] > 5
    assert value['target_monotonic'] == value['preparation_started_monotonic'] + 5
    contract.check_task(value)


@pytest.mark.parametrize('lead', [5.000001, 0.0, -0.000001])
def test_invalid_lead_still_rejected_with_construction_inputs(tmp_path, lead):
    with pytest.raises(ValueError, match='deadline_contract_mismatch') as caught:
        task(tmp_path, lead=lead)
    assert 'task_construction=' in str(caught.value)
    assert 'actual' in str(caught.value) and 'started' in str(caught.value)
    assert 'start < target <= start + 5' in str(caught.value)


def test_one_ulp_outside_boundary_is_not_clamped(tmp_path):
    value = task(tmp_path)
    value['target_monotonic'] = math.nextafter(value['target_monotonic'], math.inf)
    with pytest.raises(ValueError, match='upper_difference'):
        contract.check_task(value)


@pytest.mark.parametrize('field', ['preparation_work_deadline', 'preparation_hard_deadline',
    'io_deadline_monotonic', 'hard_deadline_monotonic'])
def test_each_deadline_recomputed_with_exact_diagnostic(tmp_path, field):
    value = task(tmp_path)
    expected = value[field]
    value[field] += .001
    with pytest.raises(ValueError) as caught:
        contract.check_task(value)
    detail = json.loads(str(caught.value).split(':', 1)[1])
    row = next(row for row in detail['failures'] if row['condition'] == field)
    assert row['actual'] == value[field] and row['expected'] == expected
    assert row['difference'] == value[field] - expected


@pytest.mark.parametrize('field', ['target_monotonic', 'preparation_started_monotonic',
    'preparation_work_deadline', 'preparation_hard_deadline', 'io_deadline_monotonic', 'hard_deadline_monotonic'])
@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), float('-inf'), True, '1000', None])
def test_invalid_time_types_fail_closed(tmp_path, field, invalid):
    value = task(tmp_path)
    value[field] = invalid
    with pytest.raises(ValueError, match='deadline_invalid') as caught:
        contract.check_task(value)
    assert field in str(caught.value)


def test_a_to_b_fixture_wall_advance_keeps_real_monotonic_origin(tmp_path, monkeypatch):
    real = [math.nextafter(1020.0, -math.inf)]
    monkeypatch.setattr('time.monotonic', lambda: real[0])
    clock = Clock('14:47:50')
    a = contract.task_for(clock.base + timedelta(seconds=5), actual=clock(), started=real[0],
        batch_id='synthetic-A', code_sha='a'*40,
        config={'path': str(tmp_path/'config'), 'sha256': 'b'*64}, test_only=True)
    clock.sleep(60)
    b = contract.task_for(clock.base + timedelta(seconds=5), actual=clock(), started=real[0],
        batch_id='synthetic-B', code_sha='a'*40, config=a['config'], test_only=True)
    assert a['target_monotonic'] == b['target_monotonic']
    assert a['scheduled_at'] != b['scheduled_at']
    contract.check_task(deepcopy(b))


def test_cli_b_construction_failure_keeps_inputs_without_b_worker(case, monkeypatch, capsys):
    from overnight_quant.tests import test_phase117_protocol_differential as many
    from overnight_quant.scripts import run_minute_protocol_diagnostic as cli
    c = many.prepare(case, monkeypatch, capsys)
    original = contract.task_for

    def invalid_b(target, **kwargs):
        if target.minute == 48:
            kwargs['actual'] = target - timedelta(seconds=5.000001)
        return original(target, **kwargs)

    monkeypatch.setattr(contract, 'task_for', invalid_b)
    assert cli.main(many.command(c)) == 2
    summary = json.loads(capsys.readouterr().out)
    result = json.loads(Path(summary['evidence_path']).read_bytes())
    assert result['error'].startswith('B:ValueError:protocol_diagnostic_deadline_contract_mismatch')
    assert 'clock_sources=' in result['error'] and 'task_construction=' in result['error']
    assert 'upper_difference' in result['error'] and 'actual' in result['error']
    assert len(c['calls']) == 1  # A only; no B READY/GO or worker original fabricated.
    assert [row['id'] for row in result['items']] == ['A']
