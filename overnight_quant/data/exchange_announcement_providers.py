from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from http.cookiejar import CookieJar
import hashlib
import json
import re
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import parse_cn_datetime, stable_hash


EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_V1 = "exchange_announcement_evidence_v1"
EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_VERSION = (
    "exchange_announcement_evidence_v2"
)
EXCHANGE_ANNOUNCEMENT_VERIFIER_CONTRACT_V1_HASH = (
    "e3c359ac0bf49cebc09846fee507e7fbf9c3e68fbcf9e448d54ce52384b035e5"
)
EXCHANGE_ANNOUNCEMENT_VERIFIER_CONTRACT_VERSION = (
    "exchange_announcement_verifier_v2"
)
PUBLICATION_TIME_PRECISION_INSUFFICIENT = (
    "publication_time_precision_insufficient"
)

SSE_ANNOUNCEMENT_URL = (
    "https://query.sse.com.cn/security/stock/"
    "queryCompanyBulletinNew.do"
)
SZSE_ANNOUNCEMENT_URL = (
    "https://www.szse.cn/api/disc/announcement/annList"
)
BSE_LANDING_URL = "https://www.bse.cn/disclosure/announcement.html"
BSE_ANNOUNCEMENT_URL = (
    "https://www.bse.cn/disclosureInfoController/companyAnnouncement.do"
)

SOURCE_IDENTITIES = {
    "sse": (
        "sse",
        "direct_http",
        "sse_query_company_bulletin_new_v2026-09-18",
    ),
    "szse": (
        "szse",
        "direct_http",
        "szse_ann_list_v2026-09-18",
    ),
    "bse": (
        "bse",
        "direct_http",
        "bse_company_announcement_v2026-09-18",
    ),
}

PROVIDER_KEYS = {
    "sse": (
        "exchange_announcement_providers.ExchangeAnnouncementProviders."
        "collect_sse_records"
    ),
    "szse": (
        "exchange_announcement_providers.ExchangeAnnouncementProviders."
        "collect_szse_records"
    ),
    "bse": (
        "exchange_announcement_providers.ExchangeAnnouncementProviders."
        "collect_bse_records"
    ),
}

