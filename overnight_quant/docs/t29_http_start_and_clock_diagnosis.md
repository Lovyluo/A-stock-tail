# T2.9 HTTP client-call spacing and clock diagnosis

## Scope

Base: `0c3a53acab403f073ed205dca780f6938ed49135` (main at start).
The existing worktree and uncommitted October 12 progress update are retained.
No market request, resampling, time adjustment, NTP polling, task, or reminder.
This is a candidate-infrastructure fix, not source qualification.

## Client Call Contract

- Executor v2: `two_process_http_call_rate_limited_audit_executor_v2`.
- Coordination v1: `http_client_call_spacing_v1`.
- Same-host clients share an exclusive, append-only local journal. The critical
  section contains the actual `HttpClient.SendAsync()` invocation, not a parent
  dispatch timestamp or an advance permission grant.
- The next call waits at least 500ms after the previous invocation returned its
  task. This conservative post-call marker also covers synchronous startup delay.
- `request_started_at` denotes the client API call boundary; it does not measure
  DNS/TLS traffic or an on-wire packet. Handler observations in tests are likewise
  client-side invocation observations, not network packet capture.
- Waiting, worker startup, invocation, full body reading and pipe processing all
  consume the original 2000ms parent deadline. Parent Python and child .NET use
  the Windows shared performance counter; a real-process test verifies its epoch.
- Deadline, cancellation, stale/malformed/torn/pending journal state fail closed.
  A pending marker is flushed before calling the client. Completion is appended,
  never written by truncating the marker. Worker termination cannot erase it.
- The bounded executor still owns process-tree cancellation and stop-dispatch.
  No retry, fallback, alternate transport, additional worker or longer budget.
- Uncertain journals are not automatically reset. Any recovery requires a separate
  operator review proving there are no active clients. No cleanup is performed here.
- Wall timestamps retain their native precision and explicit +08:00 offset. The
  body completion timestamp follows EOF. Monotonic time controls waits/deadlines.
- A 25ms wall-vs-monotonic instrumentation discrepancy guard additionally rejects
  clock discontinuities. This is NOT source-event tolerance: event/observation/
  availability ordering remains exact, with no one-second grace, truncation or shift.
- Upstream transport/source/Registry identities and historical verifier contracts
  are unchanged; the new execution coordination has its own version and metadata.
  Production bindings, readiness and source semantics are not modified.

## October 12 Findings

Frozen evidence file SHA-256:
`9aba77f789b994b4d9f563c350a5d4d4fad301c794234334f4e3bce897b197f6`.
Evidence hash:
`8772b707881d1810d2a4cd869988427cdff8ab76451798f0a5405ad278028b66`.
Full original/replay/protection manifests remain outside Git under
`D:\Projects\A-stock-tail\local-archive\recovery\bk-manual-audit-20261012`.

| Finding | Classification |
| --- | --- |
| Parent dispatch gap >=500.613ms, actual client-start gap 240.970ms | Proven limit applied at wrong boundary; fixed by shared client-side gate |
| Old completion recorded after streaming loop reached EOF | Proven; not merely response-header arrival |
| f86=1791787023 converts to 2026-10-12T14:37:03+08:00 | Python/.NET agree; no UTC/+08:00 conversion defect found |
| BK1239/BK1277 event later than body completion by 664.404/115.674ms | Proven exact ordering rejection; original evidence remains rejected |
| Parent wall minus monotonic duration: +0.306/-0.108/+0.058/+0.044ms | No large parent clock jump observed inside these four requests |
| October 12 saved NTP offsets 1324.2891/1404.6412/1403.6629ms, service reported unsynchronized | Historical observations; local lag is a plausible cause, not proven root cause |
| No client-side monotonic samples in old response | Historical child clock jump cannot be independently ruled out or reconstructed |

Python keeps full JSON timestamp strings. Python's `fromisoformat` retains
microseconds; the .NET seventh decimal is 100ns, not a one-second rounding rule.
PowerShell diagnostic JSON readers must preserve strings (`-DateKind String` when
supported): implicit date-to-string conversion can lose fractional seconds. This
reader artifact was not used to repair or reinterpret the original evidence.

## Current Clock Observation

Read-only Windows query on October 13 reports leap indicator 0, stratum 5,
source `time.windows.com,0x9`, and last successful synchronization at 04:53:58.
Timezone is `China Standard Time` (+08:00). No resync/configuration/remote NTP
poll was performed. Current service state does not prove October 12 state or
the current offset to an independent reference.

## Verification and Next Preconditions

Phase101 uses isolated real PowerShell processes and an in-memory .NET
`HttpMessageHandler`: there is no socket transport. It covers slow/fast startup,
competition, lock-wait cancellation, exhausted budgets with zero client calls,
delayed body/EOF, forward/backward wall jumps, killed/torn journals, shared
monotonic counters, and parent process startup/body/pipe limits.
Phase94/99/100 cover directly affected transport/executor/candidate compatibility.
Historical bytes and independent replay outputs are compared, never rewritten.

Before any new collection: PM review of this Draft PR, a separately confirmed
clock synchronization/offset record, a clean fixed approved SHA, verified same-day
calendar/session, immutable fresh output and no residual clients/tasks. Any clock
adjustment needs separate approval and must finish before the batch starts.
There is no newly authorized collection in this change. Industry native response
qualification and fund-flow interval/accumulation semantics remain unproven.

`qualification=0/3`, `data_ready=false`, `hard_gate_authorized=false`,
`candidates=[]`, `tickets=[]`, `orders=[]`.
