from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


STANDARD_FIELDS = [
    "code",
    "name",
    "price",
    "change_pct",
    "vol_ratio",
    "turnover_pct",
    "amount_wan",
    "float_mcap_yi",
    "limit_up",
    "limit_down",
    "is_limit_up",
    "is_st",
    "is_suspended",
    "list_date",
    "is_new_stock",
    "is_bj_stock",
    "theme_tags",
    "big_order_net",
    "main_net",
]

SAFETY_FIELD_REASONS = {
    "limit_up": "limit_price_unknown",
    "limit_down": "limit_price_unknown",
    "is_limit_up": "limit_price_unknown",
    "is_st": "st_status_unknown",
    "is_suspended": "suspended_status_unknown",
    "is_new_stock": "list_date_missing",
    "is_bj_stock": "bj_status_unknown",
}


@dataclass
class LiveDataQualityReport:
    mode: str
    trade_date: str
    run_time: str = ""
    session_state: str = ""
    is_trade_day: bool | None = None
    fallback_to_demo: bool = False
    expected_data_date: str = ""
    freshness_basis: str = ""
    target_date_match_count: int = 0
    target_date_mismatch_count: int = 0
    timestamp_missing_count: int = 0
    source_status: list[dict[str, Any]] = field(default_factory=list)
    candidate_counts: dict[str, int] = field(default_factory=lambda: {"raw": 0, "normalized": 0, "dropped": 0, "scored": 0})
    field_coverage: dict[str, dict[str, float]] = field(default_factory=dict)
    field_improvement: dict[str, Any] = field(default_factory=dict)
    per_stock_missing: list[dict[str, Any]] = field(default_factory=list)
    freshness_summary: dict[str, int] = field(default_factory=lambda: {"fresh": 0, "stale": 0, "unknown": 0})
    stale_sources: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    report_path: str = ""

    def set_replay_context(self, expected_data_date: str, freshness_basis: str) -> None:
        self.expected_data_date = expected_data_date
        self.freshness_basis = freshness_basis

    def set_session_context(self, run_time: str, session_state: str, is_trade_day: bool, warnings: list[str] | None = None) -> None:
        self.run_time = run_time
        self.session_state = session_state
        self.is_trade_day = is_trade_day
        for warning in warnings or []:
            if warning not in self.warnings:
                self.warnings.append(warning)

    def record_source(
        self,
        source: str,
        ok: bool,
        rows: int = 0,
        error: str = "",
        freshness: dict[str, Any] | None = None,
    ) -> None:
        item = {"source": source, "ok": ok, "rows": rows, "error": error}
        if freshness:
            item.update(
                {
                    "data_date": freshness.get("data_date", ""),
                    "data_time": freshness.get("data_time", ""),
                    "is_stale": freshness.get("is_stale", False),
                    "stale_reason": freshness.get("stale_reason", ""),
                }
            )
            self.record_freshness(source, freshness)
        self.source_status.append(item)
        if not ok and error:
            self.warnings.append(f"{source}: {error}")

    def record_freshness(self, source: str, freshness: dict[str, Any]) -> None:
        reason = freshness.get("stale_reason", "")
        if freshness.get("freshness_basis") == "previous_close_expected":
            self.target_date_match_count += 1
        elif reason in {"replay_data_too_old", "replay_data_from_observation_day"}:
            self.target_date_mismatch_count += 1
        elif reason == "timestamp_missing":
            self.timestamp_missing_count += 1
        if freshness.get("is_stale"):
            self.freshness_summary["stale"] += 1
            self.stale_sources.append({"source": source, **freshness})
        elif freshness.get("stale_reason"):
            self.freshness_summary["unknown"] += 1
            self.stale_sources.append({"source": source, **freshness})
        else:
            self.freshness_summary["fresh"] += 1

    def record_candidates(self, raw_count: int, normalized: list[dict], dropped: int = 0) -> None:
        self.candidate_counts["raw"] = raw_count
        self.candidate_counts["normalized"] = len(normalized)
        self.candidate_counts["dropped"] = dropped
        self.field_coverage = build_field_coverage(normalized)
        self.field_improvement = build_field_improvement(normalized, self.source_status)
        self.per_stock_missing = [
            {
                "code": stock.get("code", ""),
                "name": stock.get("name", ""),
                "missing_fields": stock.get("_missing_fields", []),
                "risk_unknown_reasons": stock.get("_risk_unknown_reasons", []),
            }
            for stock in normalized
            if stock.get("_missing_fields")
        ]

    def mark_fallback(self, reason: str) -> None:
        self.fallback_to_demo = True
        self.warnings.append(reason)

    def write_markdown(self, reports_dir: str, trade_date: str | None = None) -> str:
        date_value = trade_date or self.trade_date
        path = Path(reports_dir)
        path.mkdir(parents=True, exist_ok=True)
        report = path / f"live_data_quality_{date_value}.md"
        lines = [
            "# Live Data Quality Report",
            "",
            f"Mode: {self.mode}",
            f"Date: {date_value}",
            f"trade_date: {date_value}",
            f"run_time: {self.run_time}",
            f"session_state: {self.session_state}",
            f"is_trade_day: {'YES' if self.is_trade_day else 'NO'}",
            f"Fallback to demo: {'YES' if self.fallback_to_demo else 'NO'}",
            "",
            "## Source Status",
            "",
        ]
        if self.expected_data_date:
            lines[9:9] = [
                f"expected_data_date: {self.expected_data_date}",
                f"freshness_basis: {self.freshness_basis}",
                f"target_date_match_count: {self.target_date_match_count}",
                f"target_date_mismatch_count: {self.target_date_mismatch_count}",
                f"timestamp_missing_count: {self.timestamp_missing_count}",
            ]
        if self.source_status:
            for item in self.source_status:
                status = "OK" if item["ok"] else "FAIL"
                lines.append(f"- {item['source']}: {status}, rows={item['rows']}, error={item['error']}")
        else:
            lines.append("- No live sources were called.")

        lines.extend(["", "## Candidate Counts", ""])
        for key, value in self.candidate_counts.items():
            lines.append(f"- {key}: {value}")

        lines.extend(["", "## Freshness", ""])
        lines.append("freshness_summary:")
        for key, value in self.freshness_summary.items():
            lines.append(f"- {key}: {value}")
        lines.append("")
        lines.append("stale_sources:")
        if self.stale_sources:
            for item in self.stale_sources:
                lines.append(
                    f"- {item.get('source', '')}: data_date={item.get('data_date', '')}, "
                    f"data_time={item.get('data_time', '')}, stale={item.get('is_stale', '')}, "
                    f"reason={item.get('stale_reason', '')}"
                )
        else:
            lines.append("- None.")

        lines.extend(["", "## Field Coverage Improvement", ""])
        if self.field_improvement:
            for label in ("list_date", "is_new_stock", "main_net", "big_order_net"):
                item = self.field_improvement.get("coverage", {}).get(label, {})
                lines.append(
                    f"- {label} coverage: {item.get('present', 0)}/{item.get('total', 0)} "
                    f"({item.get('coverage_pct', 0):.1f}%)"
                )
            lines.append(f"- safety field unknown count: {self.field_improvement.get('safety_unknown_count', 0)}")
            lines.append(
                f"- candidate rejected by safety unknown count: "
                f"{self.field_improvement.get('candidate_rejected_by_safety_unknown_count', 0)}"
            )
            lines.append(f"- fund_flow_source: {self.field_improvement.get('fund_flow_source', {})}")
            lines.append(f"- estimated_capital_flow_count: {self.field_improvement.get('estimated_capital_flow_count', 0)}")
            lines.append(f"- fund_flow_error_count: {self.field_improvement.get('fund_flow_error_count', 0)}")
            lines.append(f"- top_missing_fields: {self.field_improvement.get('top_missing_fields', {})}")
            lines.append(f"- source_error_summary: {self.field_improvement.get('source_error_summary', {})}")
        else:
            lines.append("- No normalized live rows.")

        lines.extend(["", "## Field Coverage", ""])
        if self.field_coverage:
            lines.append("| Field | Present | Missing | Coverage |")
            lines.append("|---|---:|---:|---:|")
            for field_name, item in self.field_coverage.items():
                lines.append(f"| {field_name} | {item['present']} | {item['missing']} | {item['coverage_pct']:.1f}% |")
        else:
            lines.append("- No normalized live rows.")

        lines.extend(["", "## Missing Fields By Stock", ""])
        if self.per_stock_missing:
            for item in self.per_stock_missing:
                missing = ", ".join(item["missing_fields"])
                risk = ", ".join(item["risk_unknown_reasons"])
                lines.append(f"- {item['code']} {item['name']}: missing=[{missing}], risk=[{risk}]")
        else:
            lines.append("- None.")

        lines.extend(["", "## Warnings", ""])
        if self.warnings:
            for warning in self.warnings:
                lines.append(f"- {warning}")
        else:
            lines.append("- None.")

        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.report_path = str(report)
        return str(report)


