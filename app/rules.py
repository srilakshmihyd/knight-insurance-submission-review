from app.models import Evaluation, MergedField, RuleResult, RuleStatus

RADIUS_LIMIT_MILES = 150


def _result(rule_id: str, name: str, status: RuleStatus, reason: str, *inputs: MergedField) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        name=name,
        status=status,
        reason=reason,
        values={f.name: f.value for f in inputs},
        states={f.name: f.state for f in inputs},
    )


def _is_absent(field: MergedField) -> bool:
    """MISSING after at least one source was actually read. If no source could be read
    (e.g. the LLM failed for every file), the value is not known to be absent."""
    return field.state == "MISSING" and bool(field.checked_sources)


def _problem(field: MergedField) -> str:
    """Short text for why a field cannot be used, e.g. 'operating_radius_miles is CONFLICT'."""
    if field.state == "MISSING" and not field.checked_sources:
        return f"{field.name} could not be checked (no source was read)"
    return f"{field.name} is {field.state}"


def check_r1(name: MergedField, effective_date: MergedField) -> RuleResult:
    """R1: FLAG if either is absent (takes precedence); UNKNOWN if not absent but unresolved; else PASS."""
    title = "Required identity and effective date"
    fields = [name, effective_date]
    absent = [f for f in fields if _is_absent(f)]
    if absent:
        return _result("R1", title, "FLAG", "Absent: " + ", ".join(f.name for f in absent), *fields)
    unresolved = [f for f in fields if f.state != "KNOWN"]
    if unresolved:
        return _result("R1", title, "UNKNOWN", "; ".join(_problem(f) for f in unresolved), *fields)
    return _result("R1", title, "PASS", "Business name and effective date are present and valid", *fields)


def check_r2(declared: MergedField, schedule: MergedField) -> RuleResult:
    """R2: both counts known -> equal PASS / different FLAG; otherwise UNKNOWN."""
    title = "Vehicle count reconciliation"
    unresolved = [f for f in (declared, schedule) if f.state != "KNOWN"]
    if unresolved:
        return _result("R2", title, "UNKNOWN", "; ".join(_problem(f) for f in unresolved), declared, schedule)
    if declared.value == schedule.value:
        return _result(
            "R2", title, "PASS", f"Declared count {declared.value} equals schedule count {schedule.value}",
            declared, schedule,
        )
    return _result(
        "R2", title, "FLAG", f"Declared count {declared.value} differs from schedule count {schedule.value}",
        declared, schedule,
    )


def check_r3(radius: MergedField) -> RuleResult:
    """R3: unresolved radius -> UNKNOWN before any threshold test; > 150 FLAG; <= 150 PASS."""
    title = "Operating radius"
    if radius.state != "KNOWN":
        return _result("R3", title, "UNKNOWN", _problem(radius), radius)
    if radius.value > RADIUS_LIMIT_MILES:
        return _result("R3", title, "FLAG", f"Radius {radius.value} miles is above {RADIUS_LIMIT_MILES}", radius)
    return _result("R3", title, "PASS", f"Radius {radius.value} miles is {RADIUS_LIMIT_MILES} or less", radius)


def check_r4(hazmat: MergedField) -> RuleResult:
    """R4: unresolved answer -> UNKNOWN; explicit Yes FLAG; explicit No PASS."""
    title = "Hazardous materials"
    if hazmat.state != "KNOWN":
        return _result("R4", title, "UNKNOWN", _problem(hazmat), hazmat)
    if hazmat.value is True:
        return _result("R4", title, "FLAG", "Hazardous materials: explicit Yes", hazmat)
    return _result("R4", title, "PASS", "Hazardous materials: explicit No", hazmat)


def check_r5(loss_runs: MergedField) -> RuleResult:
    """R5: from the uploaded files only. Report attached PASS; none FLAG; unreadable/unclassifiable UNKNOWN."""
    title = "Loss runs attachment"
    if loss_runs.state != "KNOWN":
        return _result("R5", title, "UNKNOWN", loss_runs.note or _problem(loss_runs), loss_runs)
    if loss_runs.value is True:
        return _result("R5", title, "PASS", loss_runs.note or "A loss runs report is attached", loss_runs)
    return _result("R5", title, "FLAG", loss_runs.note or "No loss runs report is attached", loss_runs)


def evaluate(fields: dict[str, MergedField]) -> Evaluation:
    """Run R1-R5 on the merged fields. READY_FOR_REVIEW only if all five PASS."""
    rules = [
        check_r1(fields["business_name"], fields["requested_effective_date"]),
        check_r2(fields["declared_vehicle_count"], fields["schedule_vehicle_count"]),
        check_r3(fields["operating_radius_miles"]),
        check_r4(fields["hazardous_materials"]),
        check_r5(fields["loss_runs_present"]),
    ]
    overall = "READY_FOR_REVIEW" if all(r.status == "PASS" for r in rules) else "NEEDS_REVIEW"
    return Evaluation(rules=rules, overall=overall)
