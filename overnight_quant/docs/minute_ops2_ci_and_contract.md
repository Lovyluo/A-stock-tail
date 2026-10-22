# MINUTE-OPS-2: CI Findings and Local Preparation Proposal

## CI Decision

Baseline: `06aac142f27b979f33294755628a7b4495456ea0`, existing Draft PR #42.
[Original CI 39742818478](https://github.com/Lovyluo/A-stock-tail/actions/runs/39742818478):
1687 passed, two Phase101 failures. Exact historical root cause remains
**ROOT_CAUSE_UNCONFIRMED**; a later green run does not change that conclusion.

| Failing case | Proven failure stage | Not established |
| --- | --- | --- |
| slow first / fast second | No ready markers after the 15s fixture barrier; request stage not reached by the parent test | Whether either child exited, stalled importing/compiling, or was descheduled; original stdout/stderr and checkpoints were not retained |
| three competing clients | Barrier passed; at least one cancellation occurred, including a result with `http_call_made=true` | Exact lock owner, wait duration, complete per-client exception and response-read stage; original pytest representation was truncated |

Two and only two diagnostic reproductions were made: original failing cases,
**2 passed / 4.74s**; then all 46 Phase100 cases, the Phase101 epoch check and the
two failing cases in CI-relative order, **49 passed / 4.70s**. The second run
added external test-only checkpoint/cleanup instrumentation, without changing
original assertions or budgets. Its fake-handler compilation took 256-509ms;
this does not explain the historical 15s barrier failure. There is no third
diagnostic rerun or local full suite.

OPS-1 does not change the HTTP executor, gate, or handler. The new minute-worker
hooks are installed only inside `NativeClient`, restored in `finally`, and are
not installed by module import. Phase111 uses isolated children for native
fixtures. This excludes an import-time hook from that implementation, not every
possible CI resource or test-order interaction.

An independent fixture lifecycle defect **is** proven by code: after a failed
ready barrier no `finally` reaped its clients, and one fallback wait was
unbounded. The minimal fix is test-only: register clients at spawn, always kill
unfinished clients and drain/close pipes within one shared 5s teardown budget,
retain PID, monotonic stages, raw fixture stdout/stderr and exit status, and
print those diagnostics only on failure. An interrupted checkpoint cannot stop
the other clients being reaped. A real subprocess regression deliberately
raises before release and confirms exit, closed pipes, zero HTTP calls and
retained diagnostics. Historical CI did not prove that its clients leaked.

No compiler relocation or production timing change is claimed. Request startup,
assembly initialization, cold delay, coordination, response reading and the
existing 2000ms / 500ms / concurrency assertions remain in place. Initial
affected regression: **57 passed / 83.21s**; final Phase101 including interrupted
checkpoint cleanup: **21 passed / 27.08s** (overlapping coverage, not additive).
Compile/diff checks passed. The one newly authorized full CI is recorded in the
external incremental index.

## Recommended Contract: Proposal Only

**CAPTURE_BUDGET_FEASIBILITY_UNPROVEN.** Recommend one new versioned contract,
`mootdx_local_prepare_capture_v2_proposal`, not an implementation or permission
to run. It supersedes the OPS-1 suggestion of pre-connecting: **no connection,
handshake, DNS, bars, heartbeat, discovery or reconnect during preparation**.
No alternative executor is proposed in this round.

Applicable dependency boundary: mootdx 0.11.7 / tdxpy 0.2.7 only. Installed
`StdQuotes.__init__` calls `TdxHq_API.connect`; that method creates/connects the
socket and executes all three setup commands. Therefore `Quotes.factory` or
`NativeClient()` cannot be called to prepare a connected ready client. A future
version must split dependency imports, strict local config validation and parser
setup from the existing factory/connect call. Vendor packages are not modified.
Fixed endpoint and `bestip=false`, `auto_retry=false`, `heartbeat=false` remain.
Missing/corrupt config must fail without regeneration or automatic discovery.

Proposed, **PM approval required**, per-wave lifecycle (T is its existing target):

| Phase | Proposed bound / evidence |
| --- | --- |
| Pure-local preparation | Spawn at T-5s. Absolute preparation deadline T-1s; at most 3500ms local work plus a shared 500ms failure cleanup reserve. Count process startup, imports, config/parser setup, output reading and ready validation. No retry on missing ready. |
| Ready / waiting | Strict JSON `READY_LOCAL_ONLY` with batch/wave/plan/code/dependency/config identity, PID and monotonic stages; not proof of connectivity. Remain network-denied until a single parent GO at T. Parent/child death, identity mismatch or cancellation stops the batch. |
| Network work | Targets unchanged: 14:49:55, 14:50:05, 14:50:30, 14:50:55. A one-use GO releases the preparation guard only for the approved fixed endpoint. Connect, three handshakes, five serial bars calls, full response reads and normal exit must fit the original T+1500ms I/O boundary. Late release loses remaining time, never shifts the boundary. |
| Termination / freeze | Original absolute T+2000ms hard boundary, shared 500ms cleanup reserve. Reuse Windows Job/process-tree and bounded pipe cleanup. Unconfirmed cleanup persists a blocker and stops all later waves. No next worker before confirmed exit. |

Preparation must be tested with outbound socket/DNS audit guards installed
**before imports**, denied socket/connect/send paths and attempt counters. Guard
violations are failures, not suppressed exceptions. Audit hooks alone cannot
guarantee arbitrary native-code isolation; approve the pinned dependency path
and demonstrate its zero-network preparation before accepting this boundary.
Do not invoke factory/config discovery in preparation and then call it local.

Use one dedicated worker per wave; no reusable connection or worker across
waves. Parent ready read, release wait, cancellation and tree/pipe cleanup all
need absolute monotonic deadlines. Record preparation elapsed, ready wait,
connect/handshakes, each bars read, cleanup, and **total residence** separately:
the proposed maximum residence is 7s (T-5s through T+2s), not a 2s total worker.
Wall timestamps provide traceability, never replace monotonic accounting.

Offline imports were about 448ms and the local socket fixture is not real TCP.
There is no proven upper bound for real connect + three handshakes + five reads
inside 1500ms. A budget feasibility decision remains necessary even if local
preparation works; fixture speed is not a production latency guarantee.

PM must approve the preparation schedule/budget, split initialization and
network-denial/release boundary, ready/cancel protocol and new plan/worker/
evidence/verifier versions before implementation. No preconnection is included.
The old contract includes process startup/imports in-wave: old failures remain
failures and old evidence is audit-only, never reclassified under this proposal.

## Preservation and Stop

Raw CI, two reproductions, phase results, protection checks, targeted regressions
and complete incremental hashes remain outside Git in
`D:\Projects\A-stock-tail\local-archive\recovery\minute-ops2-20261022`.
The 204 protected objects, 106 historical files, stashes, original NO_GO,
capture intent and earlier CI logs are retained. No source/NTP requests, system
changes, new executor, capture/task, plan modification, merge or release.
Qualification 0/3; data_ready=false; hard_gate_authorized=false; formal records,
research ranking, candidates, tickets and orders are empty.
