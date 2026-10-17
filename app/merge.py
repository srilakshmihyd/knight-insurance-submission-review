import re
from datetime import datetime

from app.extract import squash
from app.models import (
    LLM_FIELDS,
    CsvCount,
    Evidence,
    FieldValue,
    LossRunsResult,
    MergedField,
    SourceExtraction,
    SourceField,
)


class Invalid(ValueError):
    """Raised by a normalizer when a stated answer cannot be turned into a valid value."""


# --- normalizers: stated text -> clean value, or raise Invalid ---


def norm_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        raise Invalid("empty answer")
    return cleaned


# Accepted ways of writing a date. Slashed dates are read the US way (month/day/year).
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y")


def norm_date(text: str) -> str:
    """'2026-11-01', '11/01/2026', 'Nov 1, 2026', 'November 1st, 2026' and '1 November 2026' all
    become '2026-11-01'. Anything else, or an impossible date like Feb 30, is INVALID."""
    cleaned = re.sub(r"\s+", " ", text).strip().rstrip(".").replace(".", "")
    cleaned = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", cleaned, flags=re.IGNORECASE)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    raise Invalid(f"'{text.strip()}' is not a recognisable calendar date")


STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


def norm_state(text: str) -> str:
    """A US state name or its two-letter code (any case) becomes the code, e.g. Texas / tx -> TX."""
    cleaned = re.sub(r"\s+", " ", text).strip().rstrip(".").lower()
    if cleaned in STATES:
        return STATES[cleaned]
    if cleaned.upper() in STATES.values():
        return cleaned.upper()
    raise Invalid(f"'{text.strip()}' is not a US state")


NUMBER_WORDS = {word: n for n, word in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty".split()
)}
RADIUS_UNITS = r"miles?|mi"
COUNT_UNITS = r"vehicles?|units?|power units?|trucks?|autos?"


def _number(text: str, units: str) -> float:
    """A plain number, optionally with thousands commas, a unit and a number word:
    '150', '150 miles', '150-mile', '1,200 mi', '4 vehicles', 'four trucks'.
    Hedged answers ('about 150', 'under 100') do not match and are INVALID."""
    cleaned = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text.strip())
    match = re.fullmatch(rf"(-?\d+(?:\.\d+)?|[a-z]+)(?:[\s-]*(?:{units}))?\.?", cleaned, re.IGNORECASE)
    if match:
        number = match.group(1).lower()
        if number in NUMBER_WORDS:
            return float(NUMBER_WORDS[number])
        if not number.isalpha():
            return float(number)
    raise Invalid(f"'{text.strip()}' is not a number")


def norm_count(text: str) -> int:
    number = _number(text, COUNT_UNITS)
    if number < 0:
        raise Invalid(f"'{text.strip()}' is negative")
    if not number.is_integer():
        raise Invalid(f"'{text.strip()}' is not a whole number")
    return int(number)


def norm_radius(text: str) -> int | float:
    number = _number(text, RADIUS_UNITS)
    if number < 0:
        raise Invalid(f"'{text.strip()}' is negative")
    return int(number) if number.is_integer() else number  # 150.0 -> 150


def norm_yes_no(text: str) -> bool:
    cleaned = text.strip().rstrip(".").lower()
    if cleaned in ("yes", "y", "true"):
        return True
    if cleaned in ("no", "n", "false"):
        return False
    raise Invalid(f"'{text.strip()}' is not an explicit Yes or No")


NORMALIZERS = {
    "business_name": norm_text,
    "requested_effective_date": norm_date,
    "garaging_state": norm_state,
    "operations_description": norm_text,
    "declared_vehicle_count": norm_count,
    "operating_radius_miles": norm_radius,
    "hazardous_materials": norm_yes_no,
}


def compare_key(field: str, value: FieldValue) -> FieldValue:
    """How two clean values are compared. Names ignore case; descriptions ignore case and a final period."""
    if field == "business_name":
        return value.casefold()
    if field == "operations_description":
        return value.rstrip(".").casefold()
    return value


# --- combining sources ---


def ungrounded(found: SourceField) -> str | None:
    """Why a stated answer cannot be trusted, or None if it can. The excerpt must have been found in
    the source text (it has a locator) and the answer must appear inside that excerpt, so an
    invented value with a real-looking quote is not shown as KNOWN."""
    if not found.excerpt or found.locator is None:
        return "its quoted text was not found in the source"
    if not re.search(rf"(?<!\w){re.escape(squash(found.value_text))}(?!\w)", squash(found.excerpt)):
        return f"answer '{found.value_text}' does not appear in its quoted text"
    return None


