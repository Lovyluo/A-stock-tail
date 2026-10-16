# T3.0 Complete Industry Candidate (Offline Only)

Baseline: `e3a39004aba4d1bbebc2d08b080239a6d031b65e`.
The production registries, source qualification, readiness, features, worker,
transport and frozen fund-flow contracts are unchanged.

## Scope and Interface

`CompleteIndustryCandidateProvider` accepts only a typed
`CompleteIndustryCandidateEnvelope` with the fixed provider key. Its input is
captured data, not a callable, default transport or network constructor.
It reuses the existing derived-breadth replay provider and the fltt=1 strength
parser. Successful composition emits five **audit records**; formal `records`
and all trading outputs are always empty.

The standalone candidate source version is
`eastmoney_mapping+constituent_breadth+fltt1_strength_candidate_v1`.
Input schema: `complete_industry_captured_batch_v1`.
Contract: `complete_industry_candidate_contract_v1`.
Contract hash: `2206cdd94aed40dcdf88ef2ebc7444f465795d3b5bae4665d6a9bd583a1eda48`.
This identity is deliberately NOT added to the production Registry/Adapter.
The existing industry binding remains an inactive candidate. The public adapter
rejects the new identity without calling it; the old identity cannot be used to
activate this provider either. Production counts remain 32 entries / 8 bound /
7 candidates, with unchanged registry hashes.

## Composition Contract

- Normalize securities with the existing security-identity function. Join
  security -> exact BK -> breadth/strength. Never infer BK from a display name.
- Require five mappings, four unique boards and exactly one strength capture
  per board. The five fixed stocks must actually appear in their board members.
- Mapping, page and strength captures must carry the same batch ID, trade date,
  strict boolean test scope and time-contract hash. Raw request/native dates
  are checked as well: rewriting labels cannot join different trading days.
- Verify URL, HTTP 200, source/transport versions, request/raw hashes, native
  response fields, process boundaries, per-request 2000ms limit and every native
  constituent timestamp. Every child remains in `component_provenance`.
  All roles, including strength, require finite monotonic process duration
  <=2000ms and a completed, non-terminated worker as well as wall-clock bounds.
- Recompute breadth with the existing provider: continuous complete pages,
  stable declared totals, no duplicate members or absent mapped stock. Do not
  turn invalid values into flats; preserve `invalid_count`, zero and negatives.
- Compute strength only as the approved native `f170 / 10**f152` percent value.
  No constituent mean, breadth substitute, constant fill or new relative formula.
- Validate actual client-call monotonic spacing >=500ms per host and global
  process overlap <=2. These are captured client boundaries, not packet timing.
- The aggregate event is the maximum of all validated native component events,
  without minute truncation. Preserve the breadth minute label and raw native
  times separately. The aggregate observation is when its component responses
  have been observed; child observation fields are never repaired or overwritten.
- Mapping data is static classification audit. No mapping native event is
  fabricated from local request time. Its raw response/hash and collection
  timestamps remain in the association and component provenance.
- Versioned aggregate metadata includes `published_at=null` for this non-news
  capability and decision-time `decision_cutoff`, following the existing S2 PIT
  contract. The complete four-stage temporal contract is preserved.
- Any failure returns no audit or formal records. An accepted captured candidate
  is not production admission: the existing non-test industry gate and frozen
  fund-flow gate remain in force.

## Offline CLI and Evidence

```powershell
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_complete_industry_candidate.py --plan --date YYYY-MM-DD
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_complete_industry_candidate.py --input captured-batch.json --expected-file-sha256 EXTERNAL_SHA256 --output replay.json
```

Input JSON contains `provider_key` and `batch`. A batch carries the schema,
batch ID, codes, trade date, test scope, four-stage temporal contract, batch start
and completion, `breadth.request_tree` and `strength.captures`. Each component
and request carries the matching context; each strength item names its BK.
The CLI has no network mode. It requires an external file SHA and atomically
refuses an existing output. Identical input produces byte-identical replay,
including deterministically sorted records/provenance and the candidate hash.
The internal hash is not an upstream signature: external anchors must be kept
outside mutable input. A successful replay is not a qualification decision.

