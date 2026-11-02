"""All responses below are synthetic; no source requests or real authorization."""
import base64
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from overnight_quant.data import daily_freeze as daily
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.scripts.run_daily_freeze import main


def fixtures(tmp_path, edit=None):
    run_day = datetime.now(CN_TZ).date()
    days, day = [], run_day - timedelta(days=1)
    while len(days) < 70:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    days.sort()
    responses = []
    for index, param in enumerate(daily.PLAN):
        symbol = param.split(',')[0]
        rows = [[d, '10', '10.5', '11', '9', '1000'] for d in days]
        body = json.dumps({'data': {symbol: {'qfqday': rows}}}).encode()
        responses.append({'content_base64': base64.b64encode(body).decode()})
    if edit:
        edit(responses)
    path = tmp_path / 'fixture.json'
    path.write_text(json.dumps({'responses': responses}))
    return path, run_day.isoformat()


def invoke(tmp_path, edit=None):
    fixture, day = fixtures(tmp_path, edit)
    output = tmp_path / 'batch'
    result = daily.capture(day=day, codes=','.join(daily.FIXED_CODES), approved_sha=daily.head(),
        output=output, authorization={'batch_id': 'synthetic-1'}, fixture=fixture)
    return result, output


def test_full_six_actual_process_cli_and_double_replay(tmp_path, capsys):
    fixture, day = fixtures(tmp_path)
    output = tmp_path / 'batch'
    assert main(['--test-fixture', str(fixture), '--test-batch-id', 'test-only', '--date', day,
        '--codes', ','.join(daily.FIXED_CODES), '--approved-sha', daily.head(), '--output', str(output)]) == 0
    captured = json.loads(capsys.readouterr().out)
    assert captured['status'] == 'EXECUTION_COMPLETE'
    assert len(list(output.glob('request-*.json'))) == 6
    before = {p.name: daily.sha(p) for p in output.iterdir()}
    replay1 = daily.verify(output, captured['anchors'])
    replay2 = daily.verify(output, captured['anchors'])
    assert replay1 == replay2
    assert captured['observation_ranking'] == replay1['observation_ranking']
    assert captured['decision_hash'] == replay1['decision_hash']
    assert replay1['evidence_integrity_verified'] and replay1['observation_input_ready']
    assert len(replay1['observation_ranking']) == 5
    assert replay1['synthetic_test_only']
    assert replay1['risk_assessment'] == 'NOT_ASSESSED'
    assert before == {p.name: daily.sha(p) for p in output.iterdir()}
    anchors = tmp_path/'anchors.json'
    anchors.write_text(json.dumps(captured['anchors']))
    assert main(['--verify', str(output), '--anchors', str(anchors), '--anchors-sha256', daily.sha(anchors)]) == 0
    assert json.loads(capsys.readouterr().out) == replay1
    for key in ('records', 'candidates', 'tickets', 'orders'):
        assert replay1[key] == []


@pytest.mark.parametrize('bad', ['codes', 'date', 'sha', 'output', 'batch'])
def test_bad_input_zero_requests(tmp_path, bad):
    fixture, day = fixtures(tmp_path)
    args = dict(day=day, codes=','.join(daily.FIXED_CODES), approved_sha=daily.head(),
                output=tmp_path/'batch', fixture=fixture, authorization={'batch_id': 'test'})
    if bad == 'codes': args['codes'] = ''
    if bad == 'date': args['day'] = '2000-01-01'
    if bad == 'sha': args['approved_sha'] = '0'*40
    if bad == 'output': args['output'].mkdir()
    if bad == 'batch': args['authorization'] = {}
    result = daily.capture(**args)
    assert result['status'] == 'REQUEST_REJECTED'
    assert not list(tmp_path.rglob('request-*.json'))


def test_default_and_network_without_permission_zero_workers(monkeypatch):
    monkeypatch.setattr(daily, 'run_probe_worker_process', lambda *a, **k: pytest.fail('worker dispatched'))
    assert daily.capture(day=None, codes=None, approved_sha=None, output=None)['status'] == 'NETWORK_NOT_REQUESTED'
    assert main(['--network']) == 2