def merge_field(field: str, extractions: list[SourceExtraction]) -> MergedField:
    """Combine one LLM field across the sources (the email and the application PDF)."""
    checked = [e.source_file for e in extractions if e.error is None]
    failed = [e.source_file for e in extractions if e.error is not None]

    stated: list[Evidence] = []  # sources that gave a valid answer
    invalid: list[Evidence] = []  # sources that gave an answer we cannot use
    not_provided: list[Evidence] = []  # sources that say "Not provided" (excerpt but no value)
    problems: list[str] = []

    for extraction in extractions:
        for found in extraction.fields:
            if found.field != field:
                continue
            evidence = Evidence(
                source_file=found.source_file,
                locator=found.locator,
                excerpt=found.excerpt,
                value_text=found.value_text,
            )
            if found.value_text is None:
                if found.excerpt:
                    not_provided.append(evidence)
                continue
            problem = ungrounded(found)
            if problem:
                invalid.append(evidence)
                problems.append(f"{found.source_file}: {problem}")
                continue
            try:
                evidence.value = NORMALIZERS[field](found.value_text)
                stated.append(evidence)
            except Invalid as reason:
                invalid.append(evidence)
                problems.append(f"{found.source_file}: {reason}")

    failed_note = f" Not extracted (LLM unavailable): {', '.join(failed)}." if failed else ""
    distinct = {compare_key(field, e.value) for e in stated}

    if len(distinct) > 1:
        answers = ", ".join(f"{e.source_file} says '{e.value_text}'" for e in stated)
        return MergedField(
            name=field,
            state="CONFLICT",
            evidence=stated + invalid,
            checked_sources=checked,
            note=f"Sources disagree: {answers}.{failed_note}",
        )
    if invalid:
        return MergedField(
            name=field,
            state="INVALID",
            evidence=stated + invalid,
            checked_sources=checked,
            note="; ".join(problems) + "." + failed_note,
        )
    if stated:
        return MergedField(
            name=field,
            state="KNOWN",
            value=stated[0].value,
            evidence=stated,
            checked_sources=checked,
            note=failed_note.strip(),
        )
    return MergedField(
        name=field,
        state="MISSING",
        evidence=not_provided,
        checked_sources=checked,
        note=f"Not stated in: {', '.join(checked) or 'no sources'}.{failed_note}",
    )


def merge_loss_runs(loss_runs: LossRunsResult) -> MergedField:
    """loss_runs_present comes only from the uploaded files (the parser), never from email text."""
    checked = [entry.name for entry in loss_runs.inventory]
    evidence = [
        Evidence(
            source_file=loss_runs.source_file or "uploaded attachment inventory",
            locator=loss_runs.locator,
            excerpt=loss_runs.excerpt or loss_runs.reason,
            value=loss_runs.present,
        )
    ]
    if loss_runs.present is None:
        return MergedField(
            name="loss_runs_present",
            state="INVALID",
            evidence=evidence,
            checked_sources=checked,
            note=loss_runs.reason,
        )
    return MergedField(
        name="loss_runs_present",
        state="KNOWN",
        value=loss_runs.present,
        evidence=evidence,
        checked_sources=checked,
        note=loss_runs.reason,
    )


def merge_schedule_count(csv_count: CsvCount | None) -> MergedField:
    """schedule_vehicle_count comes from the CSV row count. No CSV -> MISSING; unparseable -> INVALID."""
    if csv_count is None:
        return MergedField(name="schedule_vehicle_count", state="MISSING", note="No vehicle schedule CSV uploaded.")

    evidence = [
        Evidence(
            source_file=csv_count.name,
            locator=csv_count.locator or None,
            excerpt=f"{csv_count.row_count} nonblank data row(s)" if csv_count.error is None else csv_count.error,
            value=csv_count.row_count,
        )
    ]
    if csv_count.error:
        return MergedField(
            name="schedule_vehicle_count",
            state="INVALID",
            evidence=evidence,
            checked_sources=[csv_count.name],
            note=csv_count.error,
        )
    return MergedField(
        name="schedule_vehicle_count",
        state="KNOWN",
        value=csv_count.row_count,
        evidence=evidence,
        checked_sources=[csv_count.name],
    )


def merge_all(
    extractions: list[SourceExtraction], loss_runs: LossRunsResult, csv_count: CsvCount | None
) -> dict[str, MergedField]:
    """All 9 fields: the 7 LLM fields, loss_runs_present and schedule_vehicle_count."""
    merged = {field: merge_field(field, extractions) for field in LLM_FIELDS}
    merged["loss_runs_present"] = merge_loss_runs(loss_runs)
    merged["schedule_vehicle_count"] = merge_schedule_count(csv_count)
    return merged
