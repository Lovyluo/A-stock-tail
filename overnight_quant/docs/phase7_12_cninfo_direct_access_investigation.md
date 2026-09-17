# Phase 7.12: CNINFO Direct Access Investigation

## Scope and boundary

This investigation is limited to the official CNINFO announcement endpoint:

`https://www.cninfo.com.cn/new/hisAnnouncement/query`

It does not use a proxy pool, captcha bypass, browser login, persistent
cookies, unofficial mirrors, AKShare, or third-party forwarding. TLS
verification remains enabled. The announcement provider remains a candidate
and is not eligible for a hard gate.

## 2026-09-17 request matrix

Four real requests were made with at least one second between requests. No
proxy environment variables were set and WinHTTP reported direct access.

| Attempt | Path | Result | Content type | Bytes | Raw SHA-256 |
| --- | --- | --- | --- | ---: | --- |
| current POST | system environment | 403 | text/html | 1648 | `5bb1f45ae204f9ec3ae4ba7df439601cf11f6bec1d4b283d605aef33bb5d8606` |
| disclosure warm-up GET | same session | 403 | text/html | 1624 | `b244585d34178220d4ae4330f895956538829c5a2204aa569c98f5392108f809` |
| standard-header POST | warmed session | 403 | text/html | 1635 | `2c882327472d15b0a9bb54b88e5b357d4db47428a830e9eb2c39532df1b21ca6` |
| standard-header POST | explicit direct | 403 | text/html | 1648 | `c1969f82679a862e22d63f7f47eea735e4f7a3d6b2771487a4ae33bce0c59103` |

The responses identify nginx and contain no `Via` header. The same result on
the system-environment and explicit-direct paths, together with the absence of
a configured system proxy, indicates that the HTML 403 originates from the
official CNINFO edge or access-control layer rather than a local proxy. Session
warm-up and normal official-page request headers did not restore access.

The official organization-id contract was retained:

- Shanghai: `gssh0{code}`
- Shenzhen: `gssz0{code}`
- Beijing: `gsbj0{code}`

## Fail-closed result

The stable provider error is `CNINFO_DIRECT_ACCESS_UNAVAILABLE`. A captured
403 response can be integrity-verified from its raw bytes, hash, status,
content type, method, and exact official URL, but it cannot make provider
validation pass. HTML risk-control pages and non-JSON responses never become
announcement records. Cookies and request headers are not persisted in
evidence.

The four previously validated S1 capabilities retain their immutable evidence
and are not collected again. CNINFO remains `candidate/unqualified`.

## Separate source proposals

Future work may separately design and qualify official announcement providers
for the Shanghai Stock Exchange, Shenzhen Stock Exchange, and Beijing Stock
Exchange. Each exchange must have its own identity, contract, evidence, and PM
approval. These proposals are not implemented here and must not be combined to
impersonate CNINFO.

All outcomes remain research-only:

```text
bound_count=4
data_ready=false
hard_gate_authorized=false
candidates=[]
tickets=[]
orders=[]
```
