# Phase 4.1.1 Morning Previous-Close Replay Design

## Status And Purpose

Phase 4.1 generated a formal next-morning observation pool only when the
after-close command was run after the prior session. Phase 4.1.1 adds a
conservative catch-up path for a missed after-close run:

```text
current trading-day pre-market invocation
  -> resolve prior likely trading weekday
  -> fetch data explicitly dated to that prior close
  -> apply the existing after-close observation score and A/B/C rules
  -> write a morning replay report and watchlist for today
```

This path produces an observation plan only. It does not generate a manual
ticket, give a transaction instruction, record orders, interact with broker
software, or automate trading.

## Confirmed User Decision

Use an explicit replay option on the existing read-only after-close command:

```text
python overnight_quant/scripts/run_after_close_analysis.py \
  --mode live \
  --replay-previous-close
```

The replay output uses separate filenames from the original after-close
output. A morning catch-up must not overwrite a report that was generated
properly on the prior evening.

## Scope And Non-Goals

Phase 4.1.1 may extend:

```text
overnight_quant/data/astock_client.py
overnight_quant/data/live_data_quality.py
overnight_quant/data/market_calendar.py
overnight_quant/strategy/after_close_analysis.py
overnight_quant/reports/after_close_report.py
overnight_quant/scripts/run_after_close_analysis.py
overnight_quant/tests/test_phase41_after_close_analysis.py
overnight_quant/config.yaml
overnight_quant/README.md
overnight_quant/RELEASE_NOTES.md
```

It does not alter:

```text
run_scan.py
run_sell_plan.py
run_record_order.py
run_trade_review.py
run_backtest.py
manual_ticket.py
order_recorder.py
position_tracker.py
```

It does not implement:

- automatic orders, automatic clicks, broker API integration, or a GUI;
- buy tickets, sell plans, order records, or position changes;
- a new strategy, scoring threshold change, or risk-rule relaxation;
- use of live-to-demo fallback as a formal watchlist;
- use of current-day intraday values to fill prior-close analysis fields.

## Selected Architecture

### Command Path

The existing read-only command gains one opt-in path:

```text
run_after_close_analysis.py --mode live --replay-previous-close
  -> config_for_mode("live")
  -> replay context: observation_date + replay_as_of_date
  -> AStockClient(..., data_context="previous_close_replay",
                  expected_data_date=replay_as_of_date)
  -> AfterCloseAnalyzer(..., analysis_mode="previous_close_replay")
  -> after_close_report writers using morning_replay filenames
```

Normal Phase 4.1 commands retain their present meaning:

```text
--mode demo                 deterministic after-close demonstration
--mode live                 after-close-only live observation analysis
--mode live --replay-previous-close
                            pre-market catch-up from the prior close
```

The replay path reuses the Phase 4.1 scoring formula, classifications, caps,
quality labels, observation-plan templates, and no-execution boundaries. It
must not call `YangYongxingOvernightStrategy.scan()`.

### Why Not A Second Script

A separate `run_morning_replay.py` would duplicate state isolation, report
serialization, and category semantics. A controlled option on the existing
observation command makes the missing-run recovery behavior explicit while
keeping one watchlist analysis contract.

### Why Not Simulate Yesterday's Clock

The implementation must not pretend the current process ran yesterday after
close. The report needs both when it was run and which close it replays, so
users can distinguish a timely after-close output from a morning recovery
output.

## Date And Session Semantics

### Date Fields

Replay results expose:

```text
analysis_mode: previous_close_replay
observation_date: 2026-05-28
replay_as_of_date: 2026-05-27
replay_calendar: weekday_proxy
run_time: 2026-05-28T08:45:00+08:00
```

Definitions:

- `observation_date` is the current day for which the watchlist is intended.
- `replay_as_of_date` is the prior likely China trading weekday whose close
  supplies the candidate facts.
- `replay_calendar` remains `weekday_proxy`; exchange holidays are not yet
  resolved from an official calendar.

### Allowed Window

The replay mode is intentionally narrow. Formal replay rows may be generated
only when:

```text
current date is a likely China trading day
session_state in {PRE_MARKET, CALL_AUCTION}
```

The replay window ends at continuous trading open. This prevents a
prior-close watchlist from being confused with a list informed by current
intraday action.

### Replay Status Values

The replay path adds these statuses:

| Condition | Status | Valid For Trading Observation | CSV Rows |
| --- | --- | --- | --- |
| Pre-market or call auction, target-dated data usable, classified rows exist | `MORNING_REPLAY_READY` | `YES` | A/B/C |
| Pre-market or call auction, target-dated data usable, no rows qualify | `MORNING_REPLAY_NO_WATCHLIST` | `YES` | Header only |
| Non-trading day or outside pre-market/call auction | `NOT_REPLAY_WINDOW` | `NO` | Header only |
| Any candidate or K-line path falls back to demo | `REPLAY_DATA_FALLBACK_DEMO` | `NO` | Header only |
| Required dated data, freshness certainty, or safety certainty fails | `REPLAY_DATA_QUALITY_BLOCKED` | `NO` | Header only |

Original Phase 4.1 statuses remain unchanged for non-replay execution.

## Strict Previous-Close Data Context

### Core Rule

The existing live client treats values dated before today as stale, which is
correct for real-time scan behavior. Replay introduces a separate,
explicitly identified freshness policy:

```text
data_context: previous_close_replay
expected_data_date: replay_as_of_date
freshness_basis: previous_close_expected
```

Under this policy, a dated value is acceptable only when:

```text
data_date == replay_as_of_date
```

For accepted rows, the client must record that they are not current-day live
prices:

```text
candidate_source: live_previous_close_replay
freshness_basis: previous_close_expected
```

This is not a global relaxation of `quote_stale`. The ordinary live scan and
ordinary after-close paths continue rejecting stale quotes as they do now.

### Source Requirements

| Field Group | Replay Source Requirement | If Not Satisfied |
| --- | --- | --- |
| Candidate seed and theme tags | THS hot-reason query for exactly `replay_as_of_date`; no search backward to another day | Quality blocked or no rows |
| Price/change/turnover/amount/volume ratio/limits | Tencent quote row accepted only if its embedded date is exactly `replay_as_of_date` | `REPLAY_DATA_QUALITY_BLOCKED` |
| Daily trend K-line | Baidu last daily K-line row must be dated exactly `replay_as_of_date` | `REPLAY_DATA_QUALITY_BLOCKED` for selectable rows |
| Stable metadata | Existing Eastmoney metadata/cache may supply name and listing date because listing date is stable metadata | Missing safety field blocks selectable rows |
| Northbound/market narrative | Use only if explicitly dated to `replay_as_of_date`; otherwise mark unavailable | Lower/disclose market quality, or block if no usable market view |
| Fund flow | Do not call minute current-day flow in replay; accept only a dated daily row matching `replay_as_of_date`, or use THS target-day big-order value as an estimated auxiliary field | `capital_missing` or `estimated_capital_flow`, never confirmed flow |

Replay must never use:

- Tencent current-day movement after continuous trading starts;
- current THS hot reasons as a substitute for the replay day;
- current minute fund flow to represent yesterday;
- demo or positive fixtures after a live source failure.

### Date Mismatch Rules

| Condition | Quality Flag / Reason | Formal Rows |
| --- | --- | --- |
| `data_date == replay_as_of_date` | `previous_close_expected` | May continue |
| `data_date < replay_as_of_date` | `replay_data_too_old` | Block |
| `data_date > replay_as_of_date` | `replay_data_from_observation_day` | Block |
| Timestamp absent | `timestamp_missing`, `freshness_unknown` | Block |
| Live source falls back to demo | `demo_fallback` | Block |

The quality report must make source dates visible so that an output is
auditable without reading implementation code.

## Analyzer Behavior

`AfterCloseAnalyzer` receives an explicit `analysis_mode` rather than
inferring replay from the clock:

```text
analysis_mode: after_close | previous_close_replay
```

For `after_close`, its existing session and quality behavior remains
unchanged.

For `previous_close_replay`:

1. Resolve `observation_date` and `replay_as_of_date`.
2. Apply the replay-window gate before collecting candidates.
3. Collect only target-dated prior-close inputs.
4. Apply the existing independent Phase 4.1 observation score and A/B/C
   category caps unchanged.
5. Block formal rows if demo fallback, mismatched/unknown timestamps, or
   safety uncertainty affects an otherwise selectable A/B row.
6. Return observation-only conditions; never emit a ticket or action.

The first release uses the existing Phase 4.1 treatment for optional missing
theme and capital fields: disclose and reduce scoring. Safety/freshness
uncertainty remains blocking.

## Output Contract

### File Isolation

Replay outputs go to live state directories but use separate names:

```text
overnight_quant/reports/morning_replay_analysis_YYYY-MM-DD.md
overnight_quant/records/morning_replay_watchlist_YYYY-MM-DD.csv
```

The date in the filename is `observation_date`, the morning on which the list
is intended for observation. This avoids overwriting:

```text
after_close_analysis_<prior-close-date>.md
next_morning_watchlist_<prior-close-date>.csv
```

### Markdown Report Fields

The replay Markdown report includes at least:

