from dataclasses import replace

import pytest

from overnight_quant.data.concept_batch_explanation import FieldEvidence, explain_members, field_evidence
from overnight_quant.data.concept_batch_observation import CONTRACT, LEGACY_CONTRACT


@pytest.fixture
def test_only_batch():
    return {
        "synthetic_test_only": True,
        "contract_hash": CONTRACT,
        "selected_name": "Fixture",
        "member_coverage": {"complete": True, "received": 4, "total": 4},
        "member_raw": [
            {"f12": "600000", "f13": 1, "f3": "1.5", "f6": 40},
            {"f12": "000001", "f13": 0, "f3": "-", "f6": 40},
            {"f12": "600519", "f13": 1, "f3": -1, "f6": 22},
            {"f12": "000002", "f13": 0, "f3": 0, "f6": 1},
        ],
    }


def test_fixture_invalid_change_does_not_count_as_flat_or_block_amount(test_only_batch):
    result = explain_members(test_only_batch, field_evidence(test_only_batch))
    assert (result["valid"], result["invalid"], result["total"]) == (3, 1, 4)
    assert result["counts"] == {"up": 1, "down": 1, "flat": 1, "up_share": 1 / 3}
    assert [row["code"] for row in result["leaders"]] == ["SH:600000", "SZ:000001", "SH:600519"]


@pytest.mark.parametrize(("missing", "counts", "leaders"), [
    ("change_unit", False, True), ("change_reference", False, True),
    ("amount_unit", True, False), ("amount_period", True, False),
    ("membership", False, False),
])
def test_only_dependent_metrics_are_withheld(test_only_batch, missing, counts, leaders):
    evidence = replace(field_evidence(test_only_batch), **{missing: None})
    result = explain_members(test_only_batch, evidence)
    assert (result["counts"] is not None) is counts
    assert (result["leaders"] is not None) is leaders
    assert result["total"] == 4


def test_incomplete_coverage_and_invalid_amount_fail_separately(test_only_batch):
    evidence = field_evidence(test_only_batch)
    test_only_batch["member_raw"][0]["f6"] = "NaN"
    result = explain_members(test_only_batch, evidence)
    assert result["counts"] is not None and result["leaders"] is None
    test_only_batch["member_coverage"]["total"] = 5
    result = explain_members(test_only_batch, evidence)
    assert not result["complete"] and result["counts"] is None and result["leaders"] is None


def test_known_route_allows_source_attribution_not_numeric_promotion(test_only_batch):
    # Model a verified live envelope in memory; the rows remain fixture values.
    batch = {**test_only_batch, "synthetic_test_only": False}
    assert field_evidence(batch) == FieldEvidence(membership=True)
    result = explain_members(batch, field_evidence(batch))
    assert result["counts"] is None and result["leaders"] is None
    assert field_evidence({**batch, "contract_hash": LEGACY_CONTRACT}) == FieldEvidence()
    assert field_evidence({**batch, "selected_name": " "}) == FieldEvidence()
