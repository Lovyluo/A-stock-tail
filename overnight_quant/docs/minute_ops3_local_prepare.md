# MINUTE-OPS-3: Candidate Local-Preparation Execution Contract

## Status and Scope

PR #42 was reviewed at `19974bf5a37f04d0c4c751c48f8db0367eb56427`, squashed as
`1bfc7155811bb8399fa3facc00fce905950bef4d`, and its
[main CI39759174294](https://github.com/Lovyluo/A-stock-tail/actions/runs/39759174294)
passed **1691 tests / 401.64s**. This implementation starts at that exact SHA.
Historical CI timeout causes remain **ROOT_CAUSE_UNCONFIRMED**. The independently
proven fixture cleanup fix is not a retrospective diagnosis of those failures.

This is an offline-tested, **unactivated candidate audit executor**, not a minute
Provider, finality profile, source qualification or research signal. There are no
source/NTP requests, clock changes, tasks, reminders, releases or dependency
package modifications in this delivery. Real dates require separate PM approval.

## Frozen Timing

T remains 14:49:55, 14:50:05, 14:50:30, 14:50:55. Fixed five stocks / node,
four independent workers/connections, 20 serial bars calls, zero transactions,
one page per stock, no retry/fallback/discovery or cross-wave reuse.

| Phase | Absolute boundary and accounting |
| --- | --- |
| Start / local work | At T-5s, record the monotonic start before task hashing and Popen. Process startup, imports, local config validation, parser initialization, pipe reading and parent READY validation share at most 3500ms; no later than T-1.5s. |
| Preparation failure cleanup | Shared 500ms, with absolute stop at T-1s. This reserves cleanup inside the approved four-second preparation window; it does not add 500ms to each operation. |
| READY wait | No connection. One parent GO at T, with actual send/receive monotonic order bound to the task. Ready-wait cancellation receives at most shared 500ms within the original absolute final boundary; it never restarts preparation. |
| Network work / normal exit | Connect + three protocol handshakes + five serial bars + complete reads + normal process/tree/pipe exit by T+1500ms. Late GO loses time and never resets the deadline. |
| Work failure cleanup | Shared 500ms, no later than T+2000ms. Existing Windows Job tree tracking and unbuffered pipe draining are reused. |
| Residence | May approach **7000ms**, T-5s through T+2s. It is not described as all work fitting in two seconds. Preparation, wait, work, cleanup and residence are retained separately. |

Collection 14:51:05, decision 14:51:10 and existing audit freeze/publication
budgets are unchanged. No new source event, interval, volume/amount or finality
semantics are created. Observational stability is never proof of upstream finality.

## Protocol and Network Boundary

- The worker installs its audit guard before importing mootdx/tdxpy. Preparation
  imports only pinned mootdx 0.11.7 / tdxpy 0.2.7, strictly reads the anchored local
  config and constructs unconnected parsers. It does **not** call Quotes.factory,
  whose installed implementation immediately connects and handshakes.
- DNS, socket connects/datagrams and helper-process creation are denied before
  GO. An attempt is counted **before** raising, and the counter must remain zero
  even if imported code swallows the exception. After GO, only one connect to the
  frozen IP/port is allowed. Heartbeat, retry and best-IP discovery stay disabled.
- This covers the pinned Python socket call path, **not arbitrary native code**
  or a general OS network sandbox. The local fixture has an additional outbound
  guard and substitutes the socket with an explicitly synthetic protocol object.
- READY_LOCAL_ONLY binds batch, wave, plan, code, worker-file SHA, pinned versions,
  anchored config, actual PID, parent chain and monotonic stages. The parent
  verifies Job membership, not PID equality with a Windows venv redirector.
- Strict JSON GO binds the complete task and its actual send time. Early,
  duplicate, extra-field, wrong-task and wrong-version messages fail before
  connecting. Exactly one GO followed by EOF is required; raw stdin polling is
  bounded, with no blocked readline or background input thread.
- A kill-on-owner-handle-close setting is used **only for this new path**. Killing
  the real owner during a blocked fixture bars call kills its owned interpreter
  and descendants. The root starts suspended, is assigned to the Job, then its
  primary thread is resumed: a venv redirector cannot create an untracked child
  in the Popen-to-Job gap. Delayed Job assignment is a real-process regression.
  The legacy Job/startup defaults are unchanged. READY/work timeouts,
  cancellation, parent/child exit and failed cleanup stop later waves.
- Date intent and cleanup-incomplete.json share the legacy capture root. A new
  version cannot bypass an old intent, pending lifecycle or cleanup blocker.
  Both versions and the environment inspector check immutable wave-start receipts
  across dates against hash-bound matching cleanup receipts. Owner death leaves
  an unmatched start blocked even if its Job probably exited. A normal confirmed
  cleanup adds a receipt; it never deletes or resets the start. Unknown/damaged
  receipts fail closed and require a separately approved recovery.
  The legacy execution entry shares only this new uncertainty veto; its old
  evidence verification and approved qualification scopes are not reinterpreted.
  Receipts are create-only; no automatic deletion, reset, rename or recovery.

## Versions and Evidence

| Component | New version |
| --- | --- |
| Explicit selection | local_prepare_v2 |
| Plan | mootdx_minute_capture_plan_v2 |
| Worker | mootdx_local_prepare_worker_v2 |
| Evidence / verifier | mootdx_minute_capture_audit_v2 / mootdx_minute_capture_verifier_v2 |
| Preflight / verifier | minute_capture_preflight_v2 / minute_capture_preflight_verifier_v2 |
| Manual authorization | minute_capture_manual_authorization_v3 |

The old v1 capture and preflight defaults remain unchanged; selecting the new
preflight is explicit. Old authorization cannot start the new executor. The new
authorization binds the approved code/date/plan, anchored v2 preflight and local
config. Preflight never generates PM approval or automatically starts capture.
The upstream minute source version stays unchanged: this is a new **execution**
contract, not new proof of source semantics. Existing approved historical scopes
and raw evidence are not revoked or reclassified.

Raw worker streams, stream hashes, tasks, READY/GO, full native rows/parser
bodies, source hashes and per-stage timings are preserved in immutable audit
evidence. Verifier identity has a deterministic rules/plan hash. Independent
replay recomputes native-row checks and timing/batch/code/config bindings against
an external file SHA. The original seven-second residence is never overwritten
to reuse the legacy native-row checks; only an internal network-window view is
passed to those unchanged checks. Integrity does not imply successful capture,
finality, authorization of formal sampling or source qualification.

## Commands and Offline Acceptance

Safe, complete default command (no worker, source request or NTP):

```powershell
& 'D:\A-stock\.venv\Scripts\python.exe' 'D:\Projects\A-stock-tail\worktrees\active\A-stock-v043-eastmoney-t2\overnight_quant\scripts\run_minute_local_prepare_audit.py' --validate-only
```

Future manual syntax only, **not execution permission**:

```powershell
# All values must come from a separately approved date/code/immutable batch.
& $python overnight_quant/scripts/run_minute_capture_preflight.py --execution-contract local_prepare_v2 --prepare --date $day --approved-code-sha $code --plan $plan --expected-plan-sha256 $planFileSha --output-dir $newPreflightDirectory --calendar $calendar --calendar-sha256 $calendarSha --session $session --session-sha256 $sessionSha --before-environment $baselineEnvironment --before-environment-sha256 $baselineEnvironmentSha --clock-raw $clockRaw --clock-raw-sha256 $clockRawSha
& $python overnight_quant/scripts/run_minute_capture_preflight.py --execution-contract local_prepare_v2 --verify $preflight --expected-file-sha256 $preflightSha --date $day --approved-code-sha $code
& $python overnight_quant/scripts/run_minute_local_prepare_audit.py --network --date $day --plan $plan --expected-plan-sha256 $planFileSha --authorization $manualV3Authorization --authorization-sha256 $manualV3AuthorizationSha
& $python overnight_quant/scripts/run_minute_local_prepare_audit.py --verify $rawEvidence --expected-file-sha256 $rawEvidenceSha --replay-output $newReplayPath
```

Phase112/113 exercise real processes and installed parser/client code with local
scripted TCP responses: full four-wave CLI/preflight/verifier/capture/replay,
early/duplicate/wrong GO, READY identity/timeout, swallowed network attempts,
configuration mismatch, cancellation, late GO with original deadline, missing
EOF, owner death while in bars, descendant-held pipes, cleanup failure and later
zero dispatch, old-version rejection, and immutable historical intent/guard.
PS5.1/7 behavior tests recognize the new worker/CLI/descendants/tasks without
classifying unrelated Python processes as minute workers. Synthetic scope is
rejected by the actual production worker before vendor imports.

Legacy capture/preflight/hash/clock/native diagnostics and shared cleanup
regressions remain affected checks. No local full suite; one final full PR CI.
Affected regression: 183 passed / 190.62s; shared cleanup: 52 passed / 35.63s;
new-contract round: 36 passed / 97.97s; final receipt/replay round: 26 passed /
45.78s plus scalar/mutable-identity checks (overlapping, not additive). One
explicit test-path invocation error ran zero tests and is retained. Fixture
preparation was 674-707ms, work 139-161ms, cleanup about 0.1ms and residence
5.14-5.16s. Nested handshake time is a subset of connect, not an extra addend.
Final timings/results and complete new hashes are in the incremental index:
`D:\Projects\A-stock-tail\local-archive\recovery\minute-ops3-20261022`.

**CAPTURE_BUDGET_FEASIBILITY_UNPROVEN.** The fixture only measures local startup
and scripted connect/handshake/read overhead. Real TCP latency and five-stock
success inside 1500ms remain unproved. Minute amount/interval/finality gaps stay
UNPROVEN; no next live attempt, finality profile or qualification day is arranged.
Qualification 0/3, data_ready=false, hard_gate_authorized=false; formal records,
research ranking, candidates, tickets and orders stay empty. New PR stays Draft.
