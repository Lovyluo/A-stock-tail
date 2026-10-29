"""Real Python controllers and PS5.1/7; all non-process system data is synthetic."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from contextlib import chdir
from unittest.mock import patch

import pytest

from overnight_quant.data import minute_protocol_contract as single
from overnight_quant.data import minute_protocol_differential_contract as matrix
from overnight_quant.tests.test_phase108_minute_preflight import SHELLS, FIXTURES, case

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / 'overnight_quant/scripts/run_minute_protocol_diagnostic.py'


def failure_diagnostic(root, value):
    """Only stage metadata, never authorization bodies or market responses."""
    rows = []
    for path in sorted(root.rglob('*.json')):
        try:
            material = json.loads(path.read_bytes())
        except (OSError, ValueError):
            continue
        if not isinstance(material, dict):
            continue
        result = material.get('result', {})
        row = {'path': str(path)}
        for key in ('schema', 'error', 'errors', 'execution_error', 'diagnostic_result',
                    'seal_status', 'seal_completed_at', 'seal_elapsed_ms', 'cleanup_complete'):
            if key in material:
                row[key] = material[key]
        if isinstance(result, dict):
            row['stages'] = {key: item for key, item in result.items()
                if key.endswith('_monotonic') or key in ('error', 'error_code', 'stage',
                    'returncode', 'cleanup_complete', 'tree_empty_observed', 'pipes_closed',
                    'request_timed_out', 'output_complete')}
            task = result.get('task', {})
            row['deadlines'] = {key: task[key] for key in
                ('scheduled_at', 'io_deadline_monotonic', 'hard_deadline_monotonic') if key in task}
        payload = material.get('payload', material)
        if isinstance(payload, dict) and 'controller_identity' in payload:
            row['environment'] = {key: payload[key] for key in
                ('controller_identity', 'ok', 'error', 'errors') if key in payload}
        if 'items' in material:
            row['experiments'] = [{**{key: item.get(key) for key in
                ('id', 'continuation_allowed', 'continuation_checked_at')},
                'verdict': {key: item.get('verdict', {}).get(key) for key in
                    ('diagnostic_result', 'errors', 'seal_status', 'execution_materials_integrity_verified')}}
                for item in material['items']]
        if len(row) > 2 or row.get('stages'):
            rows.append(row)
    return {'test_only': True, 'summary': value, 'materials': rows}


def test_failure_diagnostic_keeps_stage_evidence_not_authorization_or_raw(tmp_path):
    material = {'schema': 'test-only', 'authorization': {'secret': 'DO_NOT_EXPORT'},
        'raw_response': 'DO_NOT_EXPORT', 'result': {'stage': 'GO', 'error_code': 'REJECTED',
            'cleanup_complete': True, 'finished_monotonic': 10.0,
            'stdout': 'DO_NOT_EXPORT', 'task': {'io_deadline_monotonic': 9.0}},
        'items': [{'id': 'B', 'continuation_allowed': False,
            'verdict': {'diagnostic_result': 'FAILED', 'trace_summary': 'DO_NOT_EXPORT'}}]}
    (tmp_path / 'evidence.json').write_text(json.dumps(material))
    (tmp_path / 'broken.json').write_text('{')
    value = failure_diagnostic(tmp_path, {'diagnostic_result': 'FAILED'})
    row = value['materials'][0]
    assert row['stages']['stage'] == 'GO'
    assert row['deadlines']['io_deadline_monotonic'] == 9.0
    assert row['experiments'][0]['id'] == 'B'
    assert 'DO_NOT_EXPORT' not in json.dumps(value)


def run_controller(args, env, root, timeout):
    from overnight_quant.data.probe_worker_process import run_probe_worker_process
    with patch.dict(os.environ, env), chdir(root):
        result = run_probe_worker_process({}, int(timeout*1000), worker_command=args,
            retain_streams=True, bounded_cleanup_ms=500)
    assert result['cleanup_complete'] and result['tree_empty_observed'], result
    return subprocess.CompletedProcess(args, result['returncode'], result['stdout'].encode(), result['stderr'].encode())


def run_probe(tmp_path, shell, *, outer=False, differential=False, fault='', form='absolute', runtime_shape='venv-or-base'):
    root = tmp_path / ('synthetic root' if form == 'quoted' else 'synthetic-root')
    entry = root / 'overnight_quant/scripts/run_minute_protocol_diagnostic.py'
    entry.parent.mkdir(parents=True)
    shutil.copyfile(CLI, entry)
    inject = tmp_path / 'injection'
    inject.mkdir()
    (inject / 'sitecustomize.py').write_text(
        'from overnight_quant.tests.fixtures.controller_identity_fixture import install\ninstall()\n', encoding='utf-8')
    temp = tmp_path / 'temp'
    temp.mkdir()
    spec = dict(test_only=True, root=str(root), output=str(tmp_path / 'query'),
        shell=shell, harness=str(FIXTURES / 't31_environment_behavior_harness.ps1'),
        temp=str(temp), network_log=str(tmp_path / 'network.json'), fault=fault, runtime_shape=runtime_shape)
    spec_path = tmp_path / 'spec.json'
    spec_path.write_text(json.dumps(spec), encoding='utf-8')
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps(matrix.frozen_plan() if differential else single.frozen_plan()), encoding='utf-8')
    import hashlib
    plan_sha = hashlib.sha256(plan.read_bytes()).hexdigest()
    authorization = tmp_path / 'unused.json'
    authorization.write_text(json.dumps({'test_only': True, 'plan_file_sha256': plan_sha}), encoding='utf-8')
    args = [sys.executable, str(entry), '--network', '--date', '2026-10-21',
        '--plan', str(plan), '--expected-plan-sha256', hashlib.sha256(plan.read_bytes()).hexdigest(),
        '--authorization', str(authorization), '--authorization-sha256', hashlib.sha256(authorization.read_bytes()).hexdigest()]
    if differential:
        args.append('--differential')
    else:
        args += ['--target-at', '2026-10-21T14:49:55+08:00']
    if form == 'relative':
        args[1] = str(entry.relative_to(root))
    elif form in ('module','compact-module'):
        prefix = ['-m', 'overnight_quant.scripts.run_minute_protocol_diagnostic'] if form == 'module' else ['-movernight_quant.scripts.run_minute_protocol_diagnostic']
        args = [args[0], *prefix, *args[2:]]
    elif form == 'reordered':
        args.remove('--network')
        args.append('--network')
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(inject), str(ROOT)]),
               CONTROLLER_IDENTITY_TEST_SPEC=str(spec_path))
    if outer and outer != 'python-text':
        # Deliberately supported double-quoted invocation: shell text is not a Python controller.
        text = '& ' + subprocess.list2cmdline(args)
        if form == 'compact-module':
            text = '& ' + ' '.join("'" + arg.replace("'", "''") + "'" for arg in args)
        args = [shell, '-NoProfile', '-NonInteractive', '-Command', text + '; exit $LASTEXITCODE']
    if outer == 'python-text':
        args = [sys.executable, '-S', '-c', 'import subprocess,sys;sys.exit(subprocess.call(sys.argv[1:]))',
                *args]
    completed = run_controller(args, env, root, 30)
    (tmp_path / 'cli.stdout.bin').write_bytes(completed.stdout)
    (tmp_path / 'cli.stderr.bin').write_bytes(completed.stderr)
    result = json.loads((tmp_path / 'query/environment-query.json').read_bytes())
    native = json.loads(result['stdout'])
    network = json.loads((tmp_path / 'network.json').read_bytes())
    assert network['external_attempts'] == [] and network['protocol_workers'] == 0
    return result, native


@pytest.mark.parametrize('shell', SHELLS)
@pytest.mark.parametrize('outer', [False, True])
@pytest.mark.parametrize('differential', [False, True])
def test_real_controller_chain(tmp_path, shell, outer, differential):
    result, native = run_probe(tmp_path, shell, outer=outer, differential=differential)
    assert result['returncode'] == 0, result
    roles = native['payload']['controller_identity']
    assert roles[0]['classification'] == 'ACTUAL_CONTROLLER'
    assert roles[-1]['classification'] == 'ORDINARY_LAUNCH_BOUNDARY_NOT_CONTROLLER'
    assert all('CommandLine' not in row for row in roles)


@pytest.mark.parametrize('shell', SHELLS)
def test_ordinary_python_launch_parent_is_not_a_controller(tmp_path, shell):
    result, native = run_probe(tmp_path, shell, outer='python-text', differential=True)
    assert result['returncode'] == 0, native
    assert native['payload']['controller_identity'][-1]['classification'] == 'ORDINARY_LAUNCH_BOUNDARY_NOT_CONTROLLER'
    assert native['payload']['controller_identity'][-1]['process_name'] == 'python.exe'


@pytest.mark.parametrize('pid', [0, -1, True, '6000'])
def test_invalid_controller_pid_rejected_before_query(tmp_path, monkeypatch, pid):
    from overnight_quant.data import minute_capture_preflight as pre
    monkeypatch.setattr(pre, 'run_probe_worker_process', lambda *a, **k: pytest.fail('no child allowed'))
    with pytest.raises(ValueError, match='controller_pid_invalid'):
        pre._query(tmp_path, '2026-10-21', tmp_path, '2026-10-21T14:30:00+08:00',
            execution_contract='single_bars_protocol_diagnostic_v1', controller_pid=pid)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('shell', SHELLS)
@pytest.mark.parametrize('form', ['relative','quoted','module','compact-module','reordered'])
def test_supported_entry_forms(tmp_path, shell, form):
    result, native = run_probe(tmp_path, shell, outer=True, differential=True, form=form)
    assert result['returncode'] == 0, native


@pytest.mark.parametrize('shell', SHELLS)
@pytest.mark.parametrize('fault,stage,item', [
    ('wrong-pid','controller','CONTROLLER_NOT_INSPECTOR_PARENT'),
    ('wrong-name','controller','PROCESS_NAME_NOT_PYTHON'),
    ('wrong-image','controller','EXECUTABLE_PATH_MISMATCH'),
    ('missing-command','controller','COMMAND_LINE_UNAVAILABLE'),
    ('wrong-entry','controller','CONTROLLER_ENTRY_MISMATCH'),
    ('wrong-parent','parent','PROCESS_UNAVAILABLE'),
    ('missing-image','controller','EXECUTABLE_PATH_UNAVAILABLE'),
    ('parent-command-missing','parent','COMMAND_LINE_UNAVAILABLE'),
    ('ancestor-mismatch','parent','UNPROVEN_PYTHON_ANCESTOR_CHAIN'),
    ('worker-parent','parent','PARENT_IS_PROJECT_WORKER'),
    ('exited','controller','PROCESS_UNAVAILABLE'),
    ('query-error','process_query','PROCESS_QUERY_FAILED'),
    ('residual-worker',None,None)])
def test_real_process_rejections_are_specific_and_redacted(tmp_path, shell, fault, stage, item):
    result, native = run_probe(tmp_path, shell, outer=True, differential=True, fault=fault)
    assert result['returncode'] == 2, native
    value = native.get('payload', native)
    assert 'NEVER_SAVE_THIS' not in result['stdout']
    if fault == 'residual-worker':
        assert any(row['Name'] == 'python.exe' for row in value['workers'])
    else:
        diagnostic = value['controller_identity'][-1]
        assert diagnostic['stage'] == stage and diagnostic['failed_item'] == item
        assert diagnostic['pid'] > 0


@pytest.mark.parametrize('shell', SHELLS)
def test_base_interpreter_ancestor_negative_model(tmp_path, shell):
    result, native = run_probe(tmp_path, shell, outer=True, differential=True,
        fault='ancestor-mismatch', runtime_shape='base-only')
    assert result['returncode'] == 2
    diagnostic = native['controller_identity'][-1]
    assert diagnostic['stage'] == 'parent' and diagnostic['failed_item'] == 'UNPROVEN_PYTHON_ANCESTOR_CHAIN'


@pytest.mark.parametrize('shell', SHELLS)
@pytest.mark.parametrize('differential', [False, True])
@pytest.mark.parametrize('fault', ['', 'missing-command'])
def test_actual_cli_live_inspection_to_test_only_READY_GO(case, monkeypatch, capsys, shell, differential, fault):
    # Existing versioned preflight and authorization fixtures; no real date/clock/source evidence.
    from overnight_quant.tests import test_phase116_protocol_execution as one
    from overnight_quant.tests import test_phase117_protocol_differential as many
    from overnight_quant.data import minute_capture_preflight as pre
    from overnight_quant.data import minute_protocol_execution as execution
    c = case
    entry = c['root'] / 'overnight_quant/scripts/run_minute_protocol_diagnostic.py'
    entry.parent.mkdir(parents=True)
    shutil.copyfile(CLI, entry)
    subprocess.run(['git','-C',str(c['root']),'add',str(entry)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(c['root']),'commit','-qm','synthetic CLI copy'],check=True,capture_output=True)
    c['head'] = subprocess.check_output(['git','-C',str(c['root']),'rev-parse','HEAD'],text=True).strip()
    for path in (c['payload'],Path(c['inputs']['before_environment']['path'])):
        material = json.loads(path.read_bytes())
        payload = material.get('payload',material)
        payload['git_head'] = c['head']
        if 'payload' in material:
            material['stdout'] = json.dumps({'ok':True,'payload':payload})
        path.write_text(json.dumps(material))
    c['inputs']['before_environment'] = pre.ref(c['inputs']['before_environment']['path'])
    if differential:
        many.prepare(c, monkeypatch, capsys)
        cli_args = many.command(c)
    else:
        # Reuse the existing fixture body without introducing a production test switch.
        c = one.protocol_case.__wrapped__(c)
        assert one.generate(c, monkeypatch, capsys)[0] == 0
        one.authorize(c)
        cli_args = one.command(c)
    inject = c['tmp'] / 'injection'; inject.mkdir()
    (inject / 'sitecustomize.py').write_text('from overnight_quant.tests.fixtures.controller_identity_fixture import install\ninstall()\n')
    temp = c['tmp'] / 'system-temp'; temp.mkdir()
    spec = dict(test_only=True, mode='capture', root=str(c['root']), head=c['head'],
        clock=c['clock']().isoformat(), shell=shell, fault=fault,
        harness=str(FIXTURES/'t31_environment_behavior_harness.ps1'), temp=str(temp),
        network_log=str(c['tmp']/'network.json'))
    spec_path = c['tmp'] / 'capture-spec.json'; spec_path.write_text(json.dumps(spec))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(inject),str(ROOT)]),
               CONTROLLER_IDENTITY_TEST_SPEC=str(spec_path))
    args = [sys.executable,str(entry),*cli_args]
    text = '& ' + subprocess.list2cmdline(args) + '; exit $LASTEXITCODE'
    completed = run_controller([shell,'-NoProfile','-NonInteractive','-Command',text],env,c['root'],90)
    (c['tmp']/'actual-cli.stdout.bin').write_bytes(completed.stdout)
    (c['tmp']/'actual-cli.stderr.bin').write_bytes(completed.stderr)
    value = json.loads(completed.stdout)
    network = json.loads((c['tmp']/'network.json').read_bytes())
    assert network['external_attempts'] == []
    if completed.returncode != (2 if fault else 0):
        diagnostic = failure_diagnostic(c['root'], value)
        destination = Path(os.environ.get('PHASE118_DIAGNOSTICS', c['tmp'] / 'failure-diagnostics'))
        destination.mkdir(parents=True, exist_ok=True)
        import uuid
        path = destination / (uuid.uuid4().hex + '.json')
        path.write_text(json.dumps(diagnostic, indent=2), encoding='utf-8')
        pytest.fail(json.dumps({'diagnostic_path': str(path), **diagnostic}, default=str))
    assert network['protocol_workers'] == (0 if fault else (4 if differential else 1))
    raw_files = list(c['root'].rglob('*evidence*v*.json'))
    actual = [json.loads(p.read_bytes()) for p in raw_files if 'result' in json.loads(p.read_bytes())]
    if fault:
        assert actual == []
    else:
        assert len(actual) == (4 if differential else 1)
        assert all(e['test_only'] and e['result']['go_sent_monotonic'] is not None for e in actual)