def normalize_live_stock(raw: dict[str, Any], default_source: str = "live") -> dict[str, Any]:
    stock = dict(raw)
    sources = dict(stock.get("_sources") or {})
    for field_name in STANDARD_FIELDS:
        if field_name in stock and field_name not in sources:
            sources[field_name] = default_source
    if "is_bj" in stock and "is_bj_stock" not in stock:
        stock["is_bj_stock"] = stock["is_bj"]
        sources.setdefault("is_bj_stock", sources.get("is_bj", default_source))
    if "is_bj_stock" in stock and "is_bj" not in stock:
        stock["is_bj"] = stock["is_bj_stock"]
        sources.setdefault("is_bj", sources.get("is_bj_stock", default_source))
    stock["_sources"] = sources
    return stock


def validate_stock_fields(stock: dict[str, Any]) -> dict[str, Any]:
    result = dict(stock)
    missing: list[str] = []
    for field_name in STANDARD_FIELDS:
        aliases = [field_name]
        if field_name == "is_bj_stock":
            aliases.append("is_bj")
        if not any(_has_value(result.get(alias)) for alias in aliases):
            missing.append(field_name)

    risk_unknown = list(result.get("_risk_unknown_reasons") or [])
    for field_name in missing:
        reason = SAFETY_FIELD_REASONS.get(field_name)
        if field_name == "is_new_stock" and any(str(item).startswith("list_date_missing") for item in risk_unknown):
            continue
        if reason and reason not in risk_unknown:
            risk_unknown.append(reason)
    result["_missing_fields"] = missing
    result["_risk_unknown_reasons"] = risk_unknown
    result["_sources"] = dict(result.get("_sources") or {})
    return result


