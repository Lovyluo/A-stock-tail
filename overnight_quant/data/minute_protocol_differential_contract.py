"""Closed A-D diagnostic experiment identities; never a source admission profile."""
from copy import deepcopy
from datetime import datetime, time
from hashlib import sha256
from pathlib import Path
import base64
import struct

from . import minute_capture_audit as store
from . import minute_protocol_contract as single

MATRIX_VERSION = "protocol_differential_plan_v1"
MATRIX_AUTHORIZATION = "protocol_differential_authorization_v1"
MATRIX_EVIDENCE = "protocol_differential_result_v1"
MATRIX_ROOT = "overnight_quant/data/cache/minute_protocol_differential"
WORKER_VERSION = "mootdx_protocol_differential_worker_v1"
EXPERIMENTS = (
    ("A", "000001", 1, "14:47:55"),
    ("B", "000001", 800, "14:48:55"),
    ("C", "600000", 1, "14:49:55"),
    ("D", "600000", 800, "14:50:55"),
)
CONTRACT_NAMES = tuple("protocol_differential_" + row[0] + "_v1" for row in EXPERIMENTS)
HANDSHAKES = tuple(bytes.fromhex(value) for value in (
    "0c0218930001030003000d0001", "0c0218940001030003000d0002",
    "0c031899000120002000db0fd5d0c9ccd6a4a8af0000008fc22540130000d500c9ccbdf0d7ea00000002"))


def frozen_plan():
    value = {"schema": MATRIX_VERSION, "date": None, "execution_authorized": False,
        "endpoint": deepcopy(single.frozen_plan()["endpoint"]),
        "experiments": [{"id": key, "symbol": code, "count": count, "target_clock": at,
            "execution_contract": name, "independent_connection": True, "handshakes": 3}
            for (key, code, count, at), name in zip(EXPERIMENTS, CONTRACT_NAMES)],
        "frequency": "1m", "start": 0, "bars_requests_max": 4, "handshake_requests_max": 12,
        "transaction_requests": 0, "retry": False, "fallback": False,
        "preparation_lead_ms": 5000, "preparation_work_ms": 3500, "preparation_cleanup_ms": 500,
        "network_work_ms": 1500, "absolute_network_and_cleanup_ms": 2000,
        "shared_cleanup_ms": 500, "max_residence_ms": 7000, "seal_budget_ms": 3000,
        "collection_deadline": "14:51:05", "target_spacing_ms": 60000,
        "continuation": "success_or_sealed_parser_failure_only_cleanup_and_fresh_preflight_required",
        "count_one_purpose": "protocol_diagnostic_only_not_the_800_row_capture_plan",
        "qualification": "0/3", "production_activation": False}
    return {**value, "plan_hash": store.digest(value)}


def check_plan(value):
    if value != frozen_plan() or store.digest(value) != store.digest(frozen_plan()):
        raise ValueError("fixed_four_experiment_matrix_mismatch")


def profile_for_name(name):
    if name not in CONTRACT_NAMES:
        raise ValueError("unknown_differential_contract")
    return ExperimentProfile(EXPERIMENTS[CONTRACT_NAMES.index(name)][0])


def for_task(task):
    return ExperimentProfile(task.get("experiment_id"))


