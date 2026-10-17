from app.models import MergedField
from app.rules import check_r1, check_r2, check_r3, check_r4, check_r5, evaluate

CHECKED = ["broker_email.txt", "application.pdf"]


def known(name: str, value) -> MergedField:
    return MergedField(name=name, state="KNOWN", value=value, checked_sources=CHECKED)


def state(name: str, field_state: str, checked: list[str] = CHECKED) -> MergedField:
    return MergedField(name=name, state=field_state, checked_sources=checked)


def all_pass_fields() -> dict[str, MergedField]:
    return {
        "business_name": known("business_name", "Example LLC"),
        "requested_effective_date": known("requested_effective_date", "2026-11-01"),
        "garaging_state": known("garaging_state", "CA"),
        "operations_description": known("operations_description", "Local delivery"),
        "declared_vehicle_count": known("declared_vehicle_count", 3),
        "schedule_vehicle_count": known("schedule_vehicle_count", 3),
        "operating_radius_miles": known("operating_radius_miles", 150),
        "hazardous_materials": known("hazardous_materials", False),
        "loss_runs_present": known("loss_runs_present", True),
    }


# --- R1 ---


def test_r1_pass_when_both_known():
    assert check_r1(known("business_name", "X"), known("requested_effective_date", "2026-11-01")).status == "PASS"


def test_r1_missing_field_is_flag():
    result = check_r1(known("business_name", "X"), state("requested_effective_date", "MISSING"))
    assert result.status == "FLAG"
    assert "requested_effective_date" in result.reason


def test_r1_missing_wins_over_conflict():
    assert check_r1(state("business_name", "CONFLICT"), state("requested_effective_date", "MISSING")).status == "FLAG"


def test_r1_conflict_or_invalid_without_absence_is_unknown():
    assert check_r1(state("business_name", "CONFLICT"), known("requested_effective_date", "2026-11-01")).status == "UNKNOWN"
    assert check_r1(known("business_name", "X"), state("requested_effective_date", "INVALID")).status == "UNKNOWN"


def test_r1_not_checked_because_llm_failed_is_unknown_not_flag():
    result = check_r1(state("business_name", "MISSING", checked=[]), known("requested_effective_date", "2026-11-01"))
    assert result.status == "UNKNOWN"


# --- R2 ---


def test_r2_equal_counts_pass():
    assert check_r2(known("declared_vehicle_count", 2), known("schedule_vehicle_count", 2)).status == "PASS"


def test_r2_unequal_counts_flag():
    result = check_r2(known("declared_vehicle_count", 4), known("schedule_vehicle_count", 3))
    assert result.status == "FLAG"
    assert result.values == {"declared_vehicle_count": 4, "schedule_vehicle_count": 3}


def test_r2_zero_vehicles_is_a_valid_count():
    assert check_r2(known("declared_vehicle_count", 0), known("schedule_vehicle_count", 0)).status == "PASS"


def test_r2_missing_conflict_or_unreadable_csv_is_unknown():
    assert check_r2(state("declared_vehicle_count", "MISSING"), known("schedule_vehicle_count", 3)).status == "UNKNOWN"
    assert check_r2(state("declared_vehicle_count", "CONFLICT"), known("schedule_vehicle_count", 3)).status == "UNKNOWN"
    assert check_r2(known("declared_vehicle_count", 3), state("schedule_vehicle_count", "INVALID")).status == "UNKNOWN"


# --- R3 ---


def test_r3_150_is_pass_and_150_point_1_is_flag():
    assert check_r3(known("operating_radius_miles", 150)).status == "PASS"
    assert check_r3(known("operating_radius_miles", 150.1)).status == "FLAG"
    assert check_r3(known("operating_radius_miles", 80)).status == "PASS"


def test_r3_missing_conflict_or_invalid_is_unknown():
    for field_state in ("MISSING", "CONFLICT", "INVALID"):
        assert check_r3(state("operating_radius_miles", field_state)).status == "UNKNOWN"


# --- R4 ---


def test_r4_explicit_no_pass_yes_flag():
    assert check_r4(known("hazardous_materials", False)).status == "PASS"
    assert check_r4(known("hazardous_materials", True)).status == "FLAG"


def test_r4_conflict_is_unknown():
    assert check_r4(state("hazardous_materials", "CONFLICT")).status == "UNKNOWN"
    assert check_r4(state("hazardous_materials", "MISSING")).status == "UNKNOWN"


# --- R5 ---


def test_r5_follows_loss_runs_field():
    assert check_r5(known("loss_runs_present", True)).status == "PASS"
    assert check_r5(known("loss_runs_present", False)).status == "FLAG"
    assert check_r5(state("loss_runs_present", "INVALID")).status == "UNKNOWN"


# --- overall ---


def test_all_pass_is_ready_for_review():
    result = evaluate(all_pass_fields())
    assert [r.status for r in result.rules] == ["PASS"] * 5
    assert result.overall == "READY_FOR_REVIEW"


def test_any_flag_or_unknown_needs_review():
    fields = all_pass_fields()
    fields["operating_radius_miles"] = known("operating_radius_miles", 150.1)
    assert evaluate(fields).overall == "NEEDS_REVIEW"
    fields = all_pass_fields()
    fields["hazardous_materials"] = state("hazardous_materials", "CONFLICT")
    assert evaluate(fields).overall == "NEEDS_REVIEW"


# --- more rule cases ---


def test_r1_both_missing_lists_both():
    result = check_r1(state("business_name", "MISSING"), state("requested_effective_date", "MISSING"))
    assert result.status == "FLAG"
    assert "business_name" in result.reason and "requested_effective_date" in result.reason


def test_r2_records_the_values_used_even_when_unknown():
    result = check_r2(state("declared_vehicle_count", "MISSING"), known("schedule_vehicle_count", 3))
    assert result.values == {"declared_vehicle_count": None, "schedule_vehicle_count": 3}
    assert result.states == {"declared_vehicle_count": "MISSING", "schedule_vehicle_count": "KNOWN"}


def test_r3_zero_radius_passes():
    assert check_r3(known("operating_radius_miles", 0)).status == "PASS"


def test_r5_reason_comes_from_the_loss_runs_evidence():
    field = MergedField(name="loss_runs_present", state="KNOWN", value=False, checked_sources=CHECKED,
                        note="No loss runs or loss history report among the uploaded files")
    assert check_r5(field).reason == "No loss runs or loss history report among the uploaded files"