@pytest.mark.parametrize('index', [0, 2])
def test_parse_failure_preserves_raw_and_stops(tmp_path, index):
    def edit(responses):
        responses[index]['content_base64'] = base64.b64encode(b'not-json').decode()
    result, output = invoke(tmp_path, edit)
    assert result['status'] == 'EXECUTION_FAILED'
    assert len(list(output.glob('request-*.json'))) == index+1
    raw = json.loads((output/f'raw-{index}.json').read_text())
    assert base64.b64decode(raw['content_base64']) == b'not-json'
    assert not (output/'manifest.json').exists()
    verified = daily.verify(output, result['anchors'])
    assert verified['evidence_integrity_verified']
    assert verified['capture_execution_result'] == 'FAILED'
    assert verified['observation_ranking'] == []


def test_network_error_does_not_invent_response(tmp_path):
    result, output = invoke(tmp_path, lambda r: r[0].update(network_error=True))
    assert result['status'] == 'EXECUTION_FAILED'
    assert not list(output.glob('raw-*.json'))
    assert len(list(output.glob('request-*.json'))) == 1


def test_duplicate_and_shared_lock_never_removed(tmp_path):
    fixture, day = fixtures(tmp_path)
    state = tmp_path/'state'
    state.mkdir()
    lock = state/'active.lock'
    lock.write_text('other-controller')
    result = daily.capture(day=day, codes=','.join(daily.FIXED_CODES), approved_sha=daily.head(),
        output=tmp_path/'new', fixture=fixture, authorization={'batch_id': 'test'})
    assert result['status'] == 'REQUEST_REJECTED' and lock.read_text() == 'other-controller'
    assert not list(tmp_path.rglob('request-*.json'))


def test_persistent_cleanup_guard_blocks(tmp_path):
    fixture, day = fixtures(tmp_path)
    state = tmp_path/'state'
    state.mkdir()
    guard = state/'cleanup-incomplete.json'
    guard.write_text('preserve')
    result = daily.capture(day=day, codes=','.join(daily.FIXED_CODES), approved_sha=daily.head(),
        output=tmp_path/'new', fixture=fixture, authorization={'batch_id': 'test'})
    assert result['status'] == 'REQUEST_REJECTED' and guard.read_text() == 'preserve'


def test_timeout_real_process_cleanup_stops_requests(tmp_path):
    result, output = invoke(tmp_path, lambda r: r[0].update(sleep=12))
    assert result['status'] == 'EXECUTION_FAILED'
    process = json.loads((output/'process-0.json').read_text())
    assert process['request_timed_out'] and process['cleanup_complete']
    assert len(list(output.glob('request-*.json'))) == 1


def test_manifest_tamper_and_cross_batch_rejected(tmp_path):
    result, output = invoke(tmp_path)
    assert result['status'] == 'EXECUTION_COMPLETE'
    manifest = json.loads((output/'manifest.json').read_text())
    manifest['batch_id'] = 'other'
    (output/'manifest.json').write_text(json.dumps(manifest))
    assert not daily.verify(output, result['anchors'])['evidence_integrity_verified']
    anchors = deepcopy(result['anchors'])
    anchors['manifest'] = daily.sha(output/'manifest.json')
    anchors['files']['manifest.json'] = anchors['manifest']
    assert not daily.verify(output, anchors)['evidence_integrity_verified']


def test_no_redirect_handler():
    from overnight_quant.scripts.run_daily_freeze_worker import NoRedirect
    assert NoRedirect().redirect_request(None, None, None, None, None, None) is None


def test_raw_seal_failure_stops_no_success_manifest(tmp_path):
    result, output = invoke(tmp_path, lambda r: r[1].update(seal_failure=True))
    assert result['status'] == 'EXECUTION_FAILED'
    assert len(list(output.glob('request-*.json'))) == 2
    assert (output/'raw-0.json').exists() and not (output/'raw-1.json').exists()
    assert not (output/'manifest.json').exists()
    assert daily.verify(output, result['anchors'])['observation_ranking'] == []


def test_missing_day_does_not_publish_success_manifest(tmp_path):
    def edit(responses):
        value = json.loads(base64.b64decode(responses[2]['content_base64']))
        next(iter(value['data'].values()))['qfqday'].pop()
        responses[2]['content_base64'] = base64.b64encode(json.dumps(value).encode()).decode()
    result, output = invoke(tmp_path, edit)
    assert result['status'] == 'EXECUTION_FAILED'
    assert not (output/'manifest.json').exists()
    assert len(list(output.glob('request-*.json'))) == 3


