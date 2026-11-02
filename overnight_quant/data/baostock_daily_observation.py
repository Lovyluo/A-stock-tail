"""Read-only vendor-price observation; never a live Provider or Tencent record."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import math
from pathlib import Path
import re

from .completed_daily_observation import FORMULA, STRATEGY, _time, compute_r20, read_anchored_json
from .point_in_time import stable_hash
from .s2_integration_contract import FIXED_CODES, security_identity


SDK_CODES = tuple(('sh.' if code.startswith('6') else 'sz.') + code for code in FIXED_CODES)
FIELDS = ['date', 'code', 'open', 'high', 'low', 'close', 'adjustflag', 'tradestatus']
DISCLAIMER = ('仅比较供应商返回价格序列；停牌与填充语义未证明，'
              '不代表期间可成交，也不代表未来上涨概率。')
LIMITATIONS = {
    'risk_assessment': 'NOT_ASSESSED', 'trading_status_semantics': 'UNKNOWN',
    'suspension_fill_semantics': 'UNKNOWN', 'price_interpretation': 'VENDOR_REPORTED_QFQ_SERIES',
    'status_original_trade_day_semantics': 'UNKNOWN', 'disclaimer': DISCLAIMER,
}
PROFILE = {
    'profile_id': 'baostock_094_sdk_qfq_price_observation_v1', 'version': 1,
    'source': 'BaoStock', 'sdk_distribution_version': '0.9.4', 'sdk_layer': 'SDK_RETURNED_UNMODIFIED',
    'adjustflag': '2', 'frequency': 'd', 'codes': list(SDK_CODES),
    'fields': FIELDS, 'calendar_days': 120, 'required_sessions': 60,
    'original_batch_contract': 'baostock_five_stock_diagnostic_v1',
    'source_qualification_granted': False, 'production_binding': False,
}
PROFILE_HASH = stable_hash(PROFILE)
CONTRACT_HASH = stable_hash({'strategy': STRATEGY, 'formula': FORMULA, 'profile': PROFILE,
                             'limitations': LIMITATIONS, 'duplicate_policy': 'sdk_dates_must_be_unique'})


def _finite(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('finite_monotonic_number_required')
    return value


def _date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('canonical_date_required')
    return date.fromisoformat(value)


def _load_batch(directory, anchor_path, anchor_sha, generated_at, synthetic_test_only):
    directory = Path(directory).resolve()
    catalog = read_anchored_json(anchor_path, anchor_sha)
    batch = catalog.get('batch_id')
    if not isinstance(batch, str) or not batch.strip():
        raise ValueError('batch_identity_missing')
    files = catalog.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('external_file_catalog_missing')
    for name, digest in files.items():
        if (not isinstance(name, str) or '/' in name or '\\' in name or name in ('.', '..')
                or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
            raise ValueError('catalog_path_or_hash_invalid')
        path = (directory / name).resolve()
        if path.parent != directory or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('original_file_sha256_mismatch:' + name)
    if 'cleanup-incomplete.json' in files or (directory / 'cleanup-incomplete.json').exists():
        raise ValueError('capture_cleanup_unconfirmed')

    def load(name):
        if name not in files:
            raise ValueError('required_anchored_file_missing:' + name)
        return read_anchored_json(directory / name, files[name])

    intent = load('probe-intent.json')
    if intent.get('batch_id') != batch or intent.get('sdk_version') != '0.9.4':
        raise ValueError('batch_or_sdk_version_mismatch')
    if intent.get('codes') != list(SDK_CODES):
        raise ValueError('fixed_five_sdk_identities_mismatch')
    marker = intent.get('synthetic_test_only', False)
    if type(marker) is not bool or marker is not synthetic_test_only:
        raise ValueError('synthetic_test_only_isolation')
    run_date = intent['run_date']
    r = _date(run_date)
    if intent['start'] != (r - timedelta(days=120)).isoformat() or intent['end'] != (r - timedelta(days=1)).isoformat():
        raise ValueError('original_run_date_range_mismatch')
    generated, captured = _time(generated_at), _time(intent['started_at'])
    finished = load('batch-finished.json')
    finished_at = _time(finished['at'])
    if captured.date() != r or not captured <= finished_at <= generated or finished_at.date() != r:
        raise ValueError('capture_or_generation_time_invalid')
    origin = _finite(intent['started_monotonic'])
    work, hard = _finite(intent['work_deadline']), _finite(intent['hard_deadline'])
    frozen = _finite(finished['monotonic'])
    if not origin < work < hard or not origin <= frozen <= hard:
        raise ValueError('capture_absolute_time_order_invalid')
    process = load('process-receipt.json')
    if (process.get('ok') is not True or process.get('cleanup_complete') is not True
            or type(process.get('returncode')) is not int or process['returncode'] != 0):
        raise ValueError('capture_execution_or_cleanup_failed')
    summary = load('worker-summary.json')
    stages = ['login', 'calendar', *SDK_CODES, 'logout']
    if (summary.get('batch_id') != batch or summary.get('ok') is not True
            or summary.get('logical_calls') != {stage: 1 for stage in stages}
            or any(type(value) is not int for value in summary['logical_calls'].values())):
        raise ValueError('capture_batch_or_request_set_mismatch')
    messages = summary.get('sdk_message_calls')
    if not isinstance(messages, list) or [m['stage'] for m in messages] != stages:
        raise ValueError('capture_request_order_mismatch')
    if [m['message_type'] for m in messages] != ['00', '33', *(['95'] * 5), '02']:
        raise ValueError('capture_message_types_mismatch')
    captures = {}
    previous = captured
    for stage in stages:
        raw = load(stage + '-sdk.json')
        started = load(stage + '-started.json')
        if (raw.get('batch_id') != batch or started.get('batch_id') != batch
                or raw.get('representation') != PROFILE['sdk_layer'] or raw.get('error_code') != '0'):
            raise ValueError('sdk_batch_layer_or_error_mismatch:' + stage)
        marker = raw.get('synthetic_test_only', False)
        if type(marker) is not bool or marker is not synthetic_test_only:
            raise ValueError('synthetic_test_only_isolation')
        begin, end = _time(started['at']), _time(raw['response_completed_at'])
        if not previous <= begin <= end <= finished_at or begin.date() != r or end.date() != r:
            raise ValueError('request_wall_time_order_invalid:' + stage)
        if not origin <= _finite(started['monotonic']) <= _finite(raw['completed_monotonic']) <= work:
            raise ValueError('request_monotonic_time_order_invalid:' + stage)
        previous = end
        expected = ({'start_date': intent['start'], 'end_date': intent['end']} if stage == 'calendar' else
                    {'code': stage, 'fields': ','.join(FIELDS), 'start_date': intent['start'],
                     'end_date': intent['end'], 'frequency': 'd', 'adjustflag': '2'} if stage in SDK_CODES else {})
        if raw['parameters'] != expected or started['parameters'] != expected:
            raise ValueError('sdk_request_parameters_mismatch:' + stage)
        if stage not in ('login', 'logout'):
            iteration = load(stage + '-iteration.json')
            if (type(iteration.get('send_msg_delta')) is not int or iteration['send_msg_delta'] != 0
                    or type(iteration.get('rows_read')) is not int or iteration['rows_read'] != len(raw['rows'])):
                raise ValueError('sdk_iteration_or_pagination_mismatch:' + stage)
        captures[stage] = raw
    return intent, captures, files, batch, finished_at.isoformat()


def observe_batch(directory, *, anchors, anchors_sha256, generated_at, synthetic_test_only=False):
    result = {
        'result_schema_version': 'baostock_vendor_price_observation_v1', 'strategy_id': STRATEGY,
        'formula_version': FORMULA, 'source_profile': deepcopy(PROFILE), 'profile_hash': PROFILE_HASH,
        'contract_hash': CONTRACT_HASH, **LIMITATIONS, 'status': 'OBSERVATION_REJECTED',
        'observation_input_ready': False, 'observation_ranking': [], 'errors': [],
        'qualification': '0/3', 'data_ready': False, 'hard_gate_authorized': False,
        'records': [], 'candidates': [], 'tickets': [], 'orders': [],
        'historical_offline': True, 'synthetic_test_only': synthetic_test_only,
        'audit': {'generated_at': generated_at, 'anchors_file_sha256': anchors_sha256,
                  'input_layer': 'SDK_RETURNED_UNMODIFIED', 'frozen_manifest_created': False},
    }
    try:
        intent, captures, files, batch, complete = _load_batch(
            directory, anchors, anchors_sha256, generated_at, synthetic_test_only)
        r = _date(intent['run_date'])
        result.update(run_date=r.isoformat(), batch_id=batch)
        calendar = captures['calendar']
        if calendar['fields'] != ['calendar_date', 'is_trading_day']:
            raise ValueError('calendar_fields_mismatch')
        days = {}
        for row in calendar['rows']:
            if not isinstance(row, list) or len(row) != 2 or row[1] not in ('0', '1'):
                raise ValueError('calendar_row_invalid')
            d = _date(row[0])
            if d in days:
                raise ValueError('duplicate_calendar_date')
            days[d] = row[1]
        start = _date(intent['start'])
        if set(days) != {start + timedelta(days=i) for i in range(120)}:
            raise ValueError('calendar_120_day_coverage_missing')
        trading = sorted(d for d, flag in days.items() if flag == '1')
        required = trading[-60:]
        if len(required) != 60 or required[-1] >= r:
            raise ValueError('completed_60_day_calendar_missing')
        ranking, decision_inputs, audit_stocks = [], [], []
        for code, sdk_code in zip(FIXED_CODES, SDK_CODES):
            raw = captures[sdk_code]
            if raw['fields'] != FIELDS or raw['sdk_attributes'].get('code') != sdk_code:
                raise ValueError('daily_fields_or_sdk_identity_mismatch:' + sdk_code)
            rows = {}
            for row in raw['rows']:
                if not isinstance(row, list) or len(row) != len(FIELDS) or not all(isinstance(v, str) for v in row):
                    raise ValueError('sdk_string_row_invalid:' + sdk_code)
                d = _date(row[0])
                if row[1] != sdk_code or row[6] != '2' or d not in trading:
                    raise ValueError('row_identity_date_or_adjustment_mismatch:' + sdk_code)
                if d in rows:
                    raise ValueError('duplicate_sdk_date:' + sdk_code)
                prices = [Decimal(v) for v in row[2:6]]
                if any(not p.is_finite() or p <= 0 for p in prices):
                    raise ValueError('positive_finite_ohlc_required:' + sdk_code)
                o, h, low, c = prices
                if not low <= min(o, c) <= max(o, c) <= h:
                    raise ValueError('ohlc_range_invalid:' + sdk_code)
                rows[d] = row
            if set(rows) != set(trading):
                raise ValueError('calendar_date_coverage_missing:' + sdk_code)
            selected = [rows[d] for d in required]
            closes = [row[5] for row in selected]
            r20 = compute_r20(closes)
            identity = security_identity(code)
            source_file = sdk_code + '-sdk.json'
            refs = {'batch_id': batch, 'sdk_file': source_file, 'sdk_file_sha256': files[source_file],
                    'request_file_sha256': files[sdk_code + '-started.json'],
                    'calendar_file_sha256': files['calendar-sdk.json'],
                    'response_completed_at': raw['response_completed_at'], 'source_profile': PROFILE['profile_id']}
            ranking.append({'security_identity': identity, 'code': code, 'sdk_security_code': sdk_code,
                            'start_date': required[-21].isoformat(), 'end_date': required[-1].isoformat(),
                            'start_close': closes[-21], 'end_close': closes[-1], 'R20': r20,
                            'source_profile': PROFILE['profile_id'], 'formula_version': FORMULA,
                            'evidence_references': refs, 'limitations': dict(LIMITATIONS)})
            # Every native state string is retained, not mapped, filtered or upgraded.
            decision_inputs.append({'identity': identity, 'native_selected_rows': selected})
            audit_stocks.append({'security_identity': identity, 'records_received': len(rows),
                                 'native_fields': FIELDS, 'native_rows': [rows[d] for d in sorted(rows)],
                                 'tradestatus_distribution': dict(Counter(row[7] for row in raw['rows'])),
                                 'sdk_metadata_adjustflag': raw['sdk_attributes'].get('adjustflag')})
        ranking.sort(key=lambda item: (-item['R20'], item['security_identity']))
        for rank, item in enumerate(ranking, 1):
            item['rank'] = rank
        result.update(status='VENDOR_PRICE_OBSERVATION_COMPUTED', observation_input_ready=True,
                      observation_ranking=ranking, completed_date=required[-1].isoformat())
        result['decision_hash'] = stable_hash({'contract_hash': CONTRACT_HASH, 'profile_hash': PROFILE_HASH,
                                               'formula_version': FORMULA, 'run_date': r.isoformat(),
                                               'limitations': LIMITATIONS, 'inputs': decision_inputs})
        result['audit'].update(capture_completed_at=complete, source_files=files, stocks=audit_stocks)
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, InvalidOperation,
            OverflowError, ZeroDivisionError) as exc:
        result.update(status='OBSERVATION_REJECTED', observation_input_ready=False, observation_ranking=[],
                      errors=[f'{type(exc).__name__}:{exc}'])
        result['decision_hash'] = stable_hash({'contract_hash': CONTRACT_HASH, 'rejected': result['errors']})
    return result
