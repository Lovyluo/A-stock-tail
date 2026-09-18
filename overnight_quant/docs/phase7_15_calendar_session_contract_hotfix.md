# Phase 7.15 Calendar and Current Session Contract Hotfix

## Scope

This hotfix changes only the S2 pre-sampling Go/No-Go contract. It does not
collect S2 formal data, create tasks, change qualification thresholds, or
activate any source.

## Two independent contracts

`completed_calendar_contract` preserves the approved S1 meaning:

- `trade_dates` is sorted and unique;
- every date is strictly before `target_trade_date`;
- `latest_completed_trade_date` is the last item;
- source, source version, S1 qualification record, raw hash, and external file
  SHA-256 must validate.

The target day is never inserted into the completed-date list.

`market_session_confirmation_v1` uses only the production-bound Tencent quote
provider. A confirmation is valid only when the exact five fixed stocks are
present and every provider-native event belongs to the target date, is no
earlier than 09:30 China time, and satisfies:

```text
event_time <= observed_at <= available_at
```

The provider key, source identity, source version, request hash, raw response
hash, production registry selection, evidence hash, and external file SHA-256
are all verified. Prices and valuations are retained only as original provider
evidence and are not consumed by S2 scoring.

## Go/No-Go v2

Evidence v2 records separate references for the completed calendar and current
session confirmation. `SAMPLING_GO` is possible only when both independently
verify and every existing environment check passes. It authorizes at most one
sampling attempt and always leaves qualification as `NOT_EVALUATED` with a
consecutive count of zero.

Legacy v1 evidence is audit-only. Even a historically valid v1 file returns
`sampling_authorized=false` under the current verifier.

## Commands

Current-session evidence requires explicit networking:

```powershell
python overnight_quant/scripts/run_market_session_confirmation.py `
  --network --date YYYY-MM-DD `
  --codes 000001,000333,600000,600519,601318 `
  --output <ignored-cache-path>
```

The Go/No-Go command requires the completed-calendar file, current-session
file, and an external SHA-256 anchor for each. All evidence writes are atomic
and refuse overwrite.

## Safety state

Every path keeps:

```text
qualification=0/3
data_ready=false
hard_gate_authorized=false
candidates=[]
tickets=[]
orders=[]
```
