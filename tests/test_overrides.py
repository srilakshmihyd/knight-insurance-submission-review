import pytest

from app.merge import Invalid
from app.models import Evidence, MergedField
from app.overrides import apply_overrides, normalize_override
from app.rules import evaluate

CHECKED = ["broker_email.txt", "application.pdf"]


def known(name: str, value) -> MergedField:
    return MergedField(name=name, state="KNOWN", value=value, checked_sources=CHECKED)


def fields_with_radius_conflict() -> dict[str, MergedField]:
    radius = MergedField(
        name="operating_radius_miles",
        state="CONFLICT",
        checked_sources=CHECKED,
        evidence=[
            Evidence(source_file="broker_email.txt", locator="email body", excerpt="radius: 180", value_text="180", value=180),
            Evidence(source_file="application.pdf", locator="PDF page 1", excerpt="radius 80", value_text="80", value=80),
        ],
    )
    return {
        "business_name": known("business_name", "Example LLC"),
        "requested_effective_date": known("requested_effective_date", "2026-11-01"),
        "garaging_state": known("garaging_state", "CA"),
        "operations_description": known("operations_description", "Local delivery"),
        "declared_vehicle_count": known("declared_vehicle_count", 3),
        "schedule_vehicle_count": known("schedule_vehicle_count", 3),
        "operating_radius_miles": radius,
        "hazardous_materials": known("hazardous_materials", False),
        "loss_runs_present": known("loss_runs_present", True),
    }


def test_override_is_labelled_and_keeps_original_and_evidence():
    fields = fields_with_radius_conflict()
    effective = apply_overrides(fields, {"operating_radius_miles": 80})
    radius = effective["operating_radius_miles"]
    assert radius.human_override
    assert (radius.state, radius.value) == ("KNOWN", 80)
    assert (radius.original_state, radius.original_value) == ("CONFLICT", None)
    assert radius.evidence == fields["operating_radius_miles"].evidence
    # the extracted fields themselves are not changed
    assert fields["operating_radius_miles"].state == "CONFLICT"
    assert not fields["operating_radius_miles"].human_override


def test_rules_rerun_with_the_corrected_value():
    fields = fields_with_radius_conflict()
    assert evaluate(fields).rules[2].status == "UNKNOWN"
    assert evaluate(apply_overrides(fields, {"operating_radius_miles": 80})).rules[2].status == "PASS"
    assert evaluate(apply_overrides(fields, {"operating_radius_miles": 180})).rules[2].status == "FLAG"
    assert evaluate(apply_overrides(fields, {"operating_radius_miles": 80})).overall == "READY_FOR_REVIEW"


def test_no_overrides_leaves_fields_as_extracted():
    fields = fields_with_radius_conflict()
    assert apply_overrides(fields, {}) == fields


def test_typed_correction_is_normalized_or_rejected():
    assert normalize_override("operating_radius_miles", "150.0") == 150
    assert normalize_override("garaging_state", "California") == "CA"
    assert normalize_override("loss_runs_present", "Yes") is True
    assert normalize_override("schedule_vehicle_count", "4") == 4
    with pytest.raises(Invalid):
        normalize_override("hazardous_materials", "maybe")


def test_override_of_missing_date_makes_r1_pass():
    fields = fields_with_radius_conflict()
    fields["requested_effective_date"] = MergedField(name="requested_effective_date", state="MISSING",
                                                     checked_sources=CHECKED)
    assert evaluate(fields).rules[0].status == "FLAG"
    effective = apply_overrides(fields, {"requested_effective_date": normalize_override("requested_effective_date", "2026-11-01")})
    assert evaluate(effective).rules[0].status == "PASS"


def test_override_of_loss_runs_and_schedule_count():
    fields = fields_with_radius_conflict()
    fields["loss_runs_present"] = known("loss_runs_present", False)
    fields["schedule_vehicle_count"] = known("schedule_vehicle_count", 2)
    before = {r.rule_id: r.status for r in evaluate(fields).rules}
    assert (before["R2"], before["R5"]) == ("FLAG", "FLAG")
    effective = apply_overrides(fields, {"loss_runs_present": True, "schedule_vehicle_count": 3})
    after = {r.rule_id: r.status for r in evaluate(effective).rules}
    assert (after["R2"], after["R5"]) == ("PASS", "PASS")


def test_invalid_typed_date_is_rejected():
    with pytest.raises(Invalid):
        normalize_override("requested_effective_date", "2026-02-30")