```text
# Morning Previous-Close Replay Analysis Report

analysis_mode: previous_close_replay
observation_date:
replay_as_of_date:
replay_calendar: weekday_proxy
run_time:
status:
session_state:
candidate_source: live_previous_close_replay | demo_fallback
freshness_basis: previous_close_expected | blocked
valid_for_trading_observation: YES | NO
```

It retains the Phase 4.1 sections:

```text
Market Environment
Leading Themes
A Class Priority Observation
B Class Backup Observation
C Class Risk Observation / Do Not Chase
Next-Morning Overall Playbook
Data Quality
Risk Warning
```

The report must prominently state:

```text
This watchlist was reconstructed during the morning from prior-close dated
data. It is not current intraday confirmation, is not investment advice, and
does not place or automate orders.
```

### CSV Contract

Replay CSV retains the Phase 4.1 observation-row fields and adds explicit
lineage fields:

```text
analysis_mode
observation_date
replay_as_of_date
freshness_basis
```

For the replay path, `trade_date` in legacy watchlist columns remains the
source close date (`replay_as_of_date`) and `next_trade_date` equals
`observation_date`. This maintains the original contract: rows describe a
close and the following morning to observe.

Only `MORNING_REPLAY_READY` writes A/B/C rows. Replay block/no-watchlist
states write a fixed header with no stock rows.

## Data Quality Report

The replay report must expose:

```text
expected_data_date: <replay_as_of_date>
freshness_basis: previous_close_expected
target_date_match_count:
target_date_mismatch_count:
timestamp_missing_count:
fallback_status:
source_status:
data_quality_flags:
estimated_fields:
```

Where a target-day big-order field is used to estimate a capital score, the
existing disclosure remains mandatory:

```text
Capital-flow values marked estimated are auxiliary observation inputs only,
not confirmed capital inflow.
```

## CLI Behavior

Supported invocation:

```text
python overnight_quant/scripts/run_after_close_analysis.py \
  --mode live \
  --replay-previous-close
```

Console output includes:

```text
Mode: live
Analysis Mode: previous_close_replay
Status:
Session State:
Observation Date:
Replay As Of Date:
Candidate Source:
Freshness Basis:
Valid For Trading Observation:
A Count:
B Count:
C Count:
Report:
Watchlist CSV:
```

`--replay-previous-close` with `--mode demo` is rejected clearly:

```text
REPLAY_REQUIRES_LIVE_MODE
```

This keeps demo deterministic and avoids presenting a teaching fixture as a
prior-close live reconstruction.

## Failure And Safety Handling

- No replay candidate source silently scans backward past the expected prior
  close.
- Any live-to-demo fallback is reported and produces no formal replay rows.
- Any prior-close date mismatch or missing timestamp for selectable rows
  produces no formal replay rows.
- Safety unknown fields retain the existing conservative block.
- Output directories continue to follow `config_for_mode("live")`; examples
  are never read or written by replay.
- A failed replay does not alter an existing after-close watchlist.

## Test Plan

The implementation plan must add deterministic tests covering:

1. Non-replay Phase 4.1 after-close behavior remains passing.
2. `--mode demo --replay-previous-close` rejects with
   `REPLAY_REQUIRES_LIVE_MODE`.
3. Replay on a non-trading day returns `NOT_REPLAY_WINDOW` and header-only
   CSV.
4. Replay after continuous trading begins returns `NOT_REPLAY_WINDOW` and
   header-only CSV.
5. Pre-market and call-auction replay with all source dates exactly matching
   the previous likely weekday can produce `MORNING_REPLAY_READY`.
6. Replay report and CSV use `morning_replay_*_<observation_date>` names and
   do not overwrite the after-close files.
7. Report includes observation date, replay-as-of date, weekday-proxy
   disclosure, source, and `previous_close_expected` freshness basis.
8. THS target-date mismatch or backward-search substitute is blocked.
9. Tencent timestamp earlier or later than the target close is blocked.
10. Baidu K-line last-row mismatch or timestamp missing blocks otherwise
    selectable candidates.
11. Replay does not call current-minute fund flow and does not fill missing
    prior-day capital data from current values.
12. Target-day estimated capital remains labeled auxiliary only.
13. Demo fallback and safety-field uncertainty generate no formal rows.
14. No manual ticket is created; new/modified production modules contain no
    automatic order, click, broker execution, or GUI behavior.
15. All existing tests remain passing.

## Implementation Boundary

After this specification is approved in written form, the implementation
plan should proceed with narrow TDD tasks:

```text
replay date/window/output contract tests
  -> replay result and writer shape
  -> strict expected-date data context in the read-only client
  -> data-quality/fallback/safety blocking tests
  -> CLI documentation and full regression verification
```

No implementation is authorized by this specification alone.
