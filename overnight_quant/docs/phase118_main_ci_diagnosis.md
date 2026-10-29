# Phase118 main CI failure diagnosis

## Scope and Evidence

- Baseline: `6621a42e5dab01b9fea1a26f795e48f92d7d6517`.
- Failed run: https://github.com/Lovyluo/A-stock-tail/actions/runs/40629472096
- PS7, differential=True, normal input, actual CLI through real subprocesses.
- Expected controller exit 0; actual exit 2; stderr empty. Summary reports
  `DIFFERENTIAL_DIAGNOSTIC_FAILED` and `DIFFERENTIAL_EVIDENCE_VERIFIED`.
- The run has zero uploaded artifacts. Its assertion truncates the CLI summary.
  The failed experiment (A/B/C/D), environment/READY/GO/work/cleanup/seal/
  continuation stage and underlying error cannot be recovered from that log.
  Historical stage: UNKNOWN. Root cause: ROOT_CAUSE_UNCONFIRMED.
- This is not evidence of load, an intermittent timeout, a clock failure or a
  calendar Provider regression. Passing PR CI does not validate failed main CI.

## Bounded Reproduction

Two independent output directories are retained in the external incremental
index under `local-archive/recovery/phase118-main-ci-20261028`:

1. Round 1: exact PS7 normal differential CLI case: 1 passed in 28.01s.
2. Round 2: Phase118 file order, including direct predecessor identity cases:
   60 passed in 202.42s. No reproduction; stop after these two rounds.
3. The new diagnostic redaction/metadata regression separately passed in 0.25s.

These use the existing test-only socket/DNS denial, synthetic time and fixture
workers, not external markets or NTP. Overlapping coverage is not added together.
Local passing reproductions do not establish the historical root cause.

## Diagnostic-Only Change

On the existing unexpected-exit assertion, retain experiment IDs, continuation
decisions, verifier errors, stage/error codes, monotonic stage times and deadlines,
environment identity rejection metadata, cleanup/seal results and original paths.
Do not export authorization bodies, stdout/raw responses or trace payloads.
The test still fails; expected exits, identity checks and all budgets are unchanged.

The CI failure-only artifact step preserves this compact JSON for 14 days.
Successful tests do not generate the report. No production executor, fixture
clock, request plan, worker startup or cleanup semantics are changed.

If the single final CI fails again, inspect its new artifact and stop. Do not
retry until green or infer a cause from a local pass. Any future collection date
requires separate PM approval after review, merge and successful main CI.

Qualification remains 0/3; data_ready=false; hard_gate_authorized=false.
Formal records, research ranking, candidates, tickets and orders remain empty.