class ExperimentProfile:
    WORKER_VERSION = "mootdx_protocol_differential_worker_v1"
    WORKER_MODULE = "overnight_quant.data.minute_protocol_differential_worker"
    PLAN_VERSION = "mootdx_protocol_differential_experiment_plan_v1"
    AUTHORIZATION_VERSION = "mootdx_protocol_differential_experiment_authorization_v1"
    APPROVAL_STATUS = "PM_APPROVED_SINGLE_DIFFERENTIAL_EXPERIMENT"
    EVIDENCE_VERSION = "mootdx_protocol_differential_evidence_v1"
    VERIFIER_VERSION = "mootdx_protocol_differential_verifier_v1"
    AUDIT_ONLY_VERIFIER_VERSION = "differential_no_legacy_v1"
    AUDIT_ONLY_V2 = "differential_no_legacy_v2"
    PREFLIGHT_VERSION = "protocol_differential_preflight_v1"
    PREFLIGHT_VERIFIER = "protocol_differential_preflight_verifier_v1"
    ENV_SCOPE = single.ENV_SCOPE
    SEAL_RECEIPT_VERSION = "protocol_differential_seal_receipt_v1"
    SEAL_COMPLETION_VERSION = "protocol_differential_seal_completion_v1"
    EVIDENCE_NAME = "differential-evidence-v1.json"
    FILE_ANCHOR_NAME = "differential-file-anchor-v1.json"
    MAX_OUTPUT_BYTES = single.MAX_OUTPUT_BYTES

    def __init__(self, key):
        if type(key) is not str or key not in tuple(row[0] for row in EXPERIMENTS):
            raise ValueError("only_predeclared_experiments_A_to_D_allowed")
        _, code, count, self.target_clock = next(row for row in EXPERIMENTS if row[0] == key)
        self.experiment_id, self.CODES = key, [code]
        self.request = {"symbol": code, "frequency": "1m", "start": 0, "offset": count}
        self.EXECUTION_CONTRACT = "protocol_differential_" + key + "_v1"
        self.AUDIT_RELATIVE = MATRIX_ROOT + "/" + key

    def frozen_plan(self, *, audit_only_v1=False, audit_only_v2=False):
        if audit_only_v1 or audit_only_v2:
            raise ValueError("old_evidence_cannot_become_differential_evidence")
        value = {k: v for k, v in single.frozen_plan().items() if k != "plan_hash"}
        value.update(schema=self.PLAN_VERSION, worker_version=self.WORKER_VERSION,
            execution_contract=self.EXECUTION_CONTRACT, authorization_version=self.AUTHORIZATION_VERSION,
            evidence_version=self.EVIDENCE_VERSION, verifier=self.VERIFIER_VERSION,
            preflight_version=self.PREFLIGHT_VERSION, preflight_verifier=self.PREFLIGHT_VERIFIER,
            seal_receipt_version=self.SEAL_RECEIPT_VERSION, seal_completion_version=self.SEAL_COMPLETION_VERSION,
            experiment_id=self.experiment_id, matrix_plan_hash=frozen_plan()["plan_hash"],
            codes=self.CODES, request=self.request, target_clock=self.target_clock)
        return {**deepcopy(value), "plan_hash": store.digest(value)}

    def check_plan(self, value, **options):
        if value != self.frozen_plan(**options) or store.digest(value) != store.digest(self.frozen_plan(**options)):
            raise ValueError("differential_experiment_plan_mismatch")

    def worker_file_sha256(self):
        paths = [Path(__file__).with_name(name) for name in
            ("minute_protocol_differential_worker.py", "minute_protocol_worker.py")]
        return sha256(b"".join(path.read_bytes() for path in paths)).hexdigest()

    def check_task(self, task, **options):
        expected = self.frozen_plan(**options)
        if (task.get("worker_version") != self.WORKER_VERSION or task.get("codes") != self.CODES
                or task.get("plan_hash") != expected["plan_hash"]
                or task.get("experiment_id") != self.experiment_id
                or task.get("request") != self.request or type(task["request"].get("offset")) is not int
                or type(task["request"].get("start")) is not int
                or task.get("matrix_plan_hash") != expected["matrix_plan_hash"]
                or not isinstance(task.get("matrix_intent"), dict)
                or set(task["matrix_intent"]) != {"path", "sha256"}
                or not Path(task["matrix_intent"]["path"]).is_absolute()
                or type(task["matrix_intent"].get("sha256")) is not str
                or len(task["matrix_intent"]["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in task["matrix_intent"]["sha256"])
                or datetime.fromisoformat(task["scheduled_at"]).timetz().replace(tzinfo=None)
                   != time.fromisoformat(self.target_clock)):
            raise ValueError("differential_experiment_task_identity_invalid")
        # Reuse unchanged absolute deadlines, source, node and dependency checks.
        translated = {**task, "worker_version": single.WORKER_VERSION, "codes": single.CODES,
                      "plan_hash": single.frozen_plan()["plan_hash"]}
        single.check_task(translated)

    def task_context(self, root, trade_date):
        from .minute_capture_preflight import ref
        return {"matrix_intent_ref": ref(Path(root) / MATRIX_ROOT / trade_date / "matrix-intent.json")}

    def task_for(self, target, *, matrix_intent_ref, **kwargs):
        value = single.task_for(target, **kwargs)
        value.update(worker_version=self.WORKER_VERSION, worker_sha256=self.worker_file_sha256(),
            codes=list(self.CODES), request=dict(self.request), experiment_id=self.experiment_id,
            plan_hash=self.frozen_plan()["plan_hash"], matrix_plan_hash=frozen_plan()["plan_hash"],
            matrix_intent=matrix_intent_ref)
        self.check_task(value)
        return value

    def go_message(self, task, sent_monotonic):
        return {**single.go_message(task, sent_monotonic), "worker_version": self.WORKER_VERSION}

    def check_go(self, value, task, received_monotonic):
        if value.get("worker_version") != self.WORKER_VERSION:
            raise ValueError("differential_GO_version_invalid")
        single.check_go({**value, "worker_version": single.WORKER_VERSION}, task, received_monotonic)

    def check_ready(self, value, task, pid):
        if value.get("worker_version") != self.WORKER_VERSION:
            raise ValueError("differential_READY_version_invalid")
        single.check_ready({**value, "worker_version": single.WORKER_VERSION}, task, pid)

    def check_authorization_scope(self, value):
        if (value.get("experiment_id") != self.experiment_id
                or value.get("matrix_plan_hash") != frozen_plan()["plan_hash"]
                or type(value.get("matrix_authorization_id")) is not str or not value["matrix_authorization_id"]
                or value.get("target_at") != f"{value['trade_date']}T{self.target_clock}+08:00"):
            raise ValueError("differential_authorization_experiment_scope_mismatch")

    def check_parent_authorization(self, auth, root, *, controller):
        from .minute_protocol_differential import parent_authorization
        parent_authorization(auth, root, controller=controller)

    def check_parent_launch(self, task, auth, root):
        from .minute_capture_preflight import read
        intent_path = Path(root) / MATRIX_ROOT / task["trade_date"] / "matrix-intent.json"
        intent = read(task["matrix_intent"])
        if (intent["owner_pid"] != task["parent_pid"]
                or Path(task["matrix_intent"]["path"]).resolve() != intent_path.resolve()
                or intent["matrix_authorization_id"] != auth["matrix_authorization_id"]):
            raise ValueError("differential_worker_matrix_owner_mismatch")

    def blockers(self, root):
        from .minute_protocol_differential import blockers
        blockers(root)

    def expected_packet(self):
        return struct.pack("<HIHHHH6sHHHHIIH", 0x10c, 0x01016408, 0x1c, 0x1c, 0x052d,
            1 if self.CODES[0].startswith("6") else 0, self.CODES[0].encode("ascii"),
            8, 1, 0, self.request["offset"], 0, 0, 0)

    def check_trace_request(self, trace):
        expected = [*HANDSHAKES, self.expected_packet()]
        for group in trace.interactions:
            raw = base64.b64decode(group["send"]["data"]["base64"], validate=True)
            if raw != expected[group["index"]]:
                raise ValueError("differential_actual_send_bytes_do_not_match_experiment")
