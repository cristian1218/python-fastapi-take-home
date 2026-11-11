"""Tests for the ingestion streaming and accounting logic (scripts/load_data.py).

Tests parsing, validation, duplicate tracking, and accounting equation
without requiring a live database connection.
"""

import io
from decimal import Decimal
from unittest.mock import MagicMock

from app.cleaning import CleanedRecord
from scripts.load_data import insert_records_batch, stream_and_validate_csv

HEADER = (
    "ID,Age,Gender,Country,Coffee_Intake,Caffeine_mg,Sleep_Hours,Sleep_Quality,BMI,"
    "Heart_Rate,Stress_Level,Physical_Activity_Hours,Health_Issues,Occupation,"
    "Smoking,Alcohol_Consumption\n"
)

SAMPLE_CSV_CONTENT = (
    HEADER
    + "1,40,Male,Germany,3.5,328.1,7.5,Good,24.9,78,Low,14.5,None,Other,0,0\n"
    + "2,33,Male,Germany,1.0,94.1,6.2,Good,20.0,67,Low,11.0,None,Service,no,1\n"
    + "3,-5,Male,Brazil,5.3,503.7,5.9,Fair,22.7,59,Medium,11.2,Mild,Office,0,0\n"
    + "1,40,Male,Germany,3.5,328.1,7.5,Good,24.9,78,Low,14.5,None,Other,0,0\n"
    + "2,25,Male,Germany,1.0,94.1,6.2,Good,20.0,67,Low,11.0,None,Service,0,1\n"
    + "\n"
    + "4,45,Female,France,2.0,200.0,8.0,Good,22.0,65,Low,5.0,None,Office\n"
)


def test_stream_and_validate_csv_in_memory_dry_run() -> None:
    csv_file = io.StringIO(SAMPLE_CSV_CONTENT.strip())
    rejected_logs: list[tuple[int, str]] = []
    duplicate_logs: list[tuple[int, int, str]] = []

    def on_reject(line: int, reason: str) -> None:
        rejected_logs.append((line, reason))

    def on_duplicate(line: int, record_id: int, reason: str) -> None:
        duplicate_logs.append((line, record_id, reason))

    records_iter, stats = stream_and_validate_csv(
        csv_file,
        on_reject=on_reject,
        on_duplicate=on_duplicate,
        track_duplicates_in_memory=True,
    )
    records = list(records_iter)

    # In SAMPLE_CSV_CONTENT:
    # Line 2 (ID 1): Valid canonical -> Yielded, loaded
    # Line 3 (ID 2): Fixable ('no' -> False) -> Yielded, loaded, corrected
    # Line 4 (ID 3): Rejected (Age = -5 out of range) -> Not yielded, rejected
    # Line 5 (ID 1): Identical duplicate -> Not yielded, duplicated
    # Line 6 (ID 2): Conflicting duplicate (Age 25 vs 33) -> Not yielded, duplicated
    # Line 7: Empty line -> Skipped
    # Line 8 (ID 4): Rejected (missing columns: 13 instead of 16) -> Not yielded, rejected

    assert len(records) == 2
    assert stats.read == 6  # 6 non-empty rows
    assert stats.loaded == 2
    assert stats.corrected == 1
    assert stats.rejected == 2
    assert stats.duplicated == 2
    assert stats.is_balanced

    assert len(rejected_logs) == 2
    assert rejected_logs[0][0] == 4  # Line 4
    assert "range" in rejected_logs[0][1].lower()
    assert rejected_logs[1][0] == 8  # Line 8
    assert "columns" in rejected_logs[1][1].lower()

    assert len(duplicate_logs) == 2
    assert duplicate_logs[0][1] == 1  # ID 1
    assert duplicate_logs[1][1] == 2  # ID 2


def test_stream_and_validate_csv_db_streaming_mode() -> None:
    """When track_duplicates_in_memory=False, memory is O(1) and Postgres dedupes."""
    csv_file = io.StringIO(SAMPLE_CSV_CONTENT.strip())

    records_iter, stats = stream_and_validate_csv(
        csv_file,
        track_duplicates_in_memory=False,
    )
    records = list(records_iter)

    # All non-rejected records (including potential duplicates) are yielded for ON CONFLICT
    assert len(records) == 4
    assert stats.read == 6
    assert stats.loaded == 4
    assert stats.rejected == 2
    assert stats.duplicated == 0


def test_insert_records_batch_execution() -> None:
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [(1,), (2,)]
    mock_cursor.nextset.return_value = False

    sample_records = [
        CleanedRecord(
            id=1,
            age=30,
            gender="Male",
            country="Germany",
            coffee_intake=Decimal("2.0"),
            caffeine_mg=Decimal("150.0"),
            sleep_hours=Decimal("7.0"),
            sleep_quality="Good",
            bmi=Decimal("22.0"),
            heart_rate=70,
            stress_level="Low",
            physical_activity_hours=Decimal("5.0"),
            health_issues="None",
            occupation="Office",
            smoking=False,
            alcohol_consumption=False,
        )
    ]

    inserted_count = insert_records_batch(mock_conn, sample_records)

    assert mock_cursor.executemany.called
    assert inserted_count == 2
