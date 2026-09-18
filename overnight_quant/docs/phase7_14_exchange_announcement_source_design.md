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

## Registry state

- Capability registry schema: `source_capability_registry_v4`
- Capability entries: 31
- Capability registry hash:
  `a5ff522cb0f26464754c1c3f69af39f65d55083dcbbe49d3af20d1201049fada`
- Adapter registry schema: `source_capability_adapter_registry_v6`
- Adapter registry hash:
  `87e9fa55448a97857c9de18aa3e460fa55ea01e7a5bde925e342922c14c5964a`
- `bound_count=8`
- `candidate_count=4` (CNINFO plus SSE, SZSE, and BSE)

The three exchange providers remain `candidate_not_activated / unqualified`.
Production adapter execution therefore refuses to call them even when an exact
provider envelope is supplied.

## Safety boundary

All outputs retain `data_ready=false`, `hard_gate_authorized=false`, and empty
`candidates`, `tickets`, and `orders`. This phase does not modify strategy
scoring, readiness thresholds, trading capability, or scheduled tasks.
