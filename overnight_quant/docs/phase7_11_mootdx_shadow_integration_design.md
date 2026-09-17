# Phase 7.11: mootdx Fixed-Endpoint Qualification and Shadow Integration

## Scope

PM approved three consecutive qualification days: 2026-09-15, 2026-09-16 and
2026-09-17. The approval is deliberately narrow:

- origin source: Tongdaxin;
- adapter: `mootdx` 0.11.7;
- endpoint: `59.36.5.11:7709`;
- endpoint id: `mootdx_locked@59.36.5.11:7709`;
- capabilities: one-minute bars and transactions only;
- validation stocks: `000001,000333,600000,600519,601318`;
- request deadline: 2000ms.

It does not qualify other mootdx endpoints, order books, quotes, finance, F10,
announcements or any Eastmoney capability.

## Qualification Record

The ignored, immutable confirmation record is:

```text
overnight_quant/data/cache/source_qualification/
mootdx_minute_source_qualification_confirmation_2026-09-17.json
SHA-256: 0c772e6a900c87281a0e7c6336f5a66a49d32b7d793f1582b3caf0fd1e694f69
```

The ignored ledger is at `overnight_quant/data/cache/source_qualification/
mootdx_minute_source_qualification_ledger.json` with SHA-256
`5756e572ae2709c88cdcf21c8ab0a6f8e3efa2416241dae3077f95f9a09d16e2`.
Neither file is committed.

| Date | P95 | Raw file SHA-256 | Probe hash | Transaction hash | Combined hash | Reanalysis hash | Verifier |
|---|---:|---|---|---|---|---|---|
| 2026-09-15 | 1286ms | `71c22e01f1c579f7db027760843ad9c4569d9a0af1175dc426aad4c06f7e27cd` | `7d3d9b90f3874f5b1a3b9c5b87ef59e3b0dce743f05f934dabb406955ae149da` | `777d197d2cb6bd9b9974a8b1931053b5647c6a04f3bd52a0ad1b81a1ade06d9c` | `b2503b859121721afa70caf70423bfe46bd48dc02a91b944b4d81b8c77b6602a` | `cd0eba61cf00bb6b74015f38f2b5bdec6110fe8f120eb5c212986d21a52e5e53` | `PROBE_EVIDENCE_VERIFIED` |
| 2026-09-16 | 751ms | `941679570378d21eb65df8d7c93be9fd2e2e037febe2e418961042019a767b39` | `2d8cbd6a6f7d5b95e80ff7b91e28e77ed3b5f6d60237a39be949a02599d95d22` | `2986d66c089837f8f9295c9e74bd7f80ba3faabc3179df2fc87961db55805520` | `0f4a660c228b4faaacad699541a17338c76f62aa61b8cc5ecae902d269977e9a` | `b96c327d05fc95de4ba9a39a729766890310e0f9a327b327d3715a39994dfaa6` | `PROBE_EVIDENCE_VERIFIED` |
| 2026-09-17 | 1454ms | `0ff0eca4711f5222bf7cc93484f2e56fd378c536b94866dc2793b736f42a8cf7` | `365bdadd05b413554e8a7b9d697a39680dff53b5ec93378da7b8895af69c6cbc` | `28bf3b305d4211db13ce314fdd580bb3db7b5238f43f1dddc4a171001ad54bf1` | `432e00d5621efa65ecb2108ab93f34a517131c64b3ece1a26e691763b1e9b088` | `e554cfd9192c9c33f24c7b04c0333b0a037a68324c9eb070faa3489a74230498` | `PROBE_EVIDENCE_VERIFIED` |

Day 1 used a PM-approved manual equivalent Go/No-Go check. No immutable
Go/No-Go JSON exists, and the missing file must never be backfilled.

## Runtime Contract

The production adapter registry binds exactly two explicit Provider keys. The
caller must construct a matching `SourceProviderEnvelope`; importing or auditing
the registry never opens a network connection. The shadow service additionally
requires `--network` and the exact five-stock scope.

The Provider always uses the fixed endpoint and 2000ms timeout. It never scans
nodes, retries, falls back to another source or stitches records. A fixed-endpoint
error yields `MOOTDX_SHADOW_DATA_UNAVAILABLE`, no partial records and no call to
the transaction Provider after a minute-bar failure.

All records retain origin, adapter, source version, event/observation/availability
times, request hash, raw hash, endpoint id and qualification-record hash.

## Readiness and Safety

Qualification only makes these two source identities eligible for the formal
source contract. It does not make a complete close snapshot ready. Market and
industry breadth, quote fields, 60-day qfq bars, news source status and formal
fund flow remain independent gates.

The shadow result therefore always preserves:

```text
automatic_configuration_change=false
data_ready=false
hard_gate_authorized=false
candidates=[]
tickets=[]
orders=[]
```

No broker API, order placement, automatic trading, scheduled task or securities
software control is introduced.