def test_budget_insufficient_dispatches_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, 'run_probe_worker_process', lambda *a, **k: pytest.fail('dispatch'))
    transport = daily.CaptureTransport({'work_deadline': time.perf_counter()+19}, tmp_path)
    with pytest.raises(TimeoutError, match='budget_insufficient'):
        transport.request('GET', daily.TENCENT_KLINE_URL, params={'param': daily.PLAN[0]},
                          data=None, headers={}, timeout_seconds=10)
    assert transport.index == 0


def test_unconfirmed_cleanup_persists_guard(tmp_path, monkeypatch):
    fixture, day = fixtures(tmp_path)
    monkeypatch.setattr(daily, 'run_probe_worker_process', lambda *a, **k: {
        'ok': False, 'cleanup_complete': False, 'error_code': 'UNCONFIRMED', 'pid': 42})
    args = dict(day=day, codes=','.join(daily.FIXED_CODES), approved_sha=daily.head(),
                output=tmp_path/'batch', fixture=fixture, authorization={'batch_id': 'test'})
    assert daily.capture(**args)['status'] == 'EXECUTION_FAILED'
    before = (tmp_path/'state/cleanup-incomplete.json').read_bytes()
    args['output'] = tmp_path/'different'
    assert daily.capture(**args)['status'] == 'REQUEST_REJECTED'
    assert (tmp_path/'state/cleanup-incomplete.json').read_bytes() == before
    assert (tmp_path/'state/active.lock').exists()


def test_concurrent_actual_cli_and_repeat_no_duplicate_dispatch(tmp_path):
    fixture, day = fixtures(tmp_path, lambda r: r[0].update(sleep=1))
    output = tmp_path/'batch'
    command = [sys.executable, '-m', 'overnight_quant.scripts.run_daily_freeze',
        '--test-fixture', str(fixture), '--test-batch-id', 'test-concurrent', '--date', day,
        '--codes', ','.join(daily.FIXED_CODES), '--approved-sha', daily.head(), '--output', str(output)]
    first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        until = time.monotonic()+5
        while not (tmp_path/'state/active.lock').exists() and time.monotonic() < until:
            time.sleep(.01)
        second = subprocess.run(command, capture_output=True, text=True, timeout=10)
        out, err = first.communicate(timeout=30)
        assert first.returncode == 0, (out, err)
        assert second.returncode == 2
        before = {p.name: daily.sha(p) for p in output.iterdir()}
        repeat = subprocess.run(command, capture_output=True, text=True, timeout=10)
        assert repeat.returncode == 2
        assert before == {p.name: daily.sha(p) for p in output.iterdir()}
        assert len(list(output.glob('request-*.json'))) == 6
    finally:
        if first.poll() is None:
            first.kill()
        first.communicate(timeout=5)


def test_final_absolute_time_cannot_be_resigned_as_success(tmp_path):
    result, output = invoke(tmp_path)
    anchors = deepcopy(result['anchors'])
    intent = json.loads((output/'intent.json').read_text())
    anchors['finished_monotonic'] = intent['started_monotonic'] + 90.001
    replay = daily.verify(output, anchors)
    assert not replay['evidence_integrity_verified']
    assert replay['observation_ranking'] == []


