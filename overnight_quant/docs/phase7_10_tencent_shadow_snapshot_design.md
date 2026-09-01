# Phase 7.10 Tencent Read-Only Shadow Snapshot

## Scope

This phase connects the existing Tencent direct HTTP providers to the existing
production adapter and provenance contracts for display-only use. It does not
define a new evidence schema and does not alter the v4 provider evidence files.

The service reads `quote` and `valuation` only after an explicit `--network`
request or a user click in the Dashboard. It performs one request per
capability, with no retry, fallback, demo data, polling, or default stock pool
outside the caller-provided codes.

## Display Contract

The merged display rows contain code, name, price, PE TTM, PB, total market
capitalization, float market capitalization, source event time, source identity,
and collection time. Quote and valuation must both pass the production adapter
and provenance contracts and cover exactly the same codes.

`snapshot_available=true` means only that the read-only panel has complete data.
It must never imply strategy readiness. Every result keeps:

- `data_ready=false`
- `hard_gate_authorized=false`
- `strategy_integration=false`
- `readiness_integration=false`
- `decision_hash_integration=false`
- empty `candidates`, `tickets`, and `orders`

## Dashboard Behavior

The “Tencent Quote / Valuation” panel has no timer. Opening or refreshing the
Dashboard does not call the service. Only the panel button performs the explicit
network request. The last result is kept in Streamlit session state. Failures
render a yellow “Data not ready” message without fallback.

## Safety Boundary

This is a research/shadow-only view. It does not write strategy inputs, alter
source qualification, generate a decision hash, place an order, or connect to a
broker.
