# MINUTE-OPS-1: offline startup and exception diagnosis

## Scope and Historical Timeline

Baseline: `787ce0cabc569caba0c7b6c15337bcd8c026d7b1`. The October 22
failed capture, intent, NO_GO, cleanup receipts and original hashes remain
unchanged. This investigation makes no source requests and does not authorize
another capture. Installed mootdx 0.11.7 / tdxpy 0.2.7 are not upgraded.

| Stage | Historical evidence | Clock and limitation |
| --- | --- | --- |
| Parent dispatch | 14:49:55.000276 | Parent wall clock, not an on-wire timestamp |
| Process/scope/pipe preparation | 185.709ms | Parent perf_counter duration; not isolated CreateProcess cost |
| Worker starts capture | 14:49:55.241493 | Child wall clock; no historical module-import timestamps |
| Native client ready | 14:49:56.386114 | 1144.621ms after wave_started on the same child wall clock; imports, configuration, connect and three handshakes cannot be separated retrospectively |
| First bars call | Remaining IO 113.414ms | Historical remaining-budget receipt; no new cross-process clock-offset claim |
| First call fails | 14:49:56.398765 | Child wall-clock span 12.651ms, not a network RTT; wrapper reports TdxFunctionCallError |
| Parent IO / cleanup / total | 1499.210 / 2.7025 / 1501.912ms | Parent monotonic durations; REQUEST_DEADLINE_EXCEEDED at read_and_normal_exit, cleanup confirmed |

The aggregate child initialization occupies most of the *visible* work window.
Its internal cost distribution is UNKNOWN. Do not subtract uncalibrated parent
and child counters, mix wall durations with monotonic durations, or infer
historical TCP latency by subtracting today's local import measurement.

The retained terminal failure JSON is emitted after `capture_wave` has returned
through `client.close()`. Thus it is not proof of a historical close that never
returned. The parent still required root exit, Job emptiness and both pipe EOFs;
the historical timeout does not identify which was pending. Displayed wall-clock
order is not a calibrated cross-process causal trace. A local delayed-exit
control demonstrates this distinction, not the root cause of the original exit
delay. No timeout is reclassified as success.

## Parser and Hidden Paths

The saved two bytes `20 03` are the **decompressed GetSecurityBarsCmd parser
input**, not a complete TCP packet or retained response header. The installed
parser interprets their little-endian count as 800 and then requires four date
bytes that do not exist. Exact-body local replay deterministically raises
`struct.error: unpack requires a buffer of 4 bytes` in `get_datetime`, through
`parseResponse -> BaseParser._call_api -> get_security_bars`. The tdxpy
`last_ack_time` decorator wraps it in TdxFunctionCallError and attaches
`original_exception`. The old worker discarded that detail. The original inner
traceback and wire header were not saved; packet truncation, server fault, or
the reason for that parser body cannot be concluded from this replay.

StdQuotes.bars has no tenacity retry decorator. The selected socket API's retry
loop requires auto_retry=true; this worker passes false. Heartbeat and explicit
bestip are false, bars does not call reconnect, and no adjust mode is requested.
Three setup exchanges and actual connect remain inside each wave's budget.
Fresh construction per wave is intentional; no repeated within-wave factory
initialization was found. Eager pandas/httpx/ext/utility imports are measurable,
but changing the dependency stack is not a demonstrated safe startup fix.

**Confirmed contract risk:** mootdx `config.setup()` invokes `bestip()` on
missing/corrupt local configuration even if the factory's bestip=false. This
branch was trapped once per baseline fixture; external socket attempts were
blocked and counted zero. The historical capture did not retain configuration
evidence sufficient to prove whether that branch ran that day.

## Minimal Changes and Offline Evidence

- Replace only the isolated NativeClient's vendor setup callback during factory
  construction with strict local JSON loading. Missing, unreadable, corrupt or
  invalid configuration rejects before connect/discovery; restore the callback
  in finally. No config writes, default generation, alternate nodes or retries.
- Retain bounded original_exception/cause/context chains, root frame names,
  request failure monotonic time and remaining budget. Add same-worker timing
  events for version check, imports, factory/connect/setup and close. Logs do
  not alter native event timestamps, provide finality, or extend any deadline.
- These are additive diagnostics and fail-closed enforcement, not a new source
  identity, profile or execution plan. The frozen plan/hash, worker protocol,
  20 bars / 0 ticks, serial fixed node, 1500ms work + shared 500ms cleanup and
  four observation times remain unchanged. Old evidence remains auditable.

Three bounded comparison groups used actual Python/dependency imports and real
child processes: (1) cold interpreter imports and complete scripted TCP path;
(2) exact short-body parser error, blocked close and delayed normal exit;
(3) missing/corrupt configuration. Fixture preparation was separate, no
compilation was required, and network connect/handshake/responses were scripted.
External Python socket operations were denied before imports and counted zero.
All children were reaped. Cold interpreter is **not** cold OS filesystem cache.

Local baseline imports were 445-467ms, complete fixture total 644.648ms. After
the change the complete fixture was 621.065ms, imports 448.487ms, scripted
connect/setup 0.082ms. This is not evidence of a real-world speedup or TCP bound.
Missing/corrupt cases changed discovery count 1 -> 0; their connect count stayed
0. Blocked close / delayed exit remained failures at about 1505ms with cleanup
confirmed. The secondary cause survives in partial stdout before termination.

Phase111 plus Phase107 capture/cleanup and Phase78/87 regressions: **92 passed
in 48.29s**. An initial invocation error ran zero tests and is preserved. Full
suite runs only in the Draft PR's final CI, not locally. Raw controls, installed
source fingerprints, historical timeline, test logs, recovery and full hashes:
`D:\Projects\A-stock-tail\local-archive\recovery\minute-ops1-20261022`.

## Budget Decision and Stop

**CAPTURE_BUDGET_FEASIBILITY_UNPROVEN.** The current 1500ms must cover process
startup, imports, connect, all three handshakes, five complete bars calls and
normal exit. A local socket fixture cannot bound those real network stages, and
the historical first call had only 113ms left. This PR does not claim to make
that budget feasible or schedule another real attempt to prove it.

The smallest separate contract proposal is a bounded per-wave pre-start /
pre-connect phase, with its own absolute deadline, connection age and failure
policy explicitly approved by PM. Request times/counts, no reconnect/fallback
and window cleanup rules would remain explicit. That moves real cost into a
new audited execution phase, not out of accounting, and is **not implemented or
authorized here**. Persistent or cross-wave workers and budget changes are also
outside this fix. Await PM's decision; do not supplement today's waves.

Qualification stays 0/3; data_ready=false, hard_gate_authorized=false. Formal
records, research ranking, candidates, tickets and orders remain empty.
