# T3.1 Manual Operator Orchestration Hardening

## Confirmed Failure and Scope

The 2026-10-16 operator stopped before final preflight and before the industry
CLI. Its sealed `foreground-launch-once.ps1` invoked a cmdlet-only PowerShell
script in-process, then tested `$LASTEXITCODE`. The successful script did not
execute a native program or explicitly establish a success exit code. The
caller therefore tested an unset or previously inherited native exit status,
not this stage's outcome. The original process did not preserve that variable;
its exact value remains UNKNOWN.

An isolated reproduction in Windows PowerShell 5.1 and PowerShell 7 produced:

| Inherited LASTEXITCODE | Environment | Old next-stage call | Old exit |
| --- | --- | --- | --- |
| unset | passed | no | 2 |
| 17 | passed | no | 2 |
| 0 | passed | yes | 0 |

This is an operator protocol bug, not evidence of an Eastmoney, clock or field
failure. The sealed NO_GO and the zero industry requests remain unchanged.
The archived faulty scripts must not be edited or reused as a live launcher.

Other documented operator problems had different mechanisms: October 8 used an
invalid cutoff/deadline pair, and October 15 compared implicitly converted
PowerShell DateTime data with ISO strings. Their shared gap was unversioned
operator glue outside the tested collector CLI. Genuine October 13/14 clock
errors remain valid refusals; they are not reclassified as this bug.

## Execution Contract

`run_complete_industry_audit_manual.py` is a small, versioned foreground entry.
It reuses the frozen plan, existing collector CLI, atomic JSON writer and
process-tree cleanup. It does not acquire calendar/session data or measure NTP.
It does not introduce a scheduler, provider, retry or recovery fallback.

- Default and `--validate-only` read files only: no child process or networking.
- Explicit `--network` claims a CreateNew run directory before checking gates.
  A failed attempt cannot be overwritten or automatically retried.
- Environment inspection runs as a **native child process**. Its repository
  PowerShell script explicitly exits 0 or 2. Python reads the child's returncode;
  no decision uses a parent's `$LASTEXITCODE`, `$?` or console JSON.
- Success requires both native exit 0 and strict structured stage JSON. Missing,
  malformed, wrongly typed, failed or test-only production data is rejected.
- JSON timestamps stay ISO strings until explicitly parsed. No automatic
  PowerShell DateTime conversion participates in identity comparison.
- Stage intent, stdout/stderr bytes, exit code, elapsed time and cleanup receipt
  are retained, including launch errors and timeout failures.
- Timeout uses existing process-tree termination and waits for the child. Tests
  include a real sleeping child/grandchild, not a source-code string assertion.
- Frozen date/head/clean-worktree/plan checks and existing clock/preflight rules
  still apply. Final preflight remains separately pinned by its file SHA. It
  must be reviewed for the newly authorized date; this launcher does not make
  a historical or incorrect preflight valid.
- Admission is checked again at the existing inclusive 14:39:00 boundary;
  +1ms is refused. No deadline, 500ms spacing, 2000ms request limit, clock
  freshness limit or Hold policy changes.
- A business intent is recorded before starting the collector. Accepted starts
  are never retried. Actual source networking is unknown (`null`) until replay,
  not reported as zero merely because the child failed.

Status distinguishes `NO_GO` (no source process started) from
`MANUAL_AUDIT_COLLECTION_FAILED` (collector started but did not pass). A completed
capture is only `MANUAL_AUDIT_COLLECTION_FINISHED_REPLAY_REQUIRED`; external-SHA
verification and two independent replays are still required. No operator result
grants qualification or source activation.

## Offline Preparation

After review and a normal commit, use the newly approved fixed SHA. Do not run
production from this development worktree or reuse the consumed October 16 run.

```powershell
$py = 'D:\A-stock\.venv\Scripts\python.exe'
$root = 'D:\Projects\A-stock-tail\worktrees\active\A-stock-v043-eastmoney-t2'
& $py "$root\overnight_quant\scripts\run_complete_industry_audit_manual.py" `
  --validate-only --ops-directory '<new-reviewed-ops-directory>' `
  --preflight-sha256 '<externally-recorded-preflight-file-SHA256>' `
  --page-limits-file '<reviewed-one-page-per-BK-json>'
```

The ops directory contains reviewed `intent.json`, `prerequisite-anchors.json`
and `launch_preflight.py` plus independently prepared current-day prerequisites.
Validation checks the generated 13-request plan and pinned preflight file only;
it is not collection permission or proof that the final live gates will pass.
Any future explicit network use needs a fresh manual authorization, current
evidence, a clean reviewed code SHA and unused immutable output paths.

## External Preflight Approval Boundary

`launch_preflight.py` remains an external dependency, not a reviewed module of
this commit. Before any live preparation, reviewers must inspect the actual
file's logic, input paths, date/head checks, clock rules, dual-contract verifier
calls and non-overwrite behavior. Record that approval separately, then bind
the approved file's independently computed SHA-256 with `--preflight-sha256`.
A matching hash proves file identity only; it does **not** prove correctness or
replace logic review. Passing the synthetic launcher tests does not approve
that external script either.

For the next authorized preparation, reuse the reviewed preflight revision and
the versioned operator entry. Do not introduce temporary outer launch glue or
modify preflight logic at the collection window. If an approved file embeds a
date, path or fixed SHA that must change, review that exact diff and record a
new external file anchor before launch. Never relabel a historical preflight
as current solely because its old hash matches. Until this dependency is
approved for the new run, the launcher must not be treated as collection-ready.

## Regression and Safety

Phase104 executes actual PowerShell processes and the actual environment script
with isolated cmdlet mocks. Its synthetic responses are explicitly test-only;
no test result is source evidence. Cases cover stale/unset exit state, exceptions,
stderr, malformed/missing files, strict types, immutable outputs, simultaneous
controllers, exact time boundaries, timeout tree cleanup and early failure
receipts. Phase103 separately exercises the existing collector chain.

The first PR CI exposed a fixture-only timezone conversion: PowerShell 7 parsed
an ISO JSON string as DateTime and serialized it in the UTC runner's timezone.
The fixture now writes a pre-serialized synthetic JSON string verbatim. Exact
timestamp-text assertions remain, with explicit non-local offsets and fractional
seconds tested in both shells; no production time rule or assertion is relaxed.

The corrective CI passed the operator tests but exposed an existing body-timeout
fixture race: its 100ms test budget could expire during setup, before SendAsync.
That one regression now uses the unchanged production limit (2000ms) and a
3000ms synthetic body delay. It still requires a real single call and failure,
and additionally checks exit 2, deadline cancellation and the raw cancellation
exception. The production gate and startup-inclusive budget are untouched;
separate zero-call budget and startup/body-read regressions remain in place.

This fix does not change providers, Registry/Adapter identities, strategy,
readiness, qualification, clock configuration or old evidence. It does not
repair a historical failed audit into success. Qualification stays 0/3;
data_ready, hard_gate_authorized and sampling_authorized are false; records,
candidates, tickets and orders stay empty.
