"""Display-only interpretations of an independently replayed concept batch."""
from dataclasses import dataclass

from overnight_quant.data.concept_batch_observation import CONTRACT, _number
from overnight_quant.data.s2_integration_contract import security_identity


@dataclass(frozen=True)
class FieldEvidence:
    membership: bool = False
    change_unit: str | None = None
    change_reference: str | None = None
    amount_unit: str | None = None
    amount_period: str | None = None


def field_evidence(batch):
    if batch["synthetic_test_only"]:
        return FieldEvidence(True, "%", "合成基准", "合成单位", "合成批次")
    # Replay binds the current contract's concept filter and exact selected BK to each raw page.
    # The public quote page confirms these routes, not independent or unique membership.
    name = batch.get("selected_name")
    if batch.get("contract_hash") == CONTRACT and isinstance(name, str) and name.strip():
        return FieldEvidence(membership=True)
    return FieldEvidence()


def explain_members(batch, evidence):
    rows = batch["member_raw"]
    coverage = batch["member_coverage"]
    complete = (coverage["complete"] is True and
                0 < len(rows) == coverage["received"] == coverage["total"])
    changes = [value for row in rows if (value := _number(row.get("f3"))) is not None]
    counts = None
    leaders = None
    if evidence.membership and complete and evidence.change_unit and evidence.change_reference and changes:
        up = sum(value > 0 for value in changes)
        down = sum(value < 0 for value in changes)
        counts = {"up": up, "down": down, "flat": len(changes) - up - down,
                  "up_share": up / len(changes)}
    if evidence.membership and complete and evidence.amount_unit and evidence.amount_period:
        amounts = [(_number(row.get("f6")), row) for row in rows]
        if all(value is not None and value >= 0 for value, _ in amounts):
            ranked = [(value, security_identity(("SH" if row["f13"] == 1 else "SZ") + row["f12"]), row)
                      for value, row in amounts]
            leaders = [{"code": code, "name": row.get("f14"), "amount": value}
                       for value, code, row in sorted(ranked, key=lambda item: (-item[0], item[1]))[:3]]
    return {"complete": complete, "valid": len(changes), "invalid": len(rows) - len(changes),
            "total": len(rows), "counts": counts, "leaders": leaders}
