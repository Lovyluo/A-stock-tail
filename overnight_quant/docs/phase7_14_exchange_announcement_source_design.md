# Phase 7.14 Official Exchange Announcement Candidate Sources

## Scope

This phase adds read-only candidate providers for official listed-company
announcements from the Shanghai, Shenzhen, and Beijing stock exchanges. It does
not activate any production source. CNINFO remains unqualified and unchanged.

The fixed-five validation scope routes `600000`, `600519`, and `601318` to SSE,
and `000001` and `000333` to SZSE. BSE uses `920925` only to validate the BSE
source; it is not added to the fixed-five snapshot.

## Official endpoints

| Source | Endpoint | Official document host |
|---|---|---|
| SSE | `https://query.sse.com.cn/security/stock/queryCompanyBulletinNew.do` | `static.sse.com.cn` |
| SZSE | `https://www.szse.cn/api/disc/announcement/annList` | `disc.static.szse.cn` |
| BSE | `https://www.bse.cn/disclosureInfoController/companyAnnouncement.do` | `www.bse.cn` |

BSE performs a read-only GET of the official announcement page before the API
POST. Its session cookie stays only in the in-memory cookie jar and is never
persisted or emitted. No provider uses a proxy pool, browser UI, CAPTCHA bypass,
third-party relay, or unofficial mirror.

## Contract

Each provider handles only codes belonging to its exchange and emits one source
identity per evidence file. Required record fields include code, announcement
identifier, title, publication time, official document URL, point-in-time
timestamps, source version, request hash, and raw response hash. The adapter
layer does not repair missing fields or identities.

Announcements published after the feature cutoff are excluded. A successful
response with no qualifying announcements is `AVAILABLE_EMPTY`. HTTP failures,
HTML risk-control pages, invalid JSON, missing publication time, non-official
redirects, route mismatches, duplicates, and hash drift fail closed for the
entire source batch.

Evidence files are strict UTF-8 JSON written atomically with no overwrite. The
verifier requires an external file SHA-256, reconstructs records from the raw
response bytes, and compares request, response, record, and evidence hashes.
Each exchange is verified independently; success for one exchange cannot satisfy
another exchange.

## Publication-time precision contract

The official raw-field investigation found:

| Source | Raw field | Observed value | Precision conclusion |
|---|---|---|---|
| SSE | `SSEDATE` | `2026-09-18` | date only |
| SZSE | `publishTime` | `2026-09-17 00:00:00` | date semantics; midnight padding does not prove a publication clock |
| BSE | `publishDate` | `2026-09-17` | date only |

No provider may infer a time from retrieval order, page order, another source,
or a local clock. A non-midnight time component must be present in the official
raw field before `published_at_precision=datetime` is allowed.

For evidence v2, a date-only announcement from the target trade date is excluded
from formal `records` and retained in `audit_records` with the stable reason
`publication_time_precision_insufficient`. A date-only announcement strictly
before the target trade date remains eligible and keeps
`published_at_precision=date`. A datetime record is eligible only when its
official time is strictly earlier than the feature cutoff; equality and later
times are audit-only rejections.

Evidence v1 remains verifiable under its original contract and hash. The v2
reanalysis command reads immutable v1 raw responses, records the source file SHA
and evidence hash, and produces a new non-overwriting derived evidence file.

## Registry state

- Capability registry schema: `source_capability_registry_v4`
- Capability entries: 32
- Capability registry hash:
  `eba7d83802c164ff747d0271d2b799724bbc15ee9886c031b3db59511f8d15ed`
- Adapter registry schema: `source_capability_adapter_registry_v7`
- Adapter registry hash:
  `3cdaaaeaa6de72ae7db79fa6a7e0e7b9fb408b9e4f893f817eaa36b1560a854a`
- `bound_count=8`
- `candidate_count=7` (CNINFO, SSE, SZSE, BSE, and the three S2 market candidates)

The three exchange providers remain `candidate_not_activated / unqualified`.
Production adapter execution therefore refuses to call them even when an exact
provider envelope is supplied.

## Safety boundary

All outputs retain `data_ready=false`, `hard_gate_authorized=false`, and empty
`candidates`, `tickets`, and `orders`. This phase does not modify strategy
scoring, readiness thresholds, trading capability, or scheduled tasks.
