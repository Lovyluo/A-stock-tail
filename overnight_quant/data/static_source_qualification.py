from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from overnight_quant.data.point_in_time import stable_hash


S1_QUALIFICATION_SCHEMA_VERSION = "s1_partial_source_qualification_v1"
S1_EVIDENCE_HASH = (
    "235765b8be0189221859a9b6679a2e7b8e4c894e991e1c192acb0d34ed19725d"
)
S1_EVIDENCE_FILE_SHA256 = (
    "21e07487ce3012b175e48bc0622485c0afdb40e83cd9efe201281c2d6c18a01d"
)
S1_REPLAY_HASH = (
    "52e81b02e3a51c8ce7e9bf8fe566e51696828dc4d0154da60ba9d4df5a88e92b"
)
S1_EVIDENCE_CAPABILITY_REGISTRY_HASH = (
    "e4efac3a9d03dce1bb8e7edd063f82699b788c9cf404e4eba61308fb0d1457bc"
)
EXPECTED_S1_PARTIAL_QUALIFICATION_RECORD_HASH = (
    "11a672170abed9b5f18c1f56109f290aabd160a55f7921897afc9f962204872a"
)
CNINFO_INVESTIGATION_FILE_SHA256 = (
    "a56eaeb2de62cf4408c065bb24f247e25441b1ef668cb40f25a2977189f7a230"
)


S1_APPROVED_PROVIDER_KEYS = {
    (
        "trading_calendar",
        "tencent",
        "direct_http",
        "ifzq_fqkline_day_v2026-07-30",
    ): (
        "static_source_providers.StaticSourceProviders."
        "collect_trading_calendar_records"
    ),
    (
        "daily_bar_qfq",
        "tencent",
        "direct_http",
        "ifzq_fqkline_qfqday_v2026-07-30",
    ): (
        "static_source_providers.StaticSourceProviders."
        "collect_qfq_daily_records"
    ),
    (
        "stock_news",
        "eastmoney",
        "direct_http",
        "search_api_cms_old_v2026-07-30",
    ): (
        "static_source_providers.StaticSourceProviders."
        "collect_stock_news_records"
    ),
    (
        "global_news",
        "eastmoney",
        "direct_http",
        "np_weblist_724_v2026-07-30",
    ): (
        "static_source_providers.StaticSourceProviders."
        "collect_global_news_records"
    ),
}

S1_UNQUALIFIED_PROVIDER_KEYS = {
    (
        "announcement",
        "cninfo",
        "direct_http",
        "cninfo_query_v2026-07-30",
    ): (
        "static_source_providers.StaticSourceProviders."
        "collect_announcement_records"
    ),
}


def build_s1_partial_qualification_record() -> dict[str, Any]:
    capabilities = []
    for identity, provider_key in sorted(S1_APPROVED_PROVIDER_KEYS.items()):
        capability, origin_source, adapter, source_version = identity
        capabilities.append(
            {
                "capability": capability,
                "origin_source": origin_source,
                "adapter": adapter,
                "source_version": source_version,
                "provider_key": provider_key,
                "evidence_result": "STATIC_SOURCE_CAPABILITY_VALIDATED",
                "qualification_status": "qualified",
                "implementation_status": "bound",
                "selected_source": {
                    "origin_source": origin_source,
                    "adapter": adapter,
                    "source_version": source_version,
                },
            }
        )
    for identity, provider_key in sorted(S1_UNQUALIFIED_PROVIDER_KEYS.items()):
        capability, origin_source, adapter, source_version = identity
        capabilities.append(
            {
                "capability": capability,
                "origin_source": origin_source,
                "adapter": adapter,
                "source_version": source_version,
                "provider_key": provider_key,
                "evidence_result": "CNINFO_DIRECT_ACCESS_UNAVAILABLE",
                "qualification_status": "unqualified",
                "implementation_status": "candidate_not_activated",
                "selected_source": None,
            }
        )
    capabilities.sort(
        key=lambda row: (
            row["capability"],
            row["origin_source"],
            row["adapter"],
            row["source_version"],
        )
    )
    record = {
        "schema_version": S1_QUALIFICATION_SCHEMA_VERSION,
        "status": "S1_PARTIAL_SOURCE_QUALIFICATION_APPROVED",
        "pm_decision_date": "2026-09-18",
        "evidence": {
            "evidence_hash": S1_EVIDENCE_HASH,
            "file_sha256": S1_EVIDENCE_FILE_SHA256,
            "replay_hash": S1_REPLAY_HASH,
            "capability_registry_hash": (
                S1_EVIDENCE_CAPABILITY_REGISTRY_HASH
            ),
        },
        "cninfo_investigation": {
            "status": "CNINFO_DIRECT_ACCESS_UNAVAILABLE",
            "file_sha256": CNINFO_INVESTIGATION_FILE_SHA256,
        },
        "capabilities": capabilities,
        "automatic_configuration_change": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }
    record["qualification_record_hash"] = stable_hash(record)
    return record


def validate_s1_partial_qualification_record(
    record: Mapping[str, Any],
) -> dict[str, Any]:
    expected = build_s1_partial_qualification_record()
    supplied = deepcopy(dict(record))
    valid = supplied == expected
    return {
        "status": (
            "S1_PARTIAL_QUALIFICATION_RECORD_VERIFIED"
            if valid
            else "S1_PARTIAL_QUALIFICATION_RECORD_INVALID"
        ),
        "execution_ok": True,
        "record_valid": valid,
        "qualification_record_hash": (
            supplied.get("qualification_record_hash") or ""
        ),
        "expected_qualification_record_hash": expected[
            "qualification_record_hash"
        ],
        "automatic_configuration_change": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


S1_PARTIAL_QUALIFICATION_RECORD = build_s1_partial_qualification_record()
if (
    S1_PARTIAL_QUALIFICATION_RECORD["qualification_record_hash"]
    != EXPECTED_S1_PARTIAL_QUALIFICATION_RECORD_HASH
):
    raise RuntimeError("s1_partial_qualification_record_hash_drift")
S1_PARTIAL_QUALIFICATION_RECORD_HASH = (
    EXPECTED_S1_PARTIAL_QUALIFICATION_RECORD_HASH
)


__all__ = [
    "CNINFO_INVESTIGATION_FILE_SHA256",
    "EXPECTED_S1_PARTIAL_QUALIFICATION_RECORD_HASH",
    "S1_APPROVED_PROVIDER_KEYS",
    "S1_EVIDENCE_CAPABILITY_REGISTRY_HASH",
    "S1_EVIDENCE_FILE_SHA256",
    "S1_EVIDENCE_HASH",
    "S1_PARTIAL_QUALIFICATION_RECORD",
    "S1_PARTIAL_QUALIFICATION_RECORD_HASH",
    "S1_QUALIFICATION_SCHEMA_VERSION",
    "S1_REPLAY_HASH",
    "S1_UNQUALIFIED_PROVIDER_KEYS",
    "build_s1_partial_qualification_record",
    "validate_s1_partial_qualification_record",
]
