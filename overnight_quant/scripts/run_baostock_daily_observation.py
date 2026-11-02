"""Offline-only vendor price-series observation. No SDK, networking or execution mode."""
import argparse
from datetime import datetime
from pathlib import Path
import sys

from overnight_quant.data.baostock_daily_observation import observe_batch
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.mootdx_node_benchmark import write_benchmark_json_atomic


def _console_line(text):
    # Pipes may use cp1252 even though the immutable result file is UTF-8.
    encoding = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    print(text.encode(encoding, errors='backslashreplace').decode(encoding))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-dir', required=True)
    parser.add_argument('--anchors', required=True)
    parser.add_argument('--anchors-sha256', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--synthetic-test-only', action='store_true')
    args = parser.parse_args(argv)
    target = Path(args.output).resolve()
    source = Path(args.batch_dir).resolve()
    if target.is_relative_to(source) or target == Path(args.anchors).resolve():
        parser.error('output must be outside original batch and anchor files')
    if target.exists():
        parser.error('output already exists; no overwrite')
    result = observe_batch(source, anchors=args.anchors, anchors_sha256=args.anchors_sha256,
                           generated_at=datetime.now(CN_TZ).isoformat(), synthetic_test_only=args.synthetic_test_only)
    try:
        write_benchmark_json_atomic(target, result)
    except OSError as exc:
        _console_line(f'OBSERVATION_WRITE_FAILED:{type(exc).__name__}:{exc}')
        return 2
    _console_line(result['disclaimer'])
    _console_line(result['status'])
    return 0 if result['observation_input_ready'] is True else 2


if __name__ == '__main__':
    raise SystemExit(main())
