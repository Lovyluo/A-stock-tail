"""Audit-only dependency scheduler; threads drain pipes, processes own requests."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import urlparse

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import (
    FIXED_CODES, MARKET_DOTNET_TRANSPORT_VERSION, MarketDotNetHttpClientTransport,
    _PRODUCTION_HEADERS, _terminate_process_tree,
)
from overnight_quant.data.point_in_time import stable_hash


CONFIG_VERSION = "t25_audit_time_configuration_v1"
EXECUTOR_VERSION = "two_process_http_call_rate_limited_audit_executor_v2"
MAX_WORKERS = 2
HOST_INTERVAL_MS = 500
REQUEST_MS = 2000
FREEZE_RESERVE_MS = 500
CLEANUP_RESERVE_MS = 1000
HOSTS = frozenset({"push2.eastmoney.com", "emweb.securities.eastmoney.com"})
LOCK = Path(tempfile.gettempdir()) / "a-stock-t25-global-executor.lock"


def safe(value: dict) -> dict:
    return {**value, "audit_only": True, "qualification": "0/3",
            "data_ready": False, "hard_gate_authorized": False,
            "provider_contract_complete": False,
            "candidates": [], "tickets": [], "orders": [],
            "sampling_authorized": False, "automatic_configuration_change": False}


@dataclass(frozen=True)
class AuditTimeConfiguration:
    trade_date: str
    version: str = CONFIG_VERSION

    def __post_init__(self):
        if date.fromisoformat(self.trade_date).isoformat() != self.trade_date or self.version != CONFIG_VERSION:
            raise ValueError("AUDIT_TIME_CONFIGURATION_INVALID")

    def at(self, clock: str) -> datetime:
        return datetime.fromisoformat(f"{self.trade_date}T{clock}+08:00")

    def material(self) -> dict:
        return {"version": self.version, "trade_date": self.trade_date,
                **{key: self.at(value).isoformat() for key, value in {
                    "start_at": "14:37:00", "latest_start_at": "14:39:30",
                    "feature_cutoff": "14:40:00", "collection_deadline": "14:41:05",
                    "decision_time": "14:41:10", "execution_start": "14:42:00",
                }.items()}}


@dataclass(frozen=True)
class AuditRequest:
    request_id: str
    url: str
    params: dict
    dependencies: tuple[str, ...] = ()
    release_at: datetime | None = None

    @property
    def host(self) -> str:
        return str(urlparse(self.url).hostname)

    def material(self) -> dict:
        return {"request_id": self.request_id, "url": self.url,
                "params": dict(sorted(self.params.items())),
                "dependencies": sorted(self.dependencies),
                "release_at": self.release_at.isoformat() if self.release_at else None}


def validate_plan(jobs: list[AuditRequest]) -> list[AuditRequest]:
    ordered = sorted(jobs, key=lambda job: job.request_id)
    ids = [job.request_id for job in ordered]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("AUDIT_REQUEST_PLAN_DUPLICATE_OR_EMPTY")
    for job in ordered:
        parsed = urlparse(job.url)
        if (not job.request_id or not job.request_id.isascii() or job.request_id.strip() != job.request_id
                or parsed.scheme != "https" or job.host not in HOSTS or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or type(job.params) is not dict or any(type(k) is not str or type(v) is not str for k, v in job.params.items())
                or len(set(job.dependencies)) != len(job.dependencies)
                or set(job.dependencies) - set(ids) or job.request_id in job.dependencies):
            raise ValueError("AUDIT_REQUEST_PLAN_INVALID")
        if job.release_at and (job.release_at.tzinfo is None or job.release_at.utcoffset() != timedelta(hours=8)):
            raise ValueError("AUDIT_RELEASE_TIME_INVALID")
    done = set()
    while len(done) != len(ids):
        ready = [j.request_id for j in ordered if j.request_id not in done and set(j.dependencies) <= done]
        if not ready:
            raise ValueError("AUDIT_REQUEST_DEPENDENCY_CYCLE")
        done.update(ready)
    return ordered


def integration_plan(configuration: AuditTimeConfiguration, *, industry_pages: int = 1,
                     publication_delay_ms: int = 0) -> list[AuditRequest]:
    """Research plan, not a native parser or production qualification request."""
    if type(industry_pages) is not int or industry_pages not in (1, 2):
        raise ValueError("AUDIT_PAGINATION_PLAN_UNSUPPORTED")
    if type(publication_delay_ms) is not int or publication_delay_ms < 0:
        raise ValueError("AUDIT_PUBLICATION_DELAY_INVALID")
    from overnight_quant.data.eastmoney_derived_industry_breadth import CORE_CONCEPTION_URL, EXPECTED_INDUSTRIES
    from overnight_quant.data.market_source_providers import (
        EASTMONEY_CLIST_URL, EASTMONEY_ULIST_URL, EASTMONEY_STOCK_URL,
        EASTMONEY_FUND_FLOW_URL, MARKET_BREADTH_PARAMS, MARKET_INDEX_PARAMS,
    )
    mappings = tuple(f"10-mapping-{code}" for code in FIXED_CODES)
    jobs = [AuditRequest("00-market-counts", EASTMONEY_ULIST_URL, MARKET_BREADTH_PARAMS),
            AuditRequest("01-market-index", EASTMONEY_STOCK_URL, MARKET_INDEX_PARAMS)]
    for code, key in zip(FIXED_CODES, mappings):
        jobs.append(AuditRequest(key, CORE_CONCEPTION_URL, {"code": ("SH" if code.startswith("6") else "SZ") + code}))
    boards = sorted({value[0] for value in EXPECTED_INDUSTRIES.values()})
    for board in boards:
        # Page two is a reserved dependency only for boards whose total requires it.
        pages = industry_pages if board != "BK1239" else 1
        for page in range(1, pages + 1):
            deps = mappings if page == 1 else (f"20-constituents-{board}-{page - 1}",)
            jobs.append(AuditRequest(f"20-constituents-{board}-{page}", EASTMONEY_CLIST_URL,
                {"pn": str(page), "pz": "100", "fs": f"b:{board}", "fltt": "2", "invt": "2",
                 "fields": "f2,f3,f12,f14,f124"}, deps))
        jobs.append(AuditRequest(f"30-native-board-{board}", EASTMONEY_STOCK_URL,
            {"secid": f"90.{board}", "fltt": "2", "fields": "f57,f58,f86,f170"}, mappings))
    for code in FIXED_CODES:
        jobs.append(AuditRequest(f"40-fund-{code}", EASTMONEY_FUND_FLOW_URL,
            {"secid": ("1." if code.startswith("6") else "0.") + code, "klt": "1",
             "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56,f57"},
            release_at=configuration.at("14:40:00") + timedelta(milliseconds=publication_delay_ms)))
    return validate_plan(jobs)


def schedule_budget(jobs: list[AuditRequest], *, started_at: datetime, deadline: datetime) -> dict:
    """Simulate the same DAG/host limits, not request_count / workers."""
    pending = validate_plan(jobs)
    completed, running, last_host, trace = set(), [], {}, []
    cursor = started_at
    while pending or running:
        for end, job in list(running):
            if end <= cursor:
                completed.add(job.request_id)
                running.remove((end, job))
        launched = False
        for job in list(pending):
            if len(running) >= MAX_WORKERS:
                break
            if (not set(job.dependencies) <= completed or (job.release_at and cursor < job.release_at)
                    or cursor < last_host.get(job.host, started_at - timedelta(seconds=1)) + timedelta(milliseconds=HOST_INTERVAL_MS)):
                continue
            end = cursor + timedelta(milliseconds=REQUEST_MS)
            trace.append({"request_id": job.request_id, "host": job.host,
                          "started_at": cursor.isoformat(), "completed_at": end.isoformat(),
                          "queue_elapsed_ms": (cursor - started_at).total_seconds() * 1000})
            last_host[job.host] = cursor
            running.append((end, job))
            pending.remove(job)
            launched = True
        if not pending and not running:
            break
        events = [end for end, _ in running if end > cursor]
        for job in pending:
            if set(job.dependencies) <= completed:
                events.extend(t for t in (job.release_at, last_host.get(job.host, cursor) + timedelta(milliseconds=HOST_INTERVAL_MS)) if t and t > cursor)
        if events:
            cursor = min(events)
        elif not launched:
            raise ValueError("AUDIT_SCHEDULE_DEADLOCK")
    freeze = cursor + timedelta(milliseconds=FREEZE_RESERVE_MS + CLEANUP_RESERVE_MS)
    return safe({"status": "AUDIT_BUDGET_FEASIBLE" if freeze <= deadline else "AUDIT_BATCH_BUDGET_INSUFFICIENT",
                 "execution_ok": freeze <= deadline, "request_count": len(jobs), "trace": trace,
                 "worst_completion_at": freeze.isoformat(),
                 "remaining_margin_ms": (deadline - freeze).total_seconds() * 1000,
                 "max_workers": 2, "host_start_interval_ms": 500, "request_deadline_ms": 2000,
                 "cleanup_reserve_ms": CLEANUP_RESERVE_MS, "freeze_reserve_ms": FREEZE_RESERVE_MS})


class BoundedAuditExecutor:
    def run(self, jobs: list[AuditRequest], *, configuration: AuditTimeConfiguration, network: bool = False) -> dict:
        if network is not True:
            return safe({"status": "EXPLICIT_NETWORK_REQUIRED", "execution_ok": False, "results": []})
        now = datetime.now(CN_TZ)
        if not configuration.at("14:37:00") <= now <= configuration.at("14:39:30"):
            return safe({"status": "AUDIT_WINDOW_MISSED", "execution_ok": False, "results": [], "dispatched_count": 0})
        return self._run(jobs, deadline=configuration.at("14:41:05"), command=MarketDotNetHttpClientTransport().worker_command,
                         test_payloads=None, lock_path=LOCK)

    def _run_for_test(self, jobs: list[AuditRequest], *, deadline: datetime,
                      command: list[str], payloads: dict, lock_path: Path) -> dict:
        return self._run(jobs, deadline=deadline, command=command, test_payloads=payloads, lock_path=lock_path)

    def _run(self, jobs, *, deadline, command, test_payloads, lock_path):
        pending = validate_plan(jobs)
        if deadline.tzinfo is None or deadline.utcoffset() != timedelta(hours=8):
            raise ValueError("AUDIT_DEADLINE_INVALID")
        batch_wall, batch_start = datetime.now(CN_TZ), time.perf_counter()
        limit = batch_start + (deadline - batch_wall).total_seconds()
        release_limits = {job.request_id: batch_start + (job.release_at - batch_wall).total_seconds()
                          for job in pending if job.release_at}
        if limit - batch_start < (REQUEST_MS + FREEZE_RESERVE_MS + CLEANUP_RESERVE_MS) / 1000:
            return safe({"status": "AUDIT_BATCH_BUDGET_INSUFFICIENT", "execution_ok": False, "results": [], "dispatched_count": 0})
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return safe({"status": "AUDIT_EXECUTOR_ALREADY_RUNNING", "execution_ok": False, "results": [], "dispatched_count": 0})
        running, results, completed, last_host = {}, {}, set(), {}
        failure, peak = "", 0
        pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        try:
            while pending or running:
                now = time.perf_counter()
                if now >= limit - FREEZE_RESERVE_MS / 1000:
                    failure = failure or "AUDIT_BATCH_DEADLINE_EXCEEDED"
                for key, entry in list(running.items()):
                    process, future, started, row, job = entry
                    if not future.done() and now - started >= REQUEST_MS / 1000:
                        failure = failure or "REQUEST_DEADLINE_EXCEEDED"
                        row.update(error_code="REQUEST_DEADLINE_EXCEEDED", worker_terminated=True, request_timed_out=True)
                        _terminate_process_tree(process)
                    if future.done():
                        try:
                            stdout, _ = future.result()
                            response = json.loads(stdout.lstrip("\ufeff"))
                            elapsed = (time.perf_counter() - started) * 1000
                            if elapsed > REQUEST_MS:
                                row.update(error_code="REQUEST_DEADLINE_EXCEEDED", request_timed_out=True, worker_terminated=row.get("worker_terminated", False))
                            elif not isinstance(response, dict):
                                row["error_code"] = "WORKER_OUTPUT_INVALID"
                            elif response.get("ok") is not True:
                                row["error_code"] = str(response.get("error_code") or "WORKER_FAILED")
                                row["failure_response"] = response
                            else:
                                row["captured_worker_response"] = response
                                if process.returncode != 0:
                                    raise ValueError("AUDIT_WORKER_EXIT_INVALID")
                                self._validate_response(response, job)
                                http_started = datetime.fromisoformat(response["response"]["request_started_at"])
                                http_completed = datetime.fromisoformat(response["response"]["request_completed_at"])
                                if not datetime.fromisoformat(row["request_started_at"]) <= http_started <= http_completed <= datetime.now(CN_TZ):
                                    raise ValueError("AUDIT_RESPONSE_TIME_INVALID")
                                row.update(ok=True, response=response)
                        except (ValueError, TypeError, OSError) as exc:
                            row["error_code"] = str(exc) if str(exc).startswith("AUDIT_") else "WORKER_OUTPUT_INVALID"
                        row.update(request_completed_at=datetime.now(CN_TZ).isoformat(timespec="microseconds"),
                                   request_elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                                   worker_exit_code=process.returncode, worker_reaped=process.poll() is not None)
                        results[key] = row
                        if row.get("ok") is True and not row.get("error_code"):
                            completed.add(key)
                        else:
                            row["ok"] = False
                            failure = failure or row["error_code"]
                        del running[key]
                if failure:
                    for key, (process, future, started, row, job) in list(running.items()):
                        _terminate_process_tree(process)
                        try:
                            future.result()
                        except (ValueError, OSError):
                            pass
                        results[key] = {**row, "ok": False, "error_code": row.get("error_code") or "AUDIT_BATCH_CANCELLED",
                            "worker_terminated": True, "worker_reaped": process.poll() is not None,
                            "request_completed_at": datetime.now(CN_TZ).isoformat(timespec="microseconds"),
                            "request_elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}
                        del running[key]
                    break
                now = time.perf_counter()
                for job in list(pending):
                    if len(running) >= MAX_WORKERS:
                        break
                    if not set(job.dependencies) <= completed:
                        continue
                    if job.release_at and now < release_limits[job.request_id]:
                        continue
                    if now - last_host.get(job.host, float("-inf")) < HOST_INTERVAL_MS / 1000:
                        continue
                    if limit - now < (REQUEST_MS + FREEZE_RESERVE_MS + CLEANUP_RESERVE_MS) / 1000:
                        failure = "AUDIT_BATCH_BUDGET_INSUFFICIENT"
                        break
                    row = {"request_id": job.request_id, "host": job.host, "ok": False,
                           "error_code": "", "worker_terminated": False,
                           "request_timed_out": False, "request_deadline_ms": REQUEST_MS,
                           "url": job.url, "params": sorted(job.params.items()),
                           "transport_version": MARKET_DOTNET_TRANSPORT_VERSION,
                           "headers_hash": stable_hash(_PRODUCTION_HEADERS),
                           "request_hash": stable_hash({"method": "GET", "url": job.url, "params": sorted(job.params.items())}),
                           "plan_item_hash": stable_hash(job.material()),
                           "request_started_offset_ms": round((now - batch_start) * 1000, 3),
                           "queue_elapsed_ms": round((now - batch_start) * 1000, 3),
                           "request_started_at": datetime.now(CN_TZ).isoformat(timespec="microseconds")}
                    payload = {"method": "GET", "url": job.url, "params": sorted(job.params.items()),
                               "headers": dict(_PRODUCTION_HEADERS),
                               "request_deadline_ms": REQUEST_MS, "max_response_bytes": 5_000_000,
                               "allowed_hosts": sorted(HOSTS), "transport_version": MARKET_DOTNET_TRANSPORT_VERSION}
                    if test_payloads is not None:
                        payload["test_only"] = True
                        payload["test_response"] = test_payloads[job.request_id]
                    started = time.perf_counter()
                    payload["deadline_monotonic_seconds"] = min(started + REQUEST_MS / 1000, limit - FREEZE_RESERVE_MS / 1000)
                    if test_payloads is not None:
                        payload["http_start_state_directory"] = str(lock_path.parent / "http-start-state")
                    # Admission is the request start: startup/pipe reading consume its 2s.
                    last_host[job.host] = started
                    try:
                        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="strict",
                            cwd=Path(__file__).resolve().parents[2],
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    except OSError:
                        results[job.request_id] = {**row, "error_code": "WORKER_START_FAILED", "worker_reaped": True}
                        failure = "WORKER_START_FAILED"
                        pending.remove(job)
                        break
                    future = pool.submit(process.communicate, json.dumps(payload, ensure_ascii=False))
                    row["worker_pid"] = process.pid
                    running[job.request_id] = (process, future, started, row, job)
                    pending.remove(job)
                    peak = max(peak, len(running))
                    now = time.perf_counter()
                time.sleep(0.001)
        finally:
            for process, future, _, _, _ in running.values():
                _terminate_process_tree(process)
                try:
                    future.result()
                except (ValueError, OSError):
                    pass
            pool.shutdown(wait=True)
            os.close(fd)
            lock_path.unlink()
        return safe({"status": failure or "AUDIT_EXECUTOR_COMPLETE", "execution_ok": not failure,
            "executor_version": EXECUTOR_VERSION, "test_only": test_payloads is not None,
            "results": [results[key] for key in sorted(results)],
            "unstarted_request_ids": sorted(job.request_id for job in pending),
            "dispatched_count": len(results), "max_in_flight": peak,
            "retry_count": 0, "fallback_count": 0, "residual_worker_count": 0,
            "batch_elapsed_ms": round((time.perf_counter() - batch_start) * 1000, 3),
            "network_requests_made": 0 if test_payloads is not None else None,
            "upstream_network_activity": "none" if test_payloads is not None else "see_leaf_responses"})

    @staticmethod
    def _validate_response(value, job):
        if value.get("transport_version") != MARKET_DOTNET_TRANSPORT_VERSION:
            raise ValueError("AUDIT_TRANSPORT_VERSION_INVALID")
        response = value.get("response") or {}
        url = urlparse(str(response.get("url") or ""))
        if (url.scheme != "https" or url.hostname != job.host or url.port not in (None, 443)
                or response.get("redirect_chain") != []):
            raise ValueError("AUDIT_RESPONSE_URL_INVALID")
        if type(response.get("status_code")) is not int or response["status_code"] != 200:
            raise ValueError(f"AUDIT_HTTP_{response.get('status_code')}")
        raw = base64.b64decode(response.get("content_base64", ""), validate=True)
        if not 0 < len(raw) <= 5_000_000 or response.get("byte_count") != len(raw):
            raise ValueError("AUDIT_RESPONSE_SIZE_INVALID")
        if hashlib.sha256(raw).hexdigest() != response.get("raw_hash"):
            raise ValueError("AUDIT_RAW_HASH_INVALID")
        started = datetime.fromisoformat(response.get("request_started_at", ""))
        completed = datetime.fromisoformat(response.get("request_completed_at", ""))
        if (started.utcoffset() != timedelta(hours=8) or completed.utcoffset() != timedelta(hours=8)
                or completed < started or started.date() != completed.date()):
            raise ValueError("AUDIT_RESPONSE_TIME_INVALID")
