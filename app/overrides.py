from app.merge import NORMALIZERS, norm_count, norm_yes_no
from app.models import FieldValue, MergedField

OVERRIDE_NORMALIZERS = {
    **NORMALIZERS,
    "loss_runs_present": norm_yes_no,
    "schedule_vehicle_count": norm_count,
}


def normalize_override(field: str, text: str) -> FieldValue:
    """Clean up a reviewer's typed correction the same way extracted values are cleaned.
    Raises merge.Invalid if it is not a valid value for the field."""
    return OVERRIDE_NORMALIZERS[field](text)


def apply_overrides(fields: dict[str, MergedField], overrides: dict[str, FieldValue]) -> dict[str, MergedField]:
    """Return the fields the rules should use. The extracted fields are not changed; overrides are
    kept separately and each corrected field is labelled as a human override, keeping its original
    state, value and evidence."""
    effective = {}
    for name, field in fields.items():
        if name not in overrides:
            effective[name] = field
            continue
        effective[name] = field.model_copy(
            update={
                "state": "KNOWN",
                "value": overrides[name],
                "human_override": True,
                "original_state": field.state,
                "original_value": field.value,
                "note": f"Human override (original: {field.state}, value {field.value!r}). {field.note}".strip(),
            }
        )
    return effective