@pytest.mark.parametrize('failure_receipt_writable,expired', [(True, False), (False, False), (True, True)])
def test_cli_parent_receipt_error_after_real_workers_is_unknown_not_zero(tmp_path, monkeypatch, capsys,
                                                                       failure_receipt_writable, expired):
    fixture, day = fixtures(tmp_path)
    output = tmp_path/'batch'
    original_write = daily.write
    original_clock = time.perf_counter
    after_error = [False]
    monkeypatch.setattr(daily.time, 'perf_counter', lambda: original_clock() + (100 if after_error[0] and expired else 0))
    saved_hashes, attempts = {}, []

    def fail_parent_receipt(path, value):
        path = Path(path)
        if path.name == 'execution.json':
            saved_hashes.update({p.name: daily.sha(p) for p in output.glob('raw-*.json')})
            after_error[0] = True
            raise OSError('fixture_parent_execution_write_failed')
        if path.name == 'controller-failure.json':
            attempts.append(path.name)
            if not failure_receipt_writable:
                raise OSError('fixture_failure_receipt_write_failed')
        return original_write(path, value)

    monkeypatch.setattr(daily, 'write', fail_parent_receipt)
    code = main(['--test-fixture', str(fixture), '--test-batch-id', 'parent-failure', '--date', day,
                 '--codes', ','.join(daily.FIXED_CODES), '--approved-sha', daily.head(), '--output', str(output)])
    result = json.loads(capsys.readouterr().out)
    assert code == 2
    assert result['status'] == 'CONTROLLER_FAILED'
    assert result['dispatch_attempted'] is True
    assert result['requests_dispatched'] is None
    assert result['request_count_status'] == 'UNKNOWN_AFTER_DISPATCH'
    assert result['cleanup_complete'] is True
    assert result['failure_receipt_persisted'] is (failure_receipt_writable and not expired)
    assert result['observation_ranking'] == []
    assert result['observation_input_ready'] is False
    assert 'fixture_parent_execution_write_failed' in result['error']
    assert len(list(output.glob('request-*.json'))) == 6
    assert saved_hashes == {p.name: daily.sha(p) for p in output.glob('raw-*.json')}
    assert attempts == ([] if expired else ['controller-failure.json'])
    if failure_receipt_writable and not expired:
        receipt = json.loads((output/'controller-failure.json').read_text())
        assert receipt['requests_dispatched'] is None
        assert receipt['error'] == result['error']
    else:
        assert not (output/'controller-failure.json').exists()
        assert ('budget_exhausted' if expired else 'fixture_failure_receipt_write_failed') in result['failure_receipt_error']


def test_before_dispatch_exception_is_still_known_zero(tmp_path, monkeypatch):
    fixture, day = fixtures(tmp_path)
    monkeypatch.setattr(daily, 'run_probe_worker_process', lambda *a, **k: pytest.fail('dispatch forbidden'))
    result = daily.capture(day=day, codes='', approved_sha=daily.head(), output=tmp_path/'batch',
                           fixture=fixture, authorization={'batch_id': 'preflight'})
    assert result['status'] == 'REQUEST_REJECTED'
    assert result['dispatch_attempted'] is False
    assert result['requests_dispatched'] == 0
    assert result['request_count_status'] == 'KNOWN_ZERO_BEFORE_DISPATCH'


def test_dispatch_exception_with_guard_write_failure_retains_lock(tmp_path, monkeypatch):
    fixture, day = fixtures(tmp_path)
    original_write = daily.write

    def dispatch_error(*args, **kwargs):
        raise OSError('fixture_dispatch_return_unavailable')

    def guard_error(path, value):
        if Path(path).name == 'cleanup-incomplete.json':
            raise OSError('fixture_guard_write_failed')
        return original_write(path, value)

    monkeypatch.setattr(daily, 'run_probe_worker_process', dispatch_error)
    monkeypatch.setattr(daily, 'write', guard_error)
    result = daily.capture(day=day, codes=','.join(daily.FIXED_CODES), approved_sha=daily.head(),
                           output=tmp_path/'batch', fixture=fixture, authorization={'batch_id': 'unknown'})
    assert result['status'] == 'CONTROLLER_FAILED'
    assert result['requests_dispatched'] is None and result['cleanup_complete'] is None
    assert 'fixture_dispatch_return_unavailable' in result['error']
    assert 'fixture_guard_write_failed' in result['cleanup_guard_error']
    assert (tmp_path/'state/active.lock').exists()


@pytest.mark.parametrize('field,bad', [('evidence_integrity_verified', False),
    ('capture_execution_result', 'FAILED'), ('observation_input_ready', False)])
