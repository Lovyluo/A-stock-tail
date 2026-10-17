# T3.1 Same-Round Complete Industry Candidate Audit

Base: PR #34 squash `498fa13fa38f4faa04888591999fa1a52693a07b`.
Its main CI succeeded: 1308 passed in 205.81s,
[run 38834064496](https://github.com/Lovyluo/A-stock-tail/actions/runs/38834064496).
This change prepares an executable candidate audit, not S2 qualification.
No real source requests, tasks or reminders are authorized in this delivery.

## Manual Operator Entry

The collector tests below do not cover arbitrary external PowerShell launchers.
The October 16 operator-exit incident and its versioned, behavior-tested repair
are documented in [manual orchestration](t31_manual_orchestration.md). Future
manually authorized launches must use that reviewed entry, not archived daily
operator scripts. This addendum changes no source or time contract and does not
authorize a new collection.

## Frozen Contract

- Plan: `complete_industry_same_round_plan_v1`.
- Evidence: `complete_industry_same_round_audit_v1`.
- Time configuration: `t31_complete_industry_time_configuration_v1`; the old
  T2.5/T3.0 time-version labels and historical evidence are not rewritten.
- Verifier hash: `49bf42568d4ed7338d0a86ca56fd7dd4257485df21a5348366b6497919ae68ca`.
- Existing T3.0 composer/schema, provider source versions, production registries,
  fund-flow freeze, strategy and readiness contracts are unchanged.
- Start 14:37:00; inclusive latest start **14:39:00**. +1ms is rejected before
  transport/worker creation. No automatic waiting, rescheduling or retry.
- Feature cutoff 14:40:00, collection deadline 14:41:05, decision 14:41:10;
  existing execution boundary 14:42:00 is metadata only, never trading.
- The collection deadline does not admit native events after feature cutoff.
  Every constituent and strength native timestamp is validated independently;
  no truncation, local-time replacement or clock-offset correction.

| Stage | Calls | Dependency and stop rule |
| --- | --- | --- |
| Fixed-five CoreConception mappings | 5 | Exact security/BK identity; no name inference |
| Four exact BK first constituent pages | 4 | All mappings complete; total determines necessary pages |
| Additional constituent pages | 0-3 | Only necessary, within frozen per-BK caps; sequential per BK |
| Four fltt=1 native strength calls | 4 | All necessary pages complete; one call per unique BK |
| Total | **13-16** | No retries, fallback, alternate transport or historical stitching |

Page size=100; each BK cap 1-4, all four caps sum<=7. Default caps are one page
each (13 calls). Example maximum plan: BK0474=2, BK0475=2, BK1239=1, BK1277=2
(16 reserved calls). These are ceilings, not permission to fetch unused pages.
An unnecessary reservation is never dispatched and never becomes a pending
strength dependency. A total beyond its cap, duplicate constituent, total drift,
missing mapped stock, invalid time or child failure stops the entire batch.
Raw failed responses remain in the trace; there are no partial formal records.

## Executor and Budget

Reuse `BoundedAuditExecutor` and the production .NET worker, with global and
same-host in-flight limits of two. Its single protected budget query now permits
this candidate-specific executor to reserve the remaining plan before **every
dispatch**; the legacy default remains 2000+1000+500ms, unchanged.

The HTTP gate still enforces actual SendAsync client-call spacing >=500ms.
This is not packet timing. Startup, gate waiting and complete response/body/pipe
reading remain inside 2000ms per request. Queue time consumes the batch budget.
One existing global lock is held across stages; per-stage executor locks are
separate. Existing locks or pending/corrupt HTTP journals are not reset.

For conservative admission, serialize even across different hosts rather than
assuming throughput from two workers. For N reserved requests:

`N*2000 + (N-1)*500 + 1500 cleanup/freeze + 3*500 stage boundaries`.

This is **35000ms (13)** or **42500ms (16)**. The limits fit a 14:39:00 start
under the planning bound; no later initial start is accepted. At each stage and
dispatch, reserve unfinished/in-flight calls, future approved calls and remaining
stage/cleanup allowances. After first totals, drop only unused reservations.
Insufficient remaining budget causes zero additional dispatch; real elapsed
monotonic time cannot be refunded by a backward wall-clock change across stages.
Scheduling stalls do not authorize extra time: raw time checks still fail closed.

## Capture and Replay

The same-round batch ID, fixed codes, source versions and full temporal contract
are created once. Request bytes, raw hashes, worker-native timestamps, actual
monotonic client calls, parent process boundaries and stage traces are retained.
The producer defines observation-at-start for mapping/constituent requests and
observation-after-read for strength, matching the existing component contracts.
It never repairs an upstream event time. T3.0 receives only this batch's captures.

The CLI requires a frozen plan hash for explicit network execution and writes an
atomic, non-overwritable `.plan.json` sidecar before dispatch. Outputs must be in
ignored cache. Exact Git HEAD and a clean worktree, completed calendar/session
external anchors and their existing verifiers are required; no source constructor
or worker is created in default/validate-only mode.

Independent replay requires an external file SHA, verifies internal hash/version,
rebuilds the capture chain, request set, first-total pagination, stage dependencies,
client-call order/counts and T3.0 composition. Re-signing derived summaries does
not replace raw response replay. Failed, externally anchored audit evidence may
pass file integrity while remaining incomplete; `failure_reason_verified=false`
does not claim to independently prove network/timeout causes. Unknown network
activity after an unexpected runtime error is null, never a fabricated zero.

Synthetic worker envelopes must match the batch's test scope. Local tests use an
explicit in-memory HttpMessageHandler and a synthetic wall-clock axis; the real
monotonic process deadlines and client-call gate still execute. These captures
are not source evidence or calendar/session confirmation. Production has no test
worker, test payload or fake-clock CLI option.

## Commands (Preparation Only)

Use an actually confirmed trade date. These examples do not authorize collection.
Run from the fixed, reviewed worktree, with its final PM-approved commit.

```powershell
$py = 'D:\A-stock\.venv\Scripts\python.exe'
$root = 'D:\Projects\A-stock-tail\worktrees\active\A-stock-v043-eastmoney-t2'
$cli = "$root\overnight_quant\scripts\run_complete_industry_audit.py"
$date = 'YYYY-MM-DD'
$cache = "$root\overnight_quant\data\cache"

# Default (also accepts --validate-only): no transport, no worker, no HTTP.
& $py $cli --date $date --validate-only --output "$cache\t31_plan_$date.json"
$frozen = Get-Content -Raw -Encoding UTF8 "$cache\t31_plan_$date.json" | ConvertFrom-Json

# Optional: use --page-limits-file with the same reviewed UTF-8 caps file in BOTH
# commands; otherwise the example uses the default one-page-per-BK (13 calls).
# Future manual execution only, after clock and current calendar/session approval:
& $py $cli --network --date $date --expected-head '<PM-approved-full-SHA>' `
  --expected-plan-hash $frozen.plan.plan_hash `
  --completed-calendar '<calendar.json>' --calendar-file-sha256 '<external-SHA256>' `
  --session-confirmation '<session.json>' --session-file-sha256 '<external-SHA256>' `
  --output "$cache\t31_same_round_$date.json"

$sha = (Get-FileHash "$cache\t31_same_round_$date.json" -Algorithm SHA256).Hash.ToLowerInvariant()
& $py $cli --replay "$cache\t31_same_round_$date.json" --expected-file-sha256 $sha --output "$cache\t31_replay_1_$date.json"
& $py $cli --replay "$cache\t31_same_round_$date.json" --expected-file-sha256 $sha --output "$cache\t31_replay_2_$date.json"
```

Do not reuse old calendar/session files or future-date fixtures as real inputs.
No automatic task, reminder or background collection loop is provided.

## Stop State

Offline acceptance: 21 Phase103 cases (19-case run plus the final fixed-SHA
and partial-failure-receipt regressions) passed. The affected suites passed 325
cases, followed by two caps-file, one fixed-SHA and one partial-receipt case:
329 distinct related tests. Actual
local HttpClient fake-handler/process chains cover 13 calls, 16 calls, unused
reservations, page caps/duplicates/total drift, timeout cancellation, inclusive
14:39:00 and +1ms rejection, zero-dispatch insufficient budget, exclusive locks,
double replay and re-signed capture/scope/plan/summary tampering. The public
zero-network CLI was also invoked as a subprocess. No local full suite was run;
the new Draft PR initially received one full CI run after the consolidated push.

That CI returned 1328 passed / 1 failed: the old T29 zero-HTTP budget test saw
an OperationCanceledException before its explicit monotonic deadline check.
The zero-call assertion passed. A focused fixture-only follow-up classifies
deadline-timer cancellation before the first HTTP call as budget exhaustion,
retaining the original exception type/text and a false success status. The
production gate, deadlines, spacing and assertion strength are unchanged. One
deterministic cancellation test was added (330 distinct related cases in total).
Only the failed module and Phase103 were rerun locally: 40 passed in 76.37s;
the timer fixture also has an isolated targeted check. One corrective CI follows.
The failed CI log remains in the external recovery index, not rewritten as success.

Success means only `COMPLETE_INDUSTRY_SAME_ROUND_CANDIDATE_VALIDATED` with five
audit records. Formal records and trading outputs stay empty. The public
production Adapter cannot activate this identity. Production 32/8/7 matrix and
its hashes remain unchanged; no new production binding or fund-flow parsing.

`qualification=0/3`, `provider_contract_complete=false`, `data_ready=false`,
`hard_gate_authorized=false`, `sampling_authorized=false`. Keep the new PR Draft;
no merge, release, real collection or qualification day in this delivery.
