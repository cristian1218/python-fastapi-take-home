"""Data cleaning, normalization, and validation domain module.

Implements pure functions and immutable data structures to validate and normalize
raw CSV records according to the biological plausibility contract and domain constraints.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Plausibility bounds (enforced at ingestion and database level)
MIN_AGE = 18
MAX_AGE = 120

MIN_SLEEP_HOURS = Decimal("0.0")
MAX_SLEEP_HOURS = Decimal("24.0")

MIN_BMI = Decimal("10.0")
MAX_BMI = Decimal("80.0")

MIN_HEART_RATE = 30
MAX_HEART_RATE = 220

MIN_COFFEE_INTAKE = Decimal("0.0")
MAX_COFFEE_INTAKE = Decimal("30.0")

MIN_CAFFEINE_MG = Decimal("0.0")
MAX_CAFFEINE_MG = Decimal("3000.0")

MIN_PHYSICAL_ACTIVITY_HOURS = Decimal("0.0")
MAX_PHYSICAL_ACTIVITY_HOURS = Decimal("168.0")

# Discrete domain sets
VALID_GENDERS = {"Male", "Female", "Other"}
VALID_SLEEP_QUALITY = {"Poor", "Fair", "Good", "Excellent"}
VALID_STRESS_LEVEL = {"Low", "Medium", "High"}
VALID_HEALTH_ISSUES = {"None", "Mild", "Moderate", "Severe"}
VALID_OCCUPATIONS = {"Office", "Healthcare", "Service", "Student", "Other"}

REQUIRED_COLUMNS = (
    "ID",
    "Age",
    "Gender",
    "Country",
    "Coffee_Intake",
    "Caffeine_mg",
    "Sleep_Hours",
    "Sleep_Quality",
    "BMI",
    "Heart_Rate",
    "Stress_Level",
    "Physical_Activity_Hours",
    "Health_Issues",
    "Occupation",
    "Smoking",
    "Alcohol_Consumption",
)


class RowRejectedError(Exception):
    """Raised when a raw row violates structural, biological, or domain constraints."""

    def __init__(self, line_number: int, reason: str) -> None:
        self.line_number = line_number
        self.reason = reason
        super().__init__(f"Line {line_number} rejected: {reason}")


@dataclass(frozen=True, slots=True)
class CleanedRecord:
    """Immutable domain representation of a cleaned and validated coffee health record."""

    id: int
    age: int
    gender: str
    country: str
    coffee_intake: Decimal
    caffeine_mg: Decimal
    sleep_hours: Decimal
    sleep_quality: str
    bmi: Decimal
    heart_rate: int
    stress_level: str
    physical_activity_hours: Decimal
    health_issues: str
    occupation: str
    smoking: bool
    alcohol_consumption: bool


def _clean_str(val: str | None) -> str:
    if val is None:
        return ""
    return val.strip()


def _normalize_category(
    raw_val: str | None,
    valid_set: set[str],
    field_name: str,
    line_number: int,
) -> tuple[str, bool]:
    cleaned = _clean_str(raw_val)
    if not cleaned:
        raise RowRejectedError(line_number, f"{field_name} category is empty or missing")

    # Check case-insensitive match against allowed domain
    match = next((item for item in valid_set if item.casefold() == cleaned.casefold()), None)
    if match is None:
        raise RowRejectedError(
            line_number,
            f"Invalid {field_name} category: '{cleaned}' (allowed: {sorted(valid_set)})",
        )

    was_corrected = raw_val != match
    return match, was_corrected


def _normalize_country(raw_val: str | None, line_number: int) -> tuple[str, bool]:
    cleaned = _clean_str(raw_val)
    if not cleaned:
        raise RowRejectedError(line_number, "Country name cannot be empty")

    # Preserve special acronyms (USA, UK) or Title case
    upper = cleaned.upper()
    normalized = upper if upper in {"USA", "UK"} else cleaned.title()

    was_corrected = raw_val != normalized
    return normalized, was_corrected


def _normalize_bool(raw_val: str | None, field_name: str, line_number: int) -> tuple[bool, bool]:
    cleaned = _clean_str(raw_val).casefold()
    if not cleaned:
        raise RowRejectedError(line_number, f"{field_name} boolean is empty or missing")

    if cleaned in {"1", "true", "yes", "y"}:
        parsed = True
    elif cleaned in {"0", "false", "no", "n"}:
        parsed = False
    else:
        raise RowRejectedError(
            line_number,
            f"Invalid {field_name} boolean value: '{raw_val}'",
        )

    # In raw CSV, strictly '0' or '1' is the original format. Anything else is a correction.
    was_corrected = raw_val not in {"0", "1"}
    return parsed, was_corrected


def _parse_int(
    raw_val: str | None,
    field_name: str,
    line_number: int,
    min_val: int,
    max_val: int,
) -> int:
    cleaned = _clean_str(raw_val)
    if not cleaned:
        raise RowRejectedError(line_number, f"{field_name} cannot be empty")

    try:
        val = int(cleaned)
    except ValueError as err:
        raise RowRejectedError(
            line_number,
            f"{field_name} is not a valid numeric integer: '{raw_val}'",
        ) from err

    if val < min_val or val > max_val:
        raise RowRejectedError(
            line_number,
            f"{field_name} value {val} out of biological range [{min_val}, {max_val}]",
        )
    return val


def _parse_decimal(
    raw_val: str | None,
    field_name: str,
    line_number: int,
    min_val: Decimal,
    max_val: Decimal,
) -> Decimal:
    cleaned = _clean_str(raw_val)
    if not cleaned:
        raise RowRejectedError(line_number, f"{field_name} cannot be empty")

    try:
        val = Decimal(cleaned)
    except InvalidOperation as err:
        raise RowRejectedError(
            line_number,
            f"{field_name} is not a valid numeric decimal: '{raw_val}'",
        ) from err

    if val < min_val or val > max_val:
        raise RowRejectedError(
            line_number,
            f"{field_name} value {val} out of biological range [{min_val}, {max_val}]",
        )
    return val


def clean_and_validate_row(
    raw_row: dict[str, str | None],
    line_number: int,
) -> tuple[CleanedRecord, bool]:
    """Parse, clean, and validate a single CSV record.

    Returns:
        tuple[CleanedRecord, bool]: The validated record and a boolean indicating
        if any correction (whitespace stripping, casing, boolean mapping) was applied.

    Raises:
        RowRejectedError: If the row violates domain, structural, or plausibility rules.
    """
    # 1. Structural check: all required columns must exist in dictionary keys
    missing_cols = [col for col in REQUIRED_COLUMNS if col not in raw_row]
    if missing_cols:
        raise RowRejectedError(
            line_number,
            f"Missing required columns: {missing_cols}",
        )

    corrections: list[bool] = []

    # 2. Parse and validate ID (must be positive integer)
    record_id = _parse_int(raw_row["ID"], "ID", line_number, min_val=1, max_val=2_147_483_647)

    # 3. Parse and validate Age
    age = _parse_int(raw_row["Age"], "Age", line_number, min_val=MIN_AGE, max_val=MAX_AGE)

    # 4. Strings & Categoricals
    gender, g_corr = _normalize_category(raw_row["Gender"], VALID_GENDERS, "Gender", line_number)
    corrections.append(g_corr)

    country, c_corr = _normalize_country(raw_row["Country"], line_number)
    corrections.append(c_corr)

    sleep_quality, sq_corr = _normalize_category(
        raw_row["Sleep_Quality"],
        VALID_SLEEP_QUALITY,
        "Sleep_Quality",
        line_number,
    )
    corrections.append(sq_corr)

    stress_level, sl_corr = _normalize_category(
        raw_row["Stress_Level"],
        VALID_STRESS_LEVEL,
        "Stress_Level",
        line_number,
    )
    corrections.append(sl_corr)

    health_issues, hi_corr = _normalize_category(
        raw_row["Health_Issues"],
        VALID_HEALTH_ISSUES,
        "Health_Issues",
        line_number,
    )
    corrections.append(hi_corr)

    occupation, occ_corr = _normalize_category(
        raw_row["Occupation"],
        VALID_OCCUPATIONS,
        "Occupation",
        line_number,
    )
    corrections.append(occ_corr)

    # 5. Booleans
    smoking, smk_corr = _normalize_bool(raw_row["Smoking"], "Smoking", line_number)
    corrections.append(smk_corr)

    alcohol, alc_corr = _normalize_bool(
        raw_row["Alcohol_Consumption"],
        "Alcohol_Consumption",
        line_number,
    )
    corrections.append(alc_corr)

    # 6. Decimals and Integers with physiological bounds
    coffee_intake = _parse_decimal(
        raw_row["Coffee_Intake"],
        "Coffee_Intake",
        line_number,
        min_val=MIN_COFFEE_INTAKE,
        max_val=MAX_COFFEE_INTAKE,
    )
    caffeine_mg = _parse_decimal(
        raw_row["Caffeine_mg"],
        "Caffeine_mg",
        line_number,
        min_val=MIN_CAFFEINE_MG,
        max_val=MAX_CAFFEINE_MG,
    )
    sleep_hours = _parse_decimal(
        raw_row["Sleep_Hours"],
        "Sleep_Hours",
        line_number,
        min_val=MIN_SLEEP_HOURS,
        max_val=MAX_SLEEP_HOURS,
    )
    bmi = _parse_decimal(
        raw_row["BMI"],
        "BMI",
        line_number,
        min_val=MIN_BMI,
        max_val=MAX_BMI,
    )
    heart_rate = _parse_int(
        raw_row["Heart_Rate"],
        "Heart_Rate",
        line_number,
        min_val=MIN_HEART_RATE,
        max_val=MAX_HEART_RATE,
    )
    physical_activity = _parse_decimal(
        raw_row["Physical_Activity_Hours"],
        "Physical_Activity_Hours",
        line_number,
        min_val=MIN_PHYSICAL_ACTIVITY_HOURS,
        max_val=MAX_PHYSICAL_ACTIVITY_HOURS,
    )

    record = CleanedRecord(
        id=record_id,
        age=age,
        gender=gender,
        country=country,
        coffee_intake=coffee_intake,
        caffeine_mg=caffeine_mg,
        sleep_hours=sleep_hours,
        sleep_quality=sleep_quality,
        bmi=bmi,
        heart_rate=heart_rate,
        stress_level=stress_level,
        physical_activity_hours=physical_activity,
        health_issues=health_issues,
        occupation=occupation,
        smoking=smoking,
        alcohol_consumption=alcohol,
    )

    was_corrected = any(corrections)
    return record, was_corrected
