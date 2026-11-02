from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from overnight_quant.data.baostock_daily_observation import (
    CONTRACT_HASH, DISCLAIMER, FIELDS, LIMITATIONS, PROFILE, PROFILE_HASH, SDK_CODES, observe_batch,
)
from overnight_quant.data.completed_daily_observation import CONTRACT_HASH as TENCENT_CONTRACT, observe
from overnight_quant.scripts.run_baostock_daily_observation import main

GENERATED = '2026-11-05T12:00:00+08:00'
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('source network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


def put(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seal(root):
    files = {p.name: digest(p) for p in root.iterdir() if p.is_file() and p.name != 'anchors.json'}
    put(root / 'anchors.json', {'batch_id': 'synthetic-test-only-batch', 'files': files})
    return digest(root / 'anchors.json')


def edit(root, name, function):
    value = json.loads((root / name).read_text(encoding='utf-8'))
    function(value)
    put(root / name, value)
    return seal(root)


@pytest.fixture
def batch(tmp_path):
    root = tmp_path / 'synthetic-test-only-sdk-batch'
    root.mkdir()
    r = date(2026, 11, 2)
    start, end = r - timedelta(days=120), r - timedelta(days=1)
    days = [start + timedelta(days=i) for i in range(120)]
    trading = [d for d in days if d.weekday() < 5]
    batch_id = 'synthetic-test-only-batch'
    intent = {'batch_id': batch_id, 'sdk_version': '0.9.4', 'codes': list(SDK_CODES),
              'run_date': r.isoformat(), 'start': start.isoformat(), 'end': end.isoformat(),
              'started_at': '2026-11-02T14:00:00+08:00', 'started_monotonic': 100.,
              'work_deadline': 215., 'hard_deadline': 220., 'synthetic_test_only': True}
    put(root / 'probe-intent.json', intent)
    put(root / 'process-receipt.json', {'ok': True, 'cleanup_complete': True, 'returncode': 0})
    put(root / 'batch-finished.json', {'at': '2026-11-02T14:00:20+08:00', 'monotonic': 120.})
    stages = ['login', 'calendar', *SDK_CODES, 'logout']
    put(root / 'worker-summary.json', {'batch_id': batch_id, 'ok': True,
        'logical_calls': {stage: 1 for stage in stages},
        'sdk_message_calls': [{'stage': stage, 'message_type': kind} for stage, kind in
                             zip(stages, ['00', '33', *(['95'] * 5), '02'])]})
    for index, stage in enumerate(stages):
        params = ({'start_date': start.isoformat(), 'end_date': end.isoformat()} if stage == 'calendar' else
                  {'code': stage, 'fields': ','.join(FIELDS), 'start_date': start.isoformat(),
                   'end_date': end.isoformat(), 'frequency': 'd', 'adjustflag': '2'} if stage in SDK_CODES else {})
        begin = datetime.fromisoformat(intent['started_at']) + timedelta(seconds=index + 1)
        put(root / (stage + '-started.json'), {'batch_id': batch_id, 'parameters': params,
                                              'at': begin.isoformat(), 'monotonic': 101. + index})
        fields, rows = [], []
        if stage == 'calendar':
            fields = ['calendar_date', 'is_trading_day']
            rows = [[d.isoformat(), '1' if d.weekday() < 5 else '0'] for d in days]
        elif stage in SDK_CODES:
            fields = FIELDS
            rows = [[d.isoformat(), stage, str(100 + i), str(101 + i), str(99 + i), str(100 + i), '2',
                     ['1', '0', 'opaque-status', ''][i % 4]] for i, d in enumerate(trading)]
        put(root / (stage + '-sdk.json'), {'batch_id': batch_id, 'parameters': params, 'error_code': '0',
            'representation': 'SDK_RETURNED_UNMODIFIED', 'synthetic_test_only': True,
            'response_completed_at': (begin + timedelta(seconds=.5)).isoformat(), 'completed_monotonic': 101.5 + index,
            'fields': fields, 'rows': rows, 'sdk_attributes': {'code': stage if stage in SDK_CODES else '', 'adjustflag': ''}})
        if stage not in ('login', 'logout'):
            put(root / (stage + '-iteration.json'), {'send_msg_delta': 0, 'rows_read': len(rows)})
    seal(root)
    return root


def run(root, **kwargs):
    return observe_batch(root, anchors=root / 'anchors.json', anchors_sha256=digest(root / 'anchors.json'),
                         generated_at=GENERATED, synthetic_test_only=True, **kwargs)


def safety(result):
    assert result['risk_assessment'] == 'NOT_ASSESSED'
    assert result['trading_status_semantics'] == result['suspension_fill_semantics'] == 'UNKNOWN'
    assert result['price_interpretation'] == 'VENDOR_REPORTED_QFQ_SERIES'
    assert result['qualification'] == '0/3'
    assert result['data_ready'] is result['hard_gate_authorized'] is False
    assert result['disclaimer'] == DISCLAIMER
    for field in ('records', 'candidates', 'tickets', 'orders'):
        assert result[field] == []


def test_normal_formula_calendar_index_and_unknown_rows_not_filtered(batch):
    result = run(batch)
    safety(result)
    assert result['observation_input_ready'] and result['historical_offline'] and result['synthetic_test_only']
    assert result['source_profile'] == PROFILE and result['profile_hash'] == PROFILE_HASH
    raw = json.loads((batch / (SDK_CODES[0] + '-sdk.json')).read_text())['rows']
    start, end = raw[-21], raw[-1]
    expected = float(100 * (Decimal(end[5]) / Decimal(start[5]) - 1))
    ranking = result['observation_ranking']
    assert [row['security_identity'] for row in ranking] == sorted(row['security_identity'] for row in ranking)
    for row in ranking:
        assert row['start_date'] == start[0] and row['end_date'] == end[0]
        assert row['start_close'] == start[5] and row['end_close'] == end[5]
        assert row['R20'] == pytest.approx(expected)
        assert row['limitations'] == LIMITATIONS
    for stock in result['audit']['stocks']:
        assert stock['records_received'] == len(raw)
        assert set(stock['tradestatus_distribution']) == {'1', '0', 'opaque-status', ''}
        assert [row[7] for row in stock['native_rows']] == [row[7] for row in raw]
    assert result['run_date'] == '2026-11-02' and result['completed_date'] == '2026-10-30'
    assert result['audit']['frozen_manifest_created'] is False


@pytest.mark.parametrize('name,mutation,reason', [
    ('probe-intent.json', lambda x: x.update(sdk_version='0.9.5'), 'sdk_version'),
    ('probe-intent.json', lambda x: x['codes'].pop(), 'fixed_five'),
    ('probe-intent.json', lambda x: x.update(run_date='2026-11-03'), 'run_date_range'),
    ('sz.000001-sdk.json', lambda x: x.update(batch_id='old-single-stock-batch'), 'sdk_batch'),
    ('sz.000001-started.json', lambda x: x.update(batch_id='other'), 'sdk_batch'),
    ('sz.000001-sdk.json', lambda x: x.update(representation='Tencent'), 'sdk_batch'),
    ('sz.000001-sdk.json', lambda x: x['parameters'].update(adjustflag='3'), 'parameters'),
    ('sz.000001-sdk.json', lambda x: x['rows'][-1].__setitem__(6, '3'), 'adjustment'),
    ('sz.000001-sdk.json', lambda x: x['rows'][-1].__setitem__(1, 'sh.000001'), 'identity'),
    ('sz.000001-sdk.json', lambda x: x['rows'][-1].__setitem__(0, '2026-11-02'), 'date'),
    ('sz.000001-sdk.json', lambda x: x['rows'].pop(), 'iteration'),
    ('sz.000001-sdk.json', lambda x: x['fields'].pop(), 'fields'),
    ('sz.000001-sdk.json', lambda x: x['sdk_attributes'].update(code='sz.000002'), 'identity'),
    ('sz.000001-sdk.json', lambda x: x.update(error_code='10002001'), 'sdk_batch'),
    ('sz.000001-sdk.json', lambda x: x.update(response_completed_at='2026-11-03T00:00:00+08:00'), 'wall_time'),
    ('sz.000001-sdk.json', lambda x: x.update(completed_monotonic=float('inf')), 'non_finite_json'),
    ('process-receipt.json', lambda x: x.update(cleanup_complete=False), 'cleanup'),
    ('worker-summary.json', lambda x: x['logical_calls'].update(calendar=2), 'request_set'),
    ('calendar-iteration.json', lambda x: x.update(send_msg_delta=1), 'pagination'),
])
def test_semantic_failures_even_after_resigning_anchors(batch, name, mutation, reason):
    edit(batch, name, mutation)
    result = run(batch)
    safety(result)
    assert not result['observation_input_ready'] and result['observation_ranking'] == []
    assert reason in str(result['errors'])


@pytest.mark.parametrize('value', ['0', '-1', 'NaN', 'Infinity', '-Infinity', 'not-a-price'])
def test_illegal_prices(batch, value):
    edit(batch, 'sz.000001-sdk.json', lambda x: x['rows'][-1].__setitem__(5, value))
    result = run(batch)
    assert not result['observation_input_ready'] and not result['observation_ranking']


def test_missing_stock_day_and_conflicting_duplicate(batch):
    path = batch / 'sz.000001-sdk.json'
    original = json.loads(path.read_text())
    def missing(value):
        value['rows'].pop(-10)
    edit(batch, path.name, missing)
    edit(batch, 'sz.000001-iteration.json', lambda x: x.update(rows_read=len(original['rows']) - 1))
    assert 'calendar_date_coverage_missing' in str(run(batch)['errors'])
    put(path, original)
    duplicate = deepcopy(original['rows'][-1])
    duplicate[5] = str(Decimal(duplicate[5]) + Decimal('.5'))
    original['rows'].append(duplicate)
    put(path, original)
    edit(batch, 'sz.000001-iteration.json', lambda x: x.update(rows_read=len(original['rows'])))
    assert 'duplicate_sdk_date' in str(run(batch)['errors'])
    path.unlink()
    seal(batch)
    assert 'required_anchored_file_missing' in str(run(batch)['errors'])


def test_bad_ohlc_relationship(batch):
    edit(batch, 'sz.000001-sdk.json', lambda x: x['rows'][-1].__setitem__(3, '1'))
    assert 'ohlc_range_invalid' in str(run(batch)['errors'])


def test_sha_mismatch_and_source_scope_isolation(batch):
    path = batch / 'sz.000001-sdk.json'
    path.write_text(path.read_text() + ' ')
    assert 'original_file_sha256_mismatch' in str(run(batch)['errors'])
    seal(batch)
    edit(batch, 'probe-intent.json', lambda x: x.update(synthetic_test_only=False))
    assert 'synthetic_test_only_isolation' in str(run(batch)['errors'])
    assert CONTRACT_HASH != TENCENT_CONTRACT
    sdk_value = json.loads(path.read_text())
    old = observe(sdk_value, source_file_sha256=digest(path), manifest=None, run_date='2026-11-02',
                  generated_at=GENERATED, historical_offline=True, synthetic_test_only=True)
    assert not old['observation_input_ready'] and not old['observation_ranking']
    assert 'source_evidence_schema_invalid' in str(old['errors'])


def test_reordering_and_non_decision_material(batch):
    first = run(batch)
    for code in SDK_CODES:
        edit(batch, code + '-sdk.json', lambda x: x['rows'].reverse())
    edit(batch, 'calendar-sdk.json', lambda x: x['rows'].reverse())
    put(batch / 'fund-flow-audit-only.json', {'semantics': 'UNPROVEN', 'main_net': -999})
    seal(batch)
    second = run(batch)
    assert first['decision_hash'] == second['decision_hash']
    assert [(r['code'], r['R20'], r['rank']) for r in first['observation_ranking']] == [
        (r['code'], r['R20'], r['rank']) for r in second['observation_ranking']]
    edit(batch, 'sz.000001-sdk.json', lambda x: x['rows'][0].__setitem__(7, 'new-unknown-native-state'))
    changed = run(batch)
    assert changed['decision_hash'] != second['decision_hash']
    assert changed['trading_status_semantics'] == 'UNKNOWN'


def test_actual_cli_default_network_refusal_and_two_replays(batch, tmp_path):
    args = ['--batch-dir', str(batch), '--anchors', str(batch / 'anchors.json'),
            '--anchors-sha256', digest(batch / 'anchors.json'), '--synthetic-test-only']
    outputs = []
    for i in range(2):
        output = tmp_path / f'replay-{i}.json'
        process = subprocess.run([sys.executable, '-m', 'overnight_quant.scripts.run_baostock_daily_observation',
                                  *args, '--output', str(output)], cwd=REPO, capture_output=True, text=True, timeout=15)
        assert process.returncode == 0, process.stderr
        outputs.append(json.loads(output.read_text(encoding='utf-8')))
    assert outputs[0]['decision_hash'] == outputs[1]['decision_hash']
    assert outputs[0]['observation_ranking'] == outputs[1]['observation_ranking']
    with pytest.raises(SystemExit):
        main(args + ['--output', str(output)])
    with pytest.raises(SystemExit):
        main(args + ['--output', str(batch / 'forbidden.json')])
    for tail in ([], ['--network']):
        process = subprocess.run([sys.executable, '-m', 'overnight_quant.scripts.run_baostock_daily_observation', *tail],
                                 cwd=REPO, capture_output=True, text=True, timeout=15)
        assert process.returncode == 2


def test_future_generated_time_and_cleanup_block(batch):
    early = observe_batch(batch, anchors=batch / 'anchors.json', anchors_sha256=digest(batch / 'anchors.json'),
                          generated_at='2026-11-02T13:00:00+08:00', synthetic_test_only=True)
    assert 'capture_or_generation_time' in str(early['errors'])
    put(batch / 'cleanup-incomplete.json', {'cleanup_complete': False})
    seal(batch)
    assert 'capture_cleanup_unconfirmed' in str(run(batch)['errors'])


def test_untrusted_semantics_flags_never_upgrade_or_change_decision(batch):
    baseline = run(batch)
    edit(batch, 'worker-summary.json', lambda x: x.update(trading_status_semantics='VERIFIED', eligible_for_hard_gate=True))
    edit(batch, 'sz.000001-sdk.json', lambda x: x.update(suspension_fill_semantics='VERIFIED'))
    result = run(batch)
    safety(result)
    assert result['decision_hash'] == baseline['decision_hash']
    result['source_profile']['codes'].clear()
    assert run(batch)['source_profile']['codes'] == list(SDK_CODES)


@pytest.mark.parametrize('name,mutation', [
    ('process-receipt.json', lambda x: x.update(returncode=False)),
    ('worker-summary.json', lambda x: x['logical_calls'].update(login=True)),
    ('calendar-iteration.json', lambda x: x.update(send_msg_delta=False)),
    ('probe-intent.json', lambda x: x.update(synthetic_test_only='true')),
])
def test_boolean_numbers_and_nonboolean_test_markers_rejected(batch, name, mutation):
    edit(batch, name, mutation)
    result = run(batch)
    assert not result['observation_input_ready'] and result['observation_ranking'] == []


@pytest.mark.parametrize('encoding', ['cp1252', 'utf-8'])
@pytest.mark.parametrize('case', ['success', 'bad-anchor', 'write-failure'])
def test_actual_cli_console_encoding_keeps_json_and_exit_contract(batch, tmp_path, encoding, case):
    output = tmp_path / 'observation.json'
    anchor_sha = digest(batch / 'anchors.json')
    if case == 'bad-anchor':
        anchor_sha = '0' * 64
    elif case == 'write-failure':
        parent_file = tmp_path / '\u4e2d\u6587-not-a-directory'
        parent_file.write_bytes(b'not a directory')
        output = parent_file / 'result.json'
    process = subprocess.run(
        [sys.executable, '-m', 'overnight_quant.scripts.run_baostock_daily_observation',
         '--batch-dir', str(batch), '--anchors', str(batch / 'anchors.json'),
         '--anchors-sha256', anchor_sha, '--synthetic-test-only', '--output', str(output)],
        cwd=REPO, capture_output=True, timeout=15,
        env={**os.environ, 'PYTHONIOENCODING': encoding + ':strict', 'PYTHONUTF8': '0'},
    )
    stdout, stderr = process.stdout.decode(encoding), process.stderr.decode(encoding)
    assert process.returncode == (0 if case == 'success' else 2), stderr
    assert 'UnicodeEncodeError' not in stderr
    if case == 'write-failure':
        assert 'OBSERVATION_WRITE_FAILED' in stdout
        assert not output.exists()
        return
    expected_console = DISCLAIMER.encode(encoding, errors='backslashreplace').decode(encoding)
    assert expected_console in stdout
    assert ('VENDOR_PRICE_OBSERVATION_COMPUTED' if case == 'success' else 'OBSERVATION_REJECTED') in stdout
    result = json.loads(output.read_bytes().decode('utf-8'))
    safety(result)
    assert result['disclaimer'] == DISCLAIMER
    assert result['observation_input_ready'] is (case == 'success')
    assert len(result['observation_ranking']) == (5 if case == 'success' else 0)
    if case == 'success':
        baseline = run(batch)
        assert result['decision_hash'] == baseline['decision_hash']
        assert result['observation_ranking'] == baseline['observation_ranking']