def test_second_final_verify_failure_actual_capture_cli_and_replay(tmp_path, monkeypatch, capsys, field, bad):
    fixture, day = fixtures(tmp_path)
    output = tmp_path/'batch'
    original_verify = daily.verify
    calls, snapshots = [], {}

    def second_verification_fails(*args, **kwargs):
        value = original_verify(*args, **kwargs)
        calls.append(deepcopy(value))
        if len(calls) == 2:
            snapshots.update({p.name: daily.sha(p) for p in output.iterdir() if p.is_file()})
            value[field] = bad
            value['errors'] = ['fixture_second_final_verification_failed']
        return value

    monkeypatch.setattr(daily, 'verify', second_verification_fails)
    code = main(['--test-fixture', str(fixture), '--test-batch-id', 'final-failure', '--date', day,
        '--codes', ','.join(daily.FIXED_CODES), '--approved-sha', daily.head(), '--output', str(output)])
    result = json.loads(capsys.readouterr().out)
    assert len(calls) == 2 and calls[0]['observation_input_ready'] is True
    assert code == 2
    assert result['status'] != 'EXECUTION_COMPLETE'
    assert result['capture_execution_result'] == 'FAILED'
    assert result['observation_input_ready'] is False and result['observation_ranking'] == []
    assert 'fixture_second_final_verification_failed' in result['errors']
    assert len(list(output.glob('request-*.json'))) == 6
    assert all(daily.sha(output/name) == digest for name, digest in snapshots.items())
    assert (output/'observation.json').exists()  # Intermediate file is not final authority.
    monkeypatch.setattr(daily, 'verify', original_verify)
    replay = daily.verify(output, result['anchors'])
    assert replay['status'] == result['status']
    assert replay['capture_execution_result'] == 'FAILED'
    assert replay['observation_input_ready'] is False and replay['observation_ranking'] == []
    anchor_path = tmp_path/'anchors.json'
    anchor_path.write_text(json.dumps(result['anchors']))
    assert main(['--verify', str(output), '--anchors', str(anchor_path), '--anchors-sha256', daily.sha(anchor_path)]) == 2


@pytest.mark.parametrize('field,bad', [('evidence_integrity_verified', False),
    ('evidence_integrity_verified', 1), ('capture_execution_result', 'FAILED'),
    ('observation_input_ready', False), ('observation_input_ready', 1)])
def test_cli_success_status_alone_never_suffices(monkeypatch, capsys, field, bad):
    from overnight_quant.scripts import run_daily_freeze as cli
    result = daily.safe(status='EXECUTION_COMPLETE', evidence_integrity_verified=True,
                        capture_execution_result='PASSED', observation_input_ready=True)
    result[field] = bad
    monkeypatch.setattr(cli, 'capture', lambda **kwargs: result)
    assert cli.main(['--test-fixture', 'not-read-because-test-stub']) == 2


def test_default_cli_remains_successful_zero_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(daily, 'run_probe_worker_process', lambda *a, **k: pytest.fail('dispatch'))
    assert main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'NETWORK_NOT_REQUESTED' and result['requests_dispatched'] == 0


def test_missing_final_outcome_cannot_use_intermediate_observation(tmp_path):
    result, output = invoke(tmp_path)
    assert result['status'] == 'EXECUTION_COMPLETE'
    assert json.loads((output/'observation.json').read_text())['observation_input_ready'] is True
    anchors = deepcopy(result['anchors'])
    anchors.pop('final_outcome')
    replay = daily.verify(output, anchors)
    assert replay['status'] == 'FINAL_VERIFICATION_FAILED'
    assert replay['observation_input_ready'] is False and replay['observation_ranking'] == []
    outcome = json.loads((output/'final-outcome.json').read_text())
    outcome['batch_id'] = 'other-batch'
    (output/'final-outcome.json').write_text(json.dumps(outcome))
    anchors['final_outcome'] = daily.sha(output/'final-outcome.json')
    anchors['files']['final-outcome.json'] = anchors['final_outcome']
    assert daily.verify(output, anchors)['capture_execution_result'] == 'FAILED'


def test_final_outcome_write_failure_retains_materials_and_count_unknown(tmp_path, monkeypatch):
    original_write = daily.write
    def fail_final(path, value):
        if Path(path).name == 'final-outcome.json':
            raise OSError('fixture_final_outcome_write_failed')
        return original_write(path, value)
    monkeypatch.setattr(daily, 'write', fail_final)
    result, output = invoke(tmp_path)
    assert result['status'] == 'CONTROLLER_FAILED'
    assert result['requests_dispatched'] is None
    assert result['request_count_status'] == 'UNKNOWN_AFTER_DISPATCH'
    assert result['observation_ranking'] == []
    assert 'fixture_final_outcome_write_failed' in result['error']
    assert result['anchors'] and result['anchors_complete'] is False
    assert (output/'observation.json').exists() and not (output/'final-outcome.json').exists()
    assert daily.verify(output, result['anchors'])['observation_input_ready'] is False
