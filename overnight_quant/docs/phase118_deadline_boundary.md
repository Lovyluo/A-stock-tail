# Phase118 deterministic deadline boundary diagnosis

## Proven Defect, Historical Limit

Baseline: `7fbf91549a923552794acd3babe795436d61c8a2`.
CI 40792221437's compact artifact identifies B's pre-worker rejection as
`protocol_diagnostic_deadline_contract_mismatch`. A passed and was sealed and
cleaned. B's task construction inputs and individual failed predicate were not
retained. Its specific triggering field remains UNKNOWN; this is not evidence
of a market timeout, upstream problem or Windows synchronization failure.

A deterministic counterexample in the current constructor is independently
proven (synthetic values, no request):

| Value | Exact displayed float |
| --- | --- |
| started | 1019.9999999999999 |
| target minus actual datetime | 5.0 seconds |
| constructed target_monotonic | 1025.0 |
| target_monotonic minus started | 5.000000000000114 |

The old subtract-then-compare predicate rejects that constructor-generated task.
The extra subtraction introduces cancellation across the floating-point exponent
boundary. All four deadline equations already agree with their constructor.
This proves a numerical rejection defect, not the precise historical CI cause.

## Minimal Correction and Compatibility

Compare `start < target <= start + 5` directly, using the constructor's existing
representable upper boundary. There is no clamp, sleep, retry, target reset or
new epsilon. One representable step above that upper boundary is still rejected.
The existing 1e-9 consistency tolerance for the four deadline equations remains
unchanged. No timestamps, deadline values or time representation are rewritten.
The T-5 preparation boundary, absolute T+1500ms work deadline, T+2000ms deadline
and shared 500ms cleanup are unchanged.

Plan, evidence and authorization versions remain unchanged: this corrects the
evaluation of their existing constructor boundary, not a new execution contract.
New executions still bind the reviewed code SHA. Historical originals, failed
intents, verdicts and qualifications are not rewritten or reclassified.

Failures now report the exact predicate, actual/expected values and signed
difference using Python's round-trip float JSON representation. Nonfinite or
invalid types are represented as diagnostic strings, never accepted as times.
Constructor failures also report target/actual datetimes, started perf_counter,
lead and caller clock sources. The CLI preserves this error before READY/GO;
it does not fabricate worker evidence or a successful task receipt.

## Offline Verification

Synthetic tests cover exact/under/over five-second leads, zero/negative leads,
multiple monotonic bases, serialization, A-to-B fixture clock advancement,
each deadline independently altered, contradictory order, nonfinite/wrong types
and one-ULP upper-bound violations. Existing absolute-time/protocol/differential
tests and real PS7 CLI single/matrix paths retain source/NTP denial and actual
fixture workers. Passing local or CI tests does not retroactively identify the
historical triggering field. Full results and hashes are in the external index:
`local-archive/recovery/phase118-deadline-20261029`.

No source requests, NTP, collection authorization, merge, task or release.
ROOT_CAUSE_UNCONFIRMED and CAPTURE_BUDGET_FEASIBILITY_UNPROVEN remain;
qualification=0/3, data_ready=false, hard_gate_authorized=false and formal
records, research ranking and transaction outputs remain empty.
