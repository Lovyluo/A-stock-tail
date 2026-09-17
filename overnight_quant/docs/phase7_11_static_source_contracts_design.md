# Phase 7.11: v0.4.3-S1 Static Source Contracts

## Scope

S1 introduces contract-compatible, read-only providers for five static or
non-tail-critical capabilities. PM approved four providers for explicit-envelope
production routing on 2026-09-18. This does not change snapshot readiness or
connect data to strategy scoring.

| Capability | Origin | Provider state | Network policy |
| --- | --- | --- | --- |
| trading calendar | Tencent | bound, PM approved | explicit provider envelope only |
| 60-day qfq daily bars | Tencent | bound, PM approved | explicit provider envelope only |
| stock news | Eastmoney | bound, PM approved | explicit provider envelope only |
| global news | Eastmoney | bound, PM approved | explicit provider envelope only |
| announcement | CNINFO | candidate, unqualified | never selected for a formal gate |

AKShare remains an unqualified optional wrapper. Tushare and Ashare remain
retired. No wrapper, legacy implementation, or proxy can satisfy a formal
gate.

## Records-first contract

Every record is emitted by the provider with its native identity and contains
`capability`, `origin_source`, `adapter`, `source_version`, `event_time`,
`observed_at`, `available_at`, `decision_cutoff`, `request_hash`, `raw_hash`,
and `payload`. News and announcements also contain `published_at`. The adapter
does not fill or repair these fields.

The calendar is derived from Tencent `sh000001` completed daily rows and names
the SSE/SZSE scope. Weekend, duplicate, malformed, current-day, and fewer than
60 dates fail closed. Qfq rows require both `request_param=qfq` and the
`qfqday` response key, then must match the trusted calendar. Each fixed stock
must have 60 unique completed dates with valid OHLCV.

Eastmoney stock and global news are separate capabilities. A successful empty
response is `AVAILABLE_EMPTY`; transport, parsing, or missing publication time
is failure. Records later than the feature cutoff are excluded. CNINFO records
retain announcement time, id, canonical URL, and `origin_source=cninfo`.

## Evidence

`run_static_source_validation.py` performs no request unless `--network` is
present. Its output is an immutable UTF-8 JSON file below ignored cache. Raw
bytes are base64 encoded and independently hashed. The verifier requires an
external file SHA-256 anchor, rebuilds records from raw responses, checks the
fixed provider and capability registry contracts, and emits a deterministic
replay hash. Re-signing modified derived records cannot pass replay.

All evidence and replay results keep:

```text
automatic_configuration_change=false
data_ready=false
hard_gate_authorized=false
candidates=[]
tickets=[]
orders=[]
```

## 2026-09-17 validation

The immutable evidence validated Tencent calendar, Tencent qfq daily bars,
Eastmoney stock news, and a successful zero-record Eastmoney global-news
response. CNINFO returned HTTP 403, so provider validation is false and S1 is
not eligible for activation. The failed origin is not replaced by AKShare or
another announcement source.

A bounded direct-access investigation is documented in Phase 7.12. Direct,
session-warmed, standard-header, and explicit-direct requests all returned an
official-edge HTML 403. The provider now preserves this response as auditable
failure evidence and emits `CNINFO_DIRECT_ACCESS_UNAVAILABLE`; it still cannot
satisfy validation or any hard gate.

The 2026-09-18 partial approval is evaluated per capability. It promotes the
four validated providers without promoting CNINFO or changing readiness. The
deterministic approval record is documented in Phase 7.13. This PR remains
Draft.
