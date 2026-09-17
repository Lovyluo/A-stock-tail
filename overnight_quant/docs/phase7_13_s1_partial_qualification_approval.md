# Phase 7.13: S1 Partial Qualification Approval

## Decision

PM approved four read-only S1 providers on 2026-09-18:

- Tencent trading calendar;
- Tencent 60-day qfq daily bars;
- Eastmoney stock news;
- Eastmoney global news.

CNINFO announcements were not approved. The production registry keeps:

```text
qualification_status=unqualified
implementation_status=candidate_not_activated
selected_source=null
```

## Evidence binding

The deterministic record binds this decision to:

- evidence hash: `235765b8be0189221859a9b6679a2e7b8e4c894e991e1c192acb0d34ed19725d`;
- evidence file SHA-256: `21e07487ce3012b175e48bc0622485c0afdb40e83cd9efe201281c2d6c18a01d`;
- replay hash: `52e81b02e3a51c8ce7e9bf8fe566e51696828dc4d0154da60ba9d4df5a88e92b`;
- qualification record hash: `11a672170abed9b5f18c1f56109f290aabd160a55f7921897afc9f962204872a`.

The old evidence file is not rewritten. Its previous capability-registry hash
is accepted only when the immutable evidence hash and external file SHA match
the approved record. Re-signed or substituted evidence remains invalid.

## Routing boundary

The four approved bindings have no default provider constructor. A caller must
provide the exact `SourceProviderEnvelope` matching the fixed provider key.
Successful empty global-news data is a valid upstream result. Network,
parsing, identity, provenance, or envelope failures close without demo or
fallback data.

CNINFO cannot be promoted by returning a forged success record because the
candidate is rejected before its callable runs. Eastmoney news, mootdx F10,
and AKShare do not substitute for announcements.

The resulting audit state is:

```text
capability_registry_schema=source_capability_registry_v3
capability_registry_hash=4f63ab273dc8cc98363d7043a47fad10f6dcb002a006a4c88cab118c3ff4ecb5
adapter_registry_schema=source_capability_adapter_registry_v5
adapter_registry_hash=05adafdb3e3a70ddcdc16644673b299b66d8c09367d2ef3259b7fc97b7adb369
bound_count=8
candidate_count=1
network_requests_made=0
data_ready=false
hard_gate_authorized=false
candidates=[]
tickets=[]
orders=[]
```