def build_field_coverage(stocks: list[dict]) -> dict[str, dict[str, float]]:
    total = len(stocks)
    coverage: dict[str, dict[str, float]] = {}
    for field_name in STANDARD_FIELDS:
        present = 0
        for stock in stocks:
            aliases = [field_name]
            if field_name == "is_bj_stock":
                aliases.append("is_bj")
            if any(_has_value(stock.get(alias)) for alias in aliases):
                present += 1
        missing = total - present
        coverage[field_name] = {
            "present": present,
            "missing": missing,
            "coverage_pct": (present / total * 100) if total else 0.0,
        }
    return coverage


def build_field_improvement(stocks: list[dict], source_status: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(stocks)
    coverage = build_field_coverage(stocks)
    selected = {}
    for field_name in ("list_date", "is_new_stock", "main_net", "big_order_net"):
        item = coverage.get(field_name, {"present": 0, "missing": total, "coverage_pct": 0.0})
        selected[field_name] = {
            "present": item["present"],
            "missing": item["missing"],
            "total": total,
            "coverage_pct": item["coverage_pct"],
        }

    missing_counts: dict[str, int] = {}
    safety_unknown_count = 0
    safety_reasons = set(SAFETY_FIELD_REASONS.values())
    for stock in stocks:
        for field_name in stock.get("_missing_fields", []):
            missing_counts[field_name] = missing_counts.get(field_name, 0) + 1
        risk_unknown = stock.get("_risk_unknown_reasons", [])
        if any(reason in safety_reasons or str(reason).startswith("list_date_missing") for reason in risk_unknown):
            safety_unknown_count += 1

    fund_flow_source: dict[str, int] = {}
    fund_flow_error_count = 0
    for stock in stocks:
        source = stock.get("fund_flow_source", "missing")
        fund_flow_source[source] = fund_flow_source.get(source, 0) + 1
        if stock.get("fund_flow_error"):
            fund_flow_error_count += 1

    source_error_summary: dict[str, int] = {}
    for item in source_status:
        if not item.get("ok"):
            source = str(item.get("source", "unknown"))
            source_error_summary[source] = source_error_summary.get(source, 0) + 1

    return {
        "coverage": selected,
        "safety_unknown_count": safety_unknown_count,
        "candidate_rejected_by_safety_unknown_count": safety_unknown_count,
        "top_missing_fields": dict(sorted(missing_counts.items(), key=lambda item: item[1], reverse=True)[:8]),
        "fund_flow_source": fund_flow_source,
        "estimated_capital_flow_count": fund_flow_source.get("estimated_from_big_order_net", 0),
        "fund_flow_error_count": fund_flow_error_count,
        "source_error_summary": source_error_summary,
    }


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if value == "":
        return False
    return True
