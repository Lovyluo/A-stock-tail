# Phase 7.14: S2 Market Source Qualification

## Scope

S2 adds three Eastmoney direct-HTTP candidates without changing close-confirmation
readiness, strategy weights, or trading behavior.

| Capability | Provider | Production state | Qualification |
| --- | --- | --- | --- |
| market breadth | Eastmoney all-A clist plus Eastmoney SSE index | candidate | unqualified, 0/3 |
| industry mapping and breadth | Eastmoney stock industry plus industry board clist | candidate | unqualified, 0/3 |
| minute fund flow | Eastmoney push2 fflow kline | candidate | unqualified, 0/3 |

Sina fund flow remains proxy/audit-only. It cannot replace or complete an
Eastmoney batch. None of the three candidates is executable through the public
production adapter path before PM approves three consecutive complete trading
days.

## Field contracts

The market record uses `origin_source=eastmoney` for both components. The fixed
benchmark is SSE Composite (`secid=1.000001`). The all-A stock pool is
`m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23` and is versioned as
`eastmoney_all_a_m0t6_80_m1t2_23_v1`.

```text
valid_count = up_count + down_count + flat_count
total_count = valid_count + invalid_count
breadth_ratio = up_count / valid_count
```

The provider rejects a partial stock pool, an internally inconsistent count,
or a missing Eastmoney benchmark timestamp. It never combines a Tencent index
with Eastmoney breadth.

Industry mapping and industry breadth both use the Eastmoney industry
classification `eastmoney_industry_m90t2_v1`. Every fixed stock must map by an
exact industry name to one board row. Industry breadth uses the same count
formula as market breadth.

Fund-flow values are denominated in CNY. Each row describes one minute interval
(`value_semantics=minute_interval_net_flow`,
`aggregation_semantics=incremental`). Every fixed stock must have an exact
14:50 event row. A missing stock or a later completion rejects the entire
provider batch.

## Timing and network boundary

Providers have no default network construction in the adapter registry. The
validation command requires `--network`, runs each provider batch in a killable
child process, and enforces an absolute collection deadline. There is one
attempt only: no retry, fallback, demo data, or cross-source stitching.

Raw responses and evidence are written only to ignored cache using exclusive,
atomic UTF-8 creation. Verification requires an externally supplied file
SHA-256 and reconstructs all normalized records from captured raw bytes.
Failure evidence can pass integrity verification, but cannot become a qualified
day.

## Independent Go/No-Go evidence

`run_market_source_go_nogo.ps1` is a pre-sampling environment gate. It does not
request or require 14:50 formal records. It checks a SHA-anchored Tencent
trading-calendar contract, China Standard Time and clock skew, the exact five
codes, the three candidate identities and source versions, DNS/TLS/official
endpoint reachability, applicable proxy-listener readiness, output
writability and immutability, and the absence of residual workers, duplicate
tasks, or similar collectors. The current time must not be later than the
configured cutoff.

The result is strict UTF-8 JSON created atomically without overwrite. A
separate verifier requires the external file SHA-256. Evidence integrity and
sampling authorization are intentionally separate: `SAMPLING_GO` only permits
one qualification sample attempt and never changes the consecutive-day count.
Any failed check produces `NO_GO_FOR_QUALIFICATION_SAMPLE`; neither result
creates or enables a scheduled task. A later formal collection failure still
leaves the day unqualified.

Environment fixtures are accepted only behind an explicit test-mode process
guard. Their evidence is marked `test_only` and cannot authorize a production
sample. Production calendar checks must also reference the approved S1 partial
qualification record; a self-signed calendar file and caller-supplied file
hash are not sufficient on their own.

## Qualification gate

Each qualifying day must have all three providers complete before the deadline,
cover all five fixed stocks where applicable, and contain no timeout, late,
wrong-window, source-identity, unit, or provenance failure. Qualification
requires three consecutive trusted trading days. A failed day resets the
sequence; code never updates the count automatically.

The initial implementation deliberately does not run a real validation on
2026-09-18. The provider contracts and targeted tests were not treated as
sufficient evidence for an intraday Go/No-Go, and no hurried sampling was
started. Current state remains:

```text
capability_registry_schema=source_capability_registry_v4
capability_registry_hash=6e6403689be62f1676d6def518e810fd36af8650ca86e2fac3961bf18e078585
adapter_registry_schema=source_capability_adapter_registry_v6
adapter_registry_hash=23a8543dc7e4eae4e53a68fec3c2cc58b1cbc585ef35cf79505bf6eca632819e
bound_count=8
candidate_count=4
consecutive_count=0/3
data_ready=false
hard_gate_authorized=false
candidates=[]
tickets=[]
orders=[]
```

The independent S2 work remains blocked from combined registry integration
until the separate official-announcement PR is approved and merged. After
that merge, this branch must merge `main` normally, recompute the combined
production hashes, and verify the expected 32 entries, 8 bound entries, and 7
candidates. No future combined hash is hard-coded here.