OFFICIAL_RESPONSE_HOSTS = {
    "sse": "query.sse.com.cn",
    "szse": "www.szse.cn",
    "bse": "www.bse.cn",
}
OFFICIAL_DOCUMENT_HOSTS = {
    "sse": {"static.sse.com.cn"},
    "szse": {"disc.static.szse.cn"},
    "bse": {"www.bse.cn"},
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/124 Safari/537.36"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CODE_PATTERN = re.compile(r"^[0-9]{6}$")


class ExchangeAnnouncementContractError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        response_evidence: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.response_evidence = (
            dict(response_evidence) if response_evidence is not None else None
        )


@dataclass(frozen=True)
class ExchangeHttpResponse:
    content: bytes
    status_code: int
    url: str
    headers: Mapping[str, str]


@dataclass(frozen=True)
class ExchangeAnnouncementBatch:
    source: str
    status: str
    records: tuple[dict[str, Any], ...]
    audit_records: tuple[dict[str, Any], ...]
    responses: tuple[dict[str, Any], ...]


class ExchangeTransport(Protocol):
    request_count: int

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
        data: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
        json_body: Mapping[str, Any] | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> ExchangeHttpResponse:
        ...


class ExchangeUrllibTransport:
    """Official HTTP transport with an in-memory, non-persistent cookie jar."""

    def __init__(self) -> None:
        self.request_count = 0
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
        data: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
        json_body: Mapping[str, Any] | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> ExchangeHttpResponse:
        if params:
            url = f"{url}?{urlencode(_pair_sequence(params), doseq=True)}"
        body: bytes | None = None
        if data is not None:
            body = urlencode(_pair_sequence(data), doseq=True).encode("utf-8")
        elif json_body is not None:
            body = json.dumps(
                json_body,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        self.request_count += 1
        request = Request(url, data=body, headers=dict(headers), method=method)
        with self._opener.open(request, timeout=timeout_seconds) as response:
            return ExchangeHttpResponse(
                content=response.read(),
                status_code=int(response.status),
                url=str(response.geturl()),
                headers=dict(response.headers.items()),
            )


class ExchangeAnnouncementProviders:
    def __init__(
        self,
        codes: Iterable[str],
        *,
        feature_cutoff: str | datetime,
        transport: ExchangeTransport,
        clock: Callable[[], datetime],
        timeout_seconds: float = 10.0,
    ) -> None:
        self.codes = _normalize_codes(codes)
        self.feature_cutoff = _as_cn(feature_cutoff)
        if not self.codes:
            raise ExchangeAnnouncementContractError(
                "EXCHANGE_ANNOUNCEMENT_CODES_MISSING"
            )
        if not hasattr(transport, "request") or not callable(clock):
            raise ExchangeAnnouncementContractError(
                "EXCHANGE_ANNOUNCEMENT_DEPENDENCY_INVALID"
            )
        if timeout_seconds <= 0:
            raise ExchangeAnnouncementContractError(
                "EXCHANGE_ANNOUNCEMENT_TIMEOUT_INVALID"
            )
        self.transport = transport
        self.clock = clock
        self.timeout_seconds = float(timeout_seconds)

    def collect_sse_records(self) -> list[dict[str, Any]]:
        return list(self.collect_sse_batch().records)

    def collect_szse_records(self) -> list[dict[str, Any]]:
        return list(self.collect_szse_batch().records)

    def collect_bse_records(self) -> list[dict[str, Any]]:
        return list(self.collect_bse_batch().records)

    def collect_sse_batch(self) -> ExchangeAnnouncementBatch:
        self._require_exchange("sse")
        records: list[dict[str, Any]] = []
        audit_records: list[dict[str, Any]] = []
        responses = []
        date_text = self.feature_cutoff.date().isoformat()
        for code in self.codes:
            response = self._request(
                "sse",
                "api",
                "GET",
                SSE_ANNOUNCEMENT_URL,
                params={
                    "isPagination": "true",
                    "pageHelp.pageSize": "50",
                    "pageHelp.pageNo": "1",
                    "pageHelp.beginPage": "1",
                    "pageHelp.cacheSize": "1",
                    "START_DATE": date_text,
                    "END_DATE": date_text,
                    "SECURITY_CODE": code,
                    "TITLE": "",
                    "BULLETIN_TYPE": "",
                    "stockType": "",
                },
                data=None,
                json_body=None,
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer": (
                        "https://www.sse.com.cn/disclosure/listedinfo/"
                        "announcement/"
                    ),
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                },
            )
            responses.append(response)
            formal, audit = self._records_from_sse(code, response)
            records.extend(formal)
            audit_records.extend(audit)
        return _batch("sse", records, audit_records, responses)

    def collect_szse_batch(self) -> ExchangeAnnouncementBatch:
        self._require_exchange("szse")
        records: list[dict[str, Any]] = []
        audit_records: list[dict[str, Any]] = []
        responses = []
        date_text = self.feature_cutoff.date().isoformat()
        for code in self.codes:
            body = {
                "stock": [code],
                "channelCode": ["listedNotice_disc"],
                "pageSize": 50,
                "pageNum": 1,
                "seDate": [date_text, date_text],
            }
            response = self._request(
                "szse",
                "api",
                "POST",
                SZSE_ANNOUNCEMENT_URL,
                params=None,
                data=None,
                json_body=body,
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer": (
                        "https://www.szse.cn/disclosure/listed/notice/"
                        "index.html"
                    ),
                    "Accept": "application/json",
                    "Content-Type": "application/json;charset=UTF-8",
                },
            )
            responses.append(response)
            formal, audit = self._records_from_szse(code, response)
            records.extend(formal)
            audit_records.extend(audit)
        return _batch("szse", records, audit_records, responses)

    def collect_bse_batch(self) -> ExchangeAnnouncementBatch:
        self._require_exchange("bse")
        records: list[dict[str, Any]] = []
        audit_records: list[dict[str, Any]] = []
        responses = []
        landing = self._request(
            "bse",
            "landing",
            "GET",
            BSE_LANDING_URL,
            params=None,
            data=None,
            json_body=None,
            headers={"User-Agent": USER_AGENT, "Referer": "https://www.bse.cn/"},
        )
        responses.append(landing)
        if b"<html" not in landing["raw_bytes"].lower():
            raise ExchangeAnnouncementContractError(
                "BSE_LANDING_RESPONSE_INVALID",
                response_evidence=landing,
            )
        date_text = self.feature_cutoff.date().isoformat()
        fields = (
            "companyCd",
            "companyName",
            "disclosureCode",
            "disclosureTitle",
            "disclosurePostTitle",
            "destFilePath",
            "publishDate",
            "xxfcbj",
            "fileExt",
            "xxzrlx",
        )
        for code in self.codes:
            form: list[tuple[str, str]] = [
                ("disclosureType[]", "5"),
                ("page", "0"),
                ("companyCd", code),
                ("isNewThree", "1"),
                ("startTime", date_text),
                ("endTime", date_text),
                ("keyword", ""),
                ("xxfcbj[]", "2"),
            ]
            form.extend(("needFields[]", field) for field in fields)
            response = self._request(
                "bse",
                "api",
                "POST",
                BSE_ANNOUNCEMENT_URL,
                params=None,
                data=form,
                json_body=None,
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer": BSE_LANDING_URL,
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            responses.append(response)
            formal, audit = self._records_from_bse(code, response)
            records.extend(formal)
            audit_records.extend(audit)
        return _batch("bse", records, audit_records, responses)

    def _records_from_sse(
        self,
        code: str,
        response: Mapping[str, Any],
        *,
        contract_version: str = "v2",
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        body = _json_bytes(response["raw_bytes"])
        rows = body.get("result") or []
        flattened = []
        for value in rows:
            if isinstance(value, list):
                flattened.extend(value)
            elif isinstance(value, Mapping):
                flattened.append(value)
        return self._records(
            "sse",
            code,
            flattened,
            response,
            id_fields=("ORG_BULLETIN_ID",),
            title_fields=("TITLE",),
            time_fields=("SSEDATE",),
            url_fields=("URL",),
            code_fields=("SECURITY_CODE",),
            contract_version=contract_version,
        )

    def _records_from_szse(
        self,
        code: str,
        response: Mapping[str, Any],
        *,
        contract_version: str = "v2",
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        body = _json_bytes(response["raw_bytes"])
        rows = body.get("data") or []
        return self._records(
            "szse",
            code,
            rows,
            response,
            id_fields=("id", "annId"),
            title_fields=("title",),
            time_fields=("publishTime",),
            url_fields=("attachPath",),
            code_fields=("secCode",),
            contract_version=contract_version,
        )

    def _records_from_bse(
        self,
        code: str,
        response: Mapping[str, Any],
        *,
        contract_version: str = "v2",
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        body = _bse_json_bytes(response["raw_bytes"])
        wrappers = body if isinstance(body, list) else []
        rows = []
        if wrappers:
            rows = ((wrappers[0].get("listInfo") or {}).get("content") or [])
        return self._records(
            "bse",
            code,
            rows,
            response,
            id_fields=("disclosureCode",),
            title_fields=("disclosureTitle", "disclosurePostTitle"),
            time_fields=("publishDate",),
            url_fields=("destFilePath",),
            code_fields=("companyCd",),
            contract_version=contract_version,
        )

    def _records(
        self,
        source: str,
        code: str,
        rows: Iterable[Mapping[str, Any]],
        response: Mapping[str, Any],
        *,
        id_fields: Sequence[str],
        title_fields: Sequence[str],
        time_fields: Sequence[str],
        url_fields: Sequence[str],
        code_fields: Sequence[str],
        contract_version: str = "v2",
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        records = []
        audit_records = []
        seen = set()
        for row in rows:
            if not isinstance(row, Mapping):
                raise ExchangeAnnouncementContractError(
                    "EXCHANGE_ANNOUNCEMENT_ROW_INVALID"
                )
            row_code = _first_text(row, code_fields)
            if isinstance(row.get("secCode"), list):
                values = [str(value) for value in row["secCode"]]
                row_code = code if code in values else ""
            if row_code != code:
                raise ExchangeAnnouncementContractError(
                    f"EXCHANGE_ANNOUNCEMENT_CODE_MISMATCH:{source}:{code}"
                )
            announcement_id = _first_text(row, id_fields)
            title = "".join(
                str(row.get(field) or "") for field in title_fields
            ).strip()
            published_text = _first_text(row, time_fields)
            if not announcement_id or not title:
                raise ExchangeAnnouncementContractError(
                    "EXCHANGE_ANNOUNCEMENT_REQUIRED_FIELD_MISSING"
                )
            published, precision = _publication_time(published_text)
            official_url = _official_document_url(
                source, _first_text(row, url_fields)
            )
            key = (code, announcement_id)
            if key in seen:
                raise ExchangeAnnouncementContractError(
                    "EXCHANGE_ANNOUNCEMENT_DUPLICATE"
                )
            seen.add(key)
            record = _record(
                source,
                code,
                announcement_id,
                title,
                published,
                precision,
                official_url,
                response,
                self.feature_cutoff,
                include_top_level_precision=contract_version != "v1",
            )
            if contract_version == "v1":
                if published <= self.feature_cutoff:
                    records.append(record)
                continue
            exclusion_reason = _publication_exclusion_reason(
                published,
                precision,
                self.feature_cutoff,
            )
            if exclusion_reason:
                audit_records.append(
                    {
                        **record,
                        "formal_eligible": False,
                        "audit_reason": exclusion_reason,
                    }
                )
            else:
                records.append(record)
        return records, audit_records

    def _request(
        self,
        source: str,
        purpose: str,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
        data: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
        json_body: Mapping[str, Any] | None,
        headers: Mapping[str, str],
    ) -> dict[str, Any]:
        observed = _as_cn(self.clock())
        request_material = _request_material(
            method, url, params=params, data=data, json_body=json_body
        )
        try:
            response = self.transport.request(
                method,
                url,
                params=params,
                data=data,
                json_body=json_body,
                headers=headers,
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as exc:
            raise ExchangeAnnouncementContractError(
                f"EXCHANGE_ANNOUNCEMENT_REQUEST_FAILED:{source}"
            ) from exc
        available = _as_cn(self.clock())
        if available < observed:
            raise ExchangeAnnouncementContractError(
                "EXCHANGE_ANNOUNCEMENT_TIME_ORDER_INVALID"
            )
        evidence = {
            "source": source,
            "purpose": purpose,
            **request_material,
            "request_hash": stable_hash(request_material),
            "raw_hash": hashlib.sha256(response.content).hexdigest(),
            "raw_bytes": response.content,
            "http_status_code": response.status_code,
            "response_url": response.url,
            "response_content_type": str(
                response.headers.get("Content-Type") or ""
            ),
            "observed_at": observed.isoformat(timespec="microseconds"),
            "available_at": available.isoformat(timespec="microseconds"),
        }
        parsed = urlparse(response.url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != OFFICIAL_RESPONSE_HOSTS[source]
        ):
            raise ExchangeAnnouncementContractError(
                "EXCHANGE_ANNOUNCEMENT_RESPONSE_URL_INVALID",
                response_evidence=evidence,
            )
        if type(response.status_code) is not int or response.status_code != 200:
            raise ExchangeAnnouncementContractError(
                "EXCHANGE_ANNOUNCEMENT_HTTP_STATUS_INVALID",
                response_evidence=evidence,
            )
        if b"<html" in response.content[:512].lower() and purpose == "api":
            raise ExchangeAnnouncementContractError(
                "EXCHANGE_ANNOUNCEMENT_HTML_RESPONSE_REJECTED",
                response_evidence=evidence,
            )
        return evidence

    def _require_exchange(self, source: str) -> None:
        invalid = [code for code in self.codes if _exchange_for_code(code) != source]
        if invalid:
            raise ExchangeAnnouncementContractError(
                f"EXCHANGE_ANNOUNCEMENT_ROUTE_MISMATCH:{source}:"
                + ",".join(invalid)
            )


def validate_exchange_announcement_records(
    source: str,
    records: Iterable[Mapping[str, Any]],
    *,
    feature_cutoff: str | datetime,
    codes: Iterable[str],
) -> dict[str, Any]:
    return _validate_exchange_announcement_record_set(
        source,
        records,
        feature_cutoff=feature_cutoff,
        codes=codes,
        audit=False,
        legacy_v1=False,
    )


def validate_exchange_announcement_records_v1(
    source: str,
    records: Iterable[Mapping[str, Any]],
    *,
    feature_cutoff: str | datetime,
    codes: Iterable[str],
) -> dict[str, Any]:
    return _validate_exchange_announcement_record_set(
        source,
        records,
        feature_cutoff=feature_cutoff,
        codes=codes,
        audit=False,
        legacy_v1=True,
    )


def validate_exchange_announcement_audit_records(
    source: str,
    records: Iterable[Mapping[str, Any]],
    *,
    feature_cutoff: str | datetime,
    codes: Iterable[str],
) -> dict[str, Any]:
    return _validate_exchange_announcement_record_set(
        source,
        records,
        feature_cutoff=feature_cutoff,
        codes=codes,
        audit=True,
        legacy_v1=False,
    )


def _validate_exchange_announcement_record_set(
    source: str,
    records: Iterable[Mapping[str, Any]],
    *,
    feature_cutoff: str | datetime,
    codes: Iterable[str],
    audit: bool,
    legacy_v1: bool,
) -> dict[str, Any]:
    if source not in SOURCE_IDENTITIES:
        return _validation("EXCHANGE_ANNOUNCEMENT_SOURCE_UNKNOWN", False, [])
    requested = _normalize_codes(codes)
    rows = [dict(row) for row in records]
    errors = []
    seen = set()
    origin, adapter, version = SOURCE_IDENTITIES[source]
    cutoff = _as_cn(feature_cutoff)
    for index, row in enumerate(rows):
        required = (
            "capability",
            "code",
            "announcement_id",
            "title",
            "published_at",
            "official_url",
            "origin_source",
            "adapter",
            "source_version",
            "event_time",
            "observed_at",
            "available_at",
            "decision_cutoff",
            "request_hash",
            "raw_hash",
        )
        if not legacy_v1:
            required += ("published_at_precision",)
        if audit:
            required += ("audit_reason", "formal_eligible")
        missing = [field for field in required if row.get(field) in (None, "")]
        if missing:
            errors.append(f"{index}:fields_missing:{','.join(missing)}")
            continue
        if row["capability"] != "announcement":
            errors.append(f"{index}:capability_mismatch")
        if (
            row["origin_source"] != origin
            or row["adapter"] != adapter
            or row["source_version"] != version
        ):
            errors.append(f"{index}:identity_mismatch")
        code = str(row["code"])
        if code not in requested or _exchange_for_code(code) != source:
            errors.append(f"{index}:code_scope_mismatch")
        key = (code, str(row["announcement_id"]))
        if key in seen:
            errors.append(f"{index}:duplicate")
        seen.add(key)
        if not _official_url(source, str(row["official_url"])):
            errors.append(f"{index}:official_url_invalid")
        for field in ("request_hash", "raw_hash"):
            if not isinstance(row[field], str) or not SHA256_PATTERN.fullmatch(
                row[field]
            ):
                errors.append(f"{index}:{field}_invalid")
        published = parse_cn_datetime(row["published_at"])
        event = parse_cn_datetime(row["event_time"])
        observed = parse_cn_datetime(row["observed_at"])
        available = parse_cn_datetime(row["available_at"])
        decision = parse_cn_datetime(row["decision_cutoff"])
        if None in {published, event, observed, available, decision}:
            errors.append(f"{index}:time_invalid")
            continue
        if not (
            published == event
            and published <= observed <= available
            and decision == cutoff
        ):
            errors.append(f"{index}:time_contract_invalid")
            continue
        if legacy_v1:
            if published > cutoff:
                errors.append(f"{index}:time_contract_invalid")
            continue
        precision = row.get("published_at_precision")
        if precision not in {"date", "datetime"}:
            errors.append(f"{index}:publication_precision_invalid")
            continue
        exclusion_reason = _publication_exclusion_reason(
            published,
            str(precision),
            cutoff,
        )
        if audit:
            if row.get("formal_eligible") is not False:
                errors.append(f"{index}:audit_formal_eligible_invalid")
            if not exclusion_reason:
                errors.append(f"{index}:audit_record_formally_eligible")
            elif row.get("audit_reason") != exclusion_reason:
                errors.append(f"{index}:audit_reason_mismatch")
        elif exclusion_reason:
            errors.append(f"{index}:formal_record_ineligible:{exclusion_reason}")
    canonical = sorted(rows, key=_record_sort_key)
    prefix = "EXCHANGE_ANNOUNCEMENT_AUDIT_RECORDS" if audit else (
        "EXCHANGE_ANNOUNCEMENT_RECORDS"
    )
    return _validation(
        f"{prefix}_VALID"
        if not errors
        else f"{prefix}_INVALID",
        not errors,
        errors,
        records_hash=stable_hash(canonical),
        record_count=len(canonical),
    )


def replay_exchange_announcement_responses(
    source: str,
    responses: Iterable[Mapping[str, Any]],
    *,
    feature_cutoff: str | datetime,
    codes: Iterable[str],
    contract_version: str = "v2",
) -> tuple[list[dict[str, Any]], list[str]]:
    records, _audit_records, errors = (
        replay_exchange_announcement_response_sets(
            source,
            responses,
            feature_cutoff=feature_cutoff,
            codes=codes,
            contract_version=contract_version,
        )
    )
    return records, errors


def replay_exchange_announcement_response_sets(
    source: str,
    responses: Iterable[Mapping[str, Any]],
    *,
    feature_cutoff: str | datetime,
    codes: Iterable[str],
    contract_version: str = "v2",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    if contract_version not in {"v1", "v2"}:
        return [], [], ["contract_version_invalid"]
    response_rows = [dict(item) for item in responses]
    errors = []
    raw_by_code: dict[str, Mapping[str, Any]] = {}
    for index, response in enumerate(response_rows):
        raw = response.get("raw_bytes")
        if not isinstance(raw, bytes):
            errors.append(f"{index}:raw_bytes_missing")
            continue
        if hashlib.sha256(raw).hexdigest() != response.get("raw_hash"):
            errors.append(f"{index}:raw_hash_mismatch")
        material = _request_material(
            str(response.get("method") or ""),
            str(response.get("url") or ""),
            params=response.get("params") or [],
            data=response.get("data") or [],
            json_body=response.get("json_body"),
        )
        if stable_hash(material) != response.get("request_hash"):
            errors.append(f"{index}:request_hash_mismatch")
        parsed_url = urlparse(str(response.get("response_url") or ""))
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname != OFFICIAL_RESPONSE_HOSTS.get(source)
            or response.get("http_status_code") != 200
        ):
            errors.append(f"{index}:response_contract_invalid")
        code = _request_code(source, response)
        if response.get("purpose") == "api" and code:
            raw_by_code[code] = response
    if errors:
        return [], [], errors
    provider = ExchangeAnnouncementProviders(
        codes,
        feature_cutoff=feature_cutoff,
        transport=_NoNetworkTransport(),
        clock=lambda: _as_cn(feature_cutoff),
    )
    records = []
    audit_records = []
    for code in _normalize_codes(codes):
        response = raw_by_code.get(code)
        if response is None:
            errors.append(f"response_missing:{code}")
            continue
        if source == "sse":
            formal, audit = provider._records_from_sse(
                code, response, contract_version=contract_version
            )
        elif source == "szse":
            formal, audit = provider._records_from_szse(
                code, response, contract_version=contract_version
            )
        elif source == "bse":
            formal, audit = provider._records_from_bse(
                code, response, contract_version=contract_version
            )
        else:
            formal, audit = [], []
        records.extend(formal)
        audit_records.extend(audit)
    return (
        sorted(records, key=_record_sort_key),
        sorted(audit_records, key=_record_sort_key),
        errors,
    )


def compute_exchange_announcement_verifier_contract_hash(
    schema_version: str = EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_VERSION,
) -> str:
    if schema_version == EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_V1:
        return EXCHANGE_ANNOUNCEMENT_VERIFIER_CONTRACT_V1_HASH
    if schema_version != EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_VERSION:
        return ""
    return stable_hash(
        {
            "contract_version": EXCHANGE_ANNOUNCEMENT_VERIFIER_CONTRACT_VERSION,
            "evidence_schema": EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_VERSION,
            "source_identities": SOURCE_IDENTITIES,
            "provider_keys": PROVIDER_KEYS,
            "official_response_hosts": OFFICIAL_RESPONSE_HOSTS,
            "official_document_hosts": {
                key: sorted(value) for key, value in OFFICIAL_DOCUMENT_HOSTS.items()
            },
            "publication_precision_contract": {
                "same_day_date_only": PUBLICATION_TIME_PRECISION_INSUFFICIENT,
                "formal_datetime": "published_at < feature_cutoff",
                "midnight_datetime": "date",
            },
        }
    )


class _NoNetworkTransport:
    request_count = 0

    def request(self, *args: Any, **kwargs: Any) -> ExchangeHttpResponse:
        raise AssertionError("network_not_allowed_during_replay")


def _record(
    source: str,
    code: str,
    announcement_id: str,
    title: str,
    published: datetime,
    published_at_precision: str,
    official_url: str,
    response: Mapping[str, Any],
    cutoff: datetime,
    *,
    include_top_level_precision: bool = True,
) -> dict[str, Any]:
    origin, adapter, version = SOURCE_IDENTITIES[source]
    record = {
        "capability": "announcement",
        "code": code,
        "announcement_id": announcement_id,
        "title": title,
        "published_at": published.isoformat(timespec="seconds"),
        "official_url": official_url,
        "origin_source": origin,
        "adapter": adapter,
        "source_version": version,
        "event_time": published.isoformat(timespec="seconds"),
        "observed_at": str(response["observed_at"]),
        "available_at": str(response["available_at"]),
        "decision_cutoff": cutoff.isoformat(timespec="seconds"),
        "request_hash": str(response["request_hash"]),
        "raw_hash": str(response["raw_hash"]),
        "payload": {
            "code": code,
            "announcement_id": announcement_id,
            "title": title,
            "published_at_precision": published_at_precision,
            "canonical_url": official_url,
        },
    }
    if include_top_level_precision:
        record["published_at_precision"] = published_at_precision
    return record


def _batch(
    source: str,
    records: list[dict[str, Any]],
    audit_records: list[dict[str, Any]],
    responses: list[dict[str, Any]],
) -> ExchangeAnnouncementBatch:
    normalized = sorted(records, key=_record_sort_key)
    normalized_audit = sorted(audit_records, key=_record_sort_key)
    return ExchangeAnnouncementBatch(
        source=source,
        status="AVAILABLE" if normalized else "AVAILABLE_EMPTY",
        records=tuple(normalized),
        audit_records=tuple(normalized_audit),
        responses=tuple(responses),
    )


def _publication_time(value: str) -> tuple[datetime, str]:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return _as_cn(text), "date"
    parsed = _as_cn(text)
    has_explicit_time = re.search(
        r"(?:T|\s)\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?",
        text,
    )
    if not has_explicit_time:
        raise ExchangeAnnouncementContractError(
            "EXCHANGE_ANNOUNCEMENT_PUBLICATION_PRECISION_UNKNOWN"
        )
    # Official SZSE rows currently serialize a date as midnight. Without an
    # independently meaningful clock value this remains date precision.
    is_midnight = parsed.timetz().replace(tzinfo=None) == datetime.min.time()
    precision = "date" if is_midnight else "datetime"
    return parsed, precision


def _publication_exclusion_reason(
    published: datetime,
    precision: str,
    cutoff: datetime,
) -> str:
    if precision == "date":
        if published.date() == cutoff.date():
            return PUBLICATION_TIME_PRECISION_INSUFFICIENT
        if published.date() > cutoff.date():
            return "publication_after_cutoff"
        return ""
    if precision == "datetime":
        return "publication_at_or_after_cutoff" if published >= cutoff else ""
    return "publication_time_precision_unknown"


def _request_material(
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
    data: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
    json_body: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "method": method.upper(),
        "url": url,
        "params": _canonical_pairs(params),
        "data": _canonical_pairs(data),
        "json_body": _canonical_json(json_body),
    }


def _request_code(source: str, response: Mapping[str, Any]) -> str:
    pairs = dict(response.get("params") or [])
    data_pairs = list(response.get("data") or [])
    if source == "sse":
        return str(pairs.get("SECURITY_CODE") or "")
    if source == "szse":
        body = response.get("json_body") or {}
        stocks = body.get("stock") or [] if isinstance(body, Mapping) else []
        return str(stocks[0]) if len(stocks) == 1 else ""
    if source == "bse":
        return str(dict(data_pairs).get("companyCd") or "")
    return ""


def _canonical_pairs(
    value: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
) -> list[list[str]]:
    pairs = _pair_sequence(value)
    return sorted([[str(key), str(item)] for key, item in pairs])


def _pair_sequence(
    value: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
) -> list[tuple[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        pairs = []
        for key, item in value.items():
            if isinstance(item, (list, tuple)):
                pairs.extend((str(key), member) for member in item)
            else:
                pairs.append((str(key), item))
        return pairs
    return [(str(key), item) for key, item in value]


def _canonical_json(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return json.loads(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _json_bytes(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExchangeAnnouncementContractError(
            "EXCHANGE_ANNOUNCEMENT_JSON_INVALID"
        ) from exc
    if not isinstance(value, dict):
        raise ExchangeAnnouncementContractError(
            "EXCHANGE_ANNOUNCEMENT_JSON_OBJECT_REQUIRED"
        )
    return value


def _bse_json_bytes(raw: bytes) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise ExchangeAnnouncementContractError(
            "EXCHANGE_ANNOUNCEMENT_JSON_INVALID"
        ) from exc
    if text.startswith("null(") and text.endswith(")"):
        text = text[5:-1]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExchangeAnnouncementContractError(
            "EXCHANGE_ANNOUNCEMENT_JSON_INVALID"
        ) from exc
    if not isinstance(value, list):
        raise ExchangeAnnouncementContractError(
            "EXCHANGE_ANNOUNCEMENT_JSON_ARRAY_REQUIRED"
        )
    return value


def _official_document_url(source: str, raw_url: str) -> str:
    if source == "sse":
        value = urljoin("https://static.sse.com.cn/", raw_url)
    elif source == "szse":
        value = urljoin("https://disc.static.szse.cn/download/", raw_url.lstrip("/"))
    elif source == "bse":
        value = urljoin("https://www.bse.cn/", raw_url)
    else:
        raise ExchangeAnnouncementContractError(
            "EXCHANGE_ANNOUNCEMENT_SOURCE_UNKNOWN"
        )
    if not _official_url(source, value):
        raise ExchangeAnnouncementContractError(
            "EXCHANGE_ANNOUNCEMENT_DOCUMENT_URL_INVALID"
        )
    return value


def _official_url(source: str, value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname in OFFICIAL_DOCUMENT_HOSTS.get(source, set())
        and bool(parsed.path)
    )


def _exchange_for_code(code: str) -> str:
    if not CODE_PATTERN.fullmatch(str(code)):
        return ""
    if code.startswith("6"):
        return "sse"
    if code.startswith(("0", "3")):
        return "szse"
    if code.startswith(("4", "8", "92")):
        return "bse"
    return ""


def _normalize_codes(codes: Iterable[str]) -> tuple[str, ...]:
    values = [str(code).strip() for code in codes]
    if any(not CODE_PATTERN.fullmatch(code) for code in values):
        raise ExchangeAnnouncementContractError(
            "EXCHANGE_ANNOUNCEMENT_CODE_INVALID"
        )
    if len(values) != len(set(values)):
        raise ExchangeAnnouncementContractError(
            "EXCHANGE_ANNOUNCEMENT_CODE_DUPLICATE"
        )
    return tuple(sorted(values))


def _as_cn(value: str | datetime) -> datetime:
    parsed = parse_cn_datetime(value)
    if parsed is None:
        raise ExchangeAnnouncementContractError(
            "EXCHANGE_ANNOUNCEMENT_TIME_INVALID"
        )
    return parsed.astimezone(CN_TZ)


def _first_text(row: Mapping[str, Any], fields: Sequence[str]) -> str:
    for field in fields:
        value = row.get(field)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _record_sort_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(row.get("code") or ""),
        str(row.get("published_at") or ""),
        str(row.get("announcement_id") or ""),
        str(row.get("title") or ""),
    )


def _validation(
    status: str,
    valid: bool,
    errors: list[str],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "status": status,
        "valid": valid,
        "errors": sorted(set(errors)),
        **extra,
        "automatic_configuration_change": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


__all__ = [
    "BSE_ANNOUNCEMENT_URL",
    "BSE_LANDING_URL",
    "EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_VERSION",
    "EXCHANGE_ANNOUNCEMENT_VERIFIER_CONTRACT_VERSION",
    "ExchangeAnnouncementBatch",
    "ExchangeAnnouncementContractError",
    "ExchangeAnnouncementProviders",
    "ExchangeHttpResponse",
    "ExchangeUrllibTransport",
    "PROVIDER_KEYS",
    "SOURCE_IDENTITIES",
    "SSE_ANNOUNCEMENT_URL",
    "SZSE_ANNOUNCEMENT_URL",
    "compute_exchange_announcement_verifier_contract_hash",
    "replay_exchange_announcement_responses",
    "validate_exchange_announcement_records",
]
