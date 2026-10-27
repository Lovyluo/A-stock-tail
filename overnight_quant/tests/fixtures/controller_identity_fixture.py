"""Isolated startup injection for real CLI processes; never an operational entry."""
import atexit
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import sys
import time
import subprocess


def install():
    if '-c' in sys.orig_argv or not any('run_minute_protocol_diagnostic' in arg for arg in sys.orig_argv):
        return  # Fixture workers use their existing independent test-only isolation.
    spec = json.loads(Path(os.environ['CONTROLLER_IDENTITY_TEST_SPEC']).read_bytes())
    if spec.get('test_only') is not True:
        raise RuntimeError('synthetic controller fixture required')
    attempts = []
    state = {'protocol_workers': 0}

    def denied(*args, **kwargs):
        attempts.append('external_network_attempt')
        raise AssertionError('external network and NTP forbidden in controller fixture')

    socket.socket.connect = denied
    socket.create_connection = denied
    socket.getaddrinfo = denied
    atexit.register(lambda: Path(spec['network_log']).write_text(json.dumps({
        'test_only': True, 'external_attempts': attempts, **state}), encoding='utf-8'))
    from overnight_quant.data import minute_capture_preflight as pre
    from overnight_quant.data import minute_protocol_execution as single
    from overnight_quant.data import minute_protocol_differential as matrix
    from overnight_quant.data import minute_capture_audit as store
    from overnight_quant.tests.test_phase107_minute_capture_audit import Clock
    root = Path(spec['root'])
    clock = Clock('14:48:00')
    clock.base = datetime.fromisoformat(spec.get('clock', '2026-10-21T14:48:00+08:00'))
    clock.start = time.monotonic()
    original_query = pre._query

    def query(target, day, project, prepared_at, *, test=None, execution_contract='single_bars_protocol_diagnostic_v1', controller_pid=None):
        profile, _, _ = pre.execution_profile(execution_contract)
        selected_pid = os.getpid()
        if spec.get('fault') == 'wrong-pid':
            selected_pid = os.getppid()
        if spec.get('fault') == 'exited':
            with subprocess.Popen([sys.executable, '-S', '-c', 'pass']) as child:
                selected_pid = child.pid
                child.wait(timeout=5)
        command = [spec['shell'], '-NoProfile', '-NonInteractive', '-File', spec['harness'],
            '-Script', str(pre.ENV_SCRIPT), '-OpsDirectory', str(target),
            '-OutputPath', str(target / 'environment-native.json'),
            '-TempDirectory', spec['temp'], '-Mode', 'real-processes', '-MinuteCapture',
            '-ProtocolDiagnostic', '-ProjectRoot', str(project),
            '-MinuteCaptureRoot', str(project / single.SHARED_RELATIVE),
            '-ProtocolDiagnosticRoot', str(project / getattr(profile, 'AUDIT_RELATIVE', single.AUDIT_RELATIVE)),
            '-TradeDate', day, '-AllowedControllerPid', str(selected_pid),
            '-ControllerPythonPath', sys._base_executable if spec.get('runtime_shape') == 'base-only' else sys.executable,
            '-ControllerBasePythonPath', sys._base_executable,
            '-ControllerWorkingDirectory', os.getcwd()]
        if spec.get('fault'):
            command += ['-ProcessFault', spec['fault']]
        command += ['-SyntheticHead', spec.get('head', 'a' * 40),
            '-SyntheticCheckedAt', clock().isoformat(), '-SyntheticBootTime', day+'T04:00:00+08:00']
        residual = None
        if spec.get('fault') == 'residual-worker':
            path = Path(spec['temp']) / 'minute_protocol_worker.py'
            path.write_text('import time\ntime.sleep(20)\n', encoding='utf-8')
            residual = subprocess.Popen([sys._base_executable, '-S', str(path)])
        try:
            reference = original_query(target, day, project, prepared_at,
                test={'query_command': command, 'clock': clock})
        finally:
            if residual is not None:
                residual.kill()
                residual.wait(timeout=3)
        if spec.get('mode') == 'capture':
            result = pre.read(reference)
            if result['ok']:
                # Only CI host timezone is synthetic; original live process-role output remains immutable.
                result['payload'].update(timezone='China Standard Time', test_only=True)
                result['stdout'] = json.dumps({'ok': True, 'payload': result['payload']})
                from hashlib import sha256
                result['stdout_sha256'] = sha256(result['stdout'].encode()).hexdigest()
                result['synthetic_timezone_only'] = True
                result['original_live_query'] = reference
                return pre.ref(store.write(target / 'test-only-environment-query.json', result))
        return reference

    if spec.get('mode') == 'capture':
        store.ROOT = pre.ROOT = root
        pre._query = query
        context = {'root': root, 'clock': clock, 'sleep': clock.sleep, 'test_only': True,
            'worker_command': [sys.executable, str(Path(__file__).with_name('minute_protocol_fixture.py')), 'complete']}
        if '--differential' in sys.orig_argv:
            context['worker_command'].append('differential')
        original_single, original_matrix, original_dispatch = single.capture, matrix.capture, single.run_prepared_wave
        def capture_one(*args, **kwargs):
            kwargs.setdefault('_test', context)
            return original_single(*args, **kwargs)
        single.capture = capture_one
        matrix.capture = lambda *a, **kw: original_matrix(*a, **kw, _test=context)
        def dispatch(*args, **kwargs):
            state['protocol_workers'] += 1
            return original_dispatch(*args, **kwargs)
        single.run_prepared_wave = dispatch
        return

    def probe(*args, **kwargs):
        target = Path(spec['output'])
        target.mkdir()
        query(target, '2026-10-21', root, '2026-10-21T14:30:00+08:00')
        raise ValueError('TEST_ONLY_PROBE_NO_CAPTURE_DISPATCH')

    single.capture = probe
    matrix.capture = probe
