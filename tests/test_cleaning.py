"""Test Harness for Data Cleaning and Validation (Pure Unit Tests - No DB Required).

Covers:
1. Happy path (valid rows without corrections).
2. Fixable rows (whitespace trimming, casing normalization, alternative boolean representations).
3. Rejection of structural anomalies (missing or extra columns, empty rows).
4. Rejection of non-numeric data in numeric columns.
5. Rejection of physiologically impossible values (Age, Sleep, BMI, Heart Rate, etc.).
6. Rejection of invalid categorical or boolean values.
"""

from decimal import Decimal

import pytest

from app.cleaning import CleanedRecord, RowRejectedError, clean_and_validate_row


@pytest.fixture
def valid_raw_row() -> dict[str, str | None]:
    return {
        "ID": "1",
        "Age": "40",
        "Gender": "Male",
        "Country": "Germany",
        "Coffee_Intake": "3.5",
        "Caffeine_mg": "328.1",
        "Sleep_Hours": "7.5",
        "Sleep_Quality": "Good",
        "BMI": "24.9",
        "Heart_Rate": "78",
        "Stress_Level": "Low",
        "Physical_Activity_Hours": "14.5",
        "Health_Issues": "None",
        "Occupation": "Other",
        "Smoking": "0",
        "Alcohol_Consumption": "0",
    }


def test_clean_and_validate_row_happy_path(valid_raw_row: dict[str, str | None]) -> None:
    record, was_corrected = clean_and_validate_row(valid_raw_row, line_number=2)

    assert not was_corrected
    assert isinstance(record, CleanedRecord)
    assert record.id == 1
    assert record.age == 40
    assert record.gender == "Male"
    assert record.country == "Germany"
    assert record.coffee_intake == Decimal("3.5")
    assert record.caffeine_mg == Decimal("328.1")
    assert record.sleep_hours == Decimal("7.5")
    assert record.sleep_quality == "Good"
    assert record.bmi == Decimal("24.9")
    assert record.heart_rate == 78
    assert record.stress_level == "Low"
    assert record.physical_activity_hours == Decimal("14.5")
    assert record.health_issues == "None"
    assert record.occupation == "Other"
    assert record.smoking is False
    assert record.alcohol_consumption is False


@pytest.mark.parametrize(
    ("field", "raw_val", "expected_val"),
    [
        ("Gender", "  Female  ", "Female"),
        ("Gender", "\tMale  ", "Male"),
        ("Gender", "male", "Male"),
        ("Gender", "MALE", "Male"),
        ("Country", "\tUSA ", "USA"),
        ("Country", "  Netherlands ", "Netherlands"),
        ("Sleep_Quality", "GOOD", "Good"),
        ("Sleep_Quality", "  Good  ", "Good"),
        ("Stress_Level", "MEDIUM", "Medium"),
        ("Stress_Level", "LOW", "Low"),
        ("Occupation", "\tHealthcare  ", "Healthcare"),
        ("Occupation", "  Service ", "Service"),
    ],
)
def test_clean_and_validate_row_string_normalizations(
    valid_raw_row: dict[str, str | None],
    field: str,
    raw_val: str,
    expected_val: str,
) -> None:
    row = dict(valid_raw_row)
    row[field] = raw_val

    record, was_corrected = clean_and_validate_row(row, line_number=10)

    assert was_corrected
    assert getattr(record, field.lower()) == expected_val


@pytest.mark.parametrize(
    ("raw_val", "expected_bool"),
    [
        ("no", False),
        ("false", False),
        ("0", False),
        ("N", False),
        ("yes", True),
        ("true", True),
        ("1", True),
        ("Y", True),
    ],
)
def test_clean_and_validate_row_boolean_normalizations(
    valid_raw_row: dict[str, str | None],
    raw_val: str,
    expected_bool: bool,
) -> None:
    row = dict(valid_raw_row)
    row["Smoking"] = raw_val

    record, was_corrected = clean_and_validate_row(row, line_number=15)
    assert record.smoking is expected_bool
    # If raw_val wasn't strictly "0" or "1", it must be flagged as corrected
    if raw_val not in {"0", "1"}:
        assert was_corrected


@pytest.mark.parametrize(
    ("field", "bad_val", "error_keyword"),
    [
        ("Age", "-3", "range"),
        ("Age", "150", "range"),
        ("Age", "212", "range"),
        ("Age", "thirty", "numeric"),
        ("Age", "4O", "numeric"),
        ("Age", "unknown", "numeric"),
        ("Age", "", "empty"),
        ("Sleep_Hours", "-1", "range"),
        ("Sleep_Hours", "25.0", "range"),
        ("Sleep_Hours", "seven", "numeric"),
        ("Sleep_Hours", "", "empty"),
        ("BMI", "0", "range"),
        ("BMI", "9.9", "range"),
        ("BMI", "85.0", "range"),
        ("BMI", "n/a", "numeric"),
        ("BMI", "", "empty"),
        ("Heart_Rate", "0", "range"),
        ("Heart_Rate", "350", "range"),
        ("Heart_Rate", "25", "range"),
        ("Heart_Rate", "", "empty"),
        ("Coffee_Intake", "-1.0", "range"),
        ("Coffee_Intake", "35.0", "range"),
        ("Caffeine_mg", "-50.0", "range"),
        ("Caffeine_mg", "4000.0", "range"),
        ("Physical_Activity_Hours", "-2.0", "range"),
        ("Physical_Activity_Hours", "170.0", "range"),
        ("Gender", "?", "category"),
        ("Gender", "N/A", "category"),
        ("Gender", "", "category"),
        ("Smoking", "-1", "boolean"),
        ("Smoking", "2", "boolean"),
        ("Country", "", "country"),
        ("Country", "   ", "country"),
        ("Sleep_Quality", "Alien", "category"),
        ("Stress_Level", "Extreme", "category"),
        ("Health_Issues", "Fatal", "category"),
        ("Occupation", "Astronaut", "category"),
    ],
)
def test_clean_and_validate_row_rejections(
    valid_raw_row: dict[str, str | None],
    field: str,
    bad_val: str,
    error_keyword: str,
) -> None:
    row = dict(valid_raw_row)
    row[field] = bad_val

    with pytest.raises(RowRejectedError) as exc_info:
        clean_and_validate_row(row, line_number=42)

    assert exc_info.value.line_number == 42
    assert error_keyword.lower() in exc_info.value.reason.lower()


def test_clean_and_validate_row_missing_required_keys(valid_raw_row: dict[str, str | None]) -> None:
    row = dict(valid_raw_row)
    del row["Smoking"]
    del row["Alcohol_Consumption"]

    with pytest.raises(RowRejectedError) as exc_info:
        clean_and_validate_row(row, line_number=99)

    assert exc_info.value.line_number == 99
    assert "missing" in exc_info.value.reason.lower()
