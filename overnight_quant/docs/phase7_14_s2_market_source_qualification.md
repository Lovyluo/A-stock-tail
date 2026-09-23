# Phase 7.14: S2 Market Source Qualification

## Scope

S2 adds three Eastmoney direct-HTTP candidates without changing close-confirmation
readiness, strategy weights, or trading behavior.

| Capability | Provider | Production state | Qualification |
| --- | --- | --- | --- |
| market breadth | Eastmoney SSE/SZSE/BSE index breadth plus Eastmoney SSE index | candidate | unqualified, 0/3 |
| industry mapping and breadth | Eastmoney stock industry plus industry board clist | candidate | unqualified, 0/3 |
| minute fund flow | Eastmoney push2 fflow kline | candidate | unqualified, 0/3 |

Sina fund flow remains proxy/audit-only. It cannot replace or complete an
Eastmoney batch. None of the three candidates is executable through the public
production adapter path before PM approves three consecutive complete trading
days.

## Field contracts

The market record uses `origin_source=eastmoney` for both components. The fixed
benchmark is SSE Composite (`secid=1.000001`). Market breadth uses Eastmoney
index breadth counters for SSE Composite, SZSE Component, and BSE 50
(`secids=1.000001,0.399001,0.899050`), versioned as
`eastmoney_index_breadth_sh_sz_bj_v2026-09-21`. The clist endpoint currently
caps returned rows even when a larger `pz` is requested, so it is not used to
pretend a first page is the full stock pool.

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
formula as market breadth. The industry board update time is the formal event
time for breadth. The stock-to-industry mapping is a static classification
observed before the collection deadline; its quote update timestamp is retained
for audit, but it cannot move a valid 14:50 board breadth record into a later
decision minute. This timing contract is identified as
`push2_stock_industry+board_breadth_v2026-09-23`; the previous 2026-09-18
identity is not interchangeable with it.

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
day. `market_source_evidence_v2` is bound to
`market_source_verifier_v2` (`220cdd293f64f91b6fb726bccf36cbc51633ccfae6f8cdbdaffb918f6e85cea4`).
The verifier recomputes the three-market breadth counts, event-minute labels,
industry board timing, request identities, and raw response hashes. Legacy v1
evidence remains externally anchored audit evidence only and always reports
`provider_validation_passed=false` and `qualification_eligible=false`.

## Independent Go/No-Go evidence

`run_market_source_go_nogo.ps1` is a pre-sampling environment gate. It does not
request or require 14:50 formal records. Evidence v2 requires two independently
anchored contracts: the S1 Tencent calendar still contains completed dates
strictly before the target day, while a separate Tencent quote confirmation
proves that all five fixed stocks have current-day events after the morning
session opened. It also checks China Standard Time and clock skew, the three
candidate identities and source versions, DNS/TLS/official endpoint
reachability, applicable proxy-listener readiness, output writability and
immutability, and the absence of residual workers, duplicate tasks, or similar
collectors. The current time must not be later than the configured cutoff.

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
qualification record. The current-session confirmation must use the bound
Tencent quote provider and exact production envelope. Both input files require
external SHA-256 anchors; self-signed files are not sufficient. Legacy v1
Go/No-Go evidence remains verifiable for audit, but can never authorize a new
sample.

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
capability_registry_hash=eba7d83802c164ff747d0271d2b799724bbc15ee9886c031b3db59511f8d15ed
adapter_registry_schema=source_capability_adapter_registry_v7
adapter_registry_hash=3cdaaaeaa6de72ae7db79fa6a7e0e7b9fb408b9e4f893f817eaa36b1560a854a
bound_count=8
candidate_count=7
consecutive_count=0/3
data_ready=false
hard_gate_authorized=false
candidates=[]
tickets=[]
orders=[]
```

The official-announcement source work was merged normally from `main`. The
combined production matrix was rebuilt from its actual entries and contains
exactly 32 entries, 8 bound providers, and 7 inactive candidates. The three S2
candidates remain unqualified at 0/3; announcement candidates do not satisfy
any S2 readiness gate.