The October 9 breadth and October 15 strength evidence are **separate component
regressions only**. Their original files/hashes are not rewritten or combined.
All positive combined fixtures are explicitly synthetic/test-only. Tests prove
association/materialization/readiness/feature field flow, not real readiness.

## Proposed Same-Round Audit Plan (Not Scheduled)

The existing audit time version is retained: launch 14:37, operational latest
14:39:30, cutoff 14:40, collection deadline 14:41:05, decision 14:41:10.
There is no network runner or sampling authorization in this change.

| Stage | Minimum / maximum requests | Dependencies |
| --- | --- | --- |
| CoreConception mappings | 5 / 5 | Verified calendar/session/clock, fixed codes |
| Exact-BK constituent pages | 4 / 7 | Mappings; each later page depends on the previous page |
| fltt=1 native board strength | 4 / 4 | Corresponding BK constituent pages complete |
| Total | 13 / 16 | No retries or fallback |

Page size remains 100. The existing derived request budget of 12 permits five
mappings plus at most seven pages; therefore one board can use at most four
pages while the other three each need at least one. Additional pages are
conditional reservations, not instructions to fetch unnecessary pages. Freeze
explicit page allowances before a real audit; totals must prove every extra
page is necessary. A need beyond the approved plan stops the batch, not an
automatic expansion to the global limit.

The plan exposes both the existing DAG simulation and a stricter conservative
bound. The latter reserves completion-to-next-launch gaps per host so worker
cold-start variation cannot compress the intended client-call gap:

- Five mapping requests: `5*2000 + 4*500 = 12000ms`.
- Four pages plus four strength calls: `8*2000 + 7*500 = 19500ms`.
- Cleanup and freeze reserve: `1500ms`; total **33000ms**.
- Seven pages plus four strength calls: `11*2000 + 10*500 = 27000ms`;
  worst total **40500ms**.

These conservative bounds deliberately do not divide request count by two.
Two workers and two in-flight requests per host are upper limits, not guaranteed
throughput. Rate waiting/startup/body reading remain inside each original 2000ms
deadline; queue time consumes the global budget. Insufficient remaining budget,
429/access restrictions or a child failure stop dispatch; no allowance is added.

**14:37 is feasible under both bounds. 14:39:30 is not a guaranteed safe start
for completion before the feature cutoff.** The calculated conservative latest
starts are 14:39:27 (single-page case) and 14:39:19.500 (seven-page case).
These are planning findings, not changes to approved runtime parameters. PM
must choose/freeze the next audit plan; there is no automatic launch, extra
worker, deadline extension or new standalone four-BK repeat audit.

## Offline Validation Receipt

- Phase102: 47 passed; affected Registry/Adapter/PIT and component suites:
  336 passed in 47.34s. No local full suite and no source network requests.
- October 9 width component independently reconstructs five rows from nine
  captures; October 15 strength independently reconstructs four board results.
  Original file SHA-256 values remain respectively
  `93a7370819a9b152a7e617ea18d466f8942c03249d1a6bfccf519bbc833bab4b`
  and `b9531580f4020b166f32df6239c59546c2696c20755fa54c4b8cab1ca6890935`.
- Synthetic combined candidate hash:
  `a86fd8e74d6302c8e929cdf85b7cfcff81a6625bf2d4621515a96d97206577fc`.
  Two CLI replay files have the identical SHA-256
  `cbd365038897141f145c668a7f36df6811948ce121f97e258449b795d96d7f23`.
  These are test-only artifacts, not real snapshot evidence.
- Full reproducible offline receipt and recovery manifests are external under
  `D:\Projects\A-stock-tail\local-archive\recovery\v043-t30-20261015`.
  The final commit, Draft PR and CI metadata go to its delivery index, avoiding
  a second code push only to record the document's own commit SHA.

## Stop State

`qualification=0/3`, `data_ready=false`, `hard_gate_authorized=false`,
`provider_contract_complete=false`, all trading outputs empty. Keep the PR Draft;
no merge, release, production activation, source requests or qualification day.
