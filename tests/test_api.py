"""Test harness for FastAPI serving, filtering, validation, and pagination.

Covers:
1. Validation errors (HTTP 422) for invalid range combinations (min_age > max_age, etc.).
2. Keyset pagination logic and response schema structure.
3. Filtering query parameter validation.
4. Summary endpoint validation.
"""

from collections.abc import Iterator
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.db import get_conn
from app.main import app


@pytest.fixture
def standalone_client() -> Iterator[TestClient]:
    """Client for testing endpoints with mocked database connections (no DB required)."""
    mock_conn = MagicMock()
    app.dependency_overrides[get_conn] = lambda: mock_conn
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_records_validation_min_max_age_conflict(standalone_client: TestClient) -> None:
    response = standalone_client.get("/records?min_age=50&max_age=30")
    assert response.status_code == 422
    assert "min_age cannot be greater than max_age" in response.text


def test_records_validation_min_max_coffee_conflict(standalone_client: TestClient) -> None:
    response = standalone_client.get("/records?min_coffee_intake=5.0&max_coffee_intake=2.0")
    assert response.status_code == 422
    assert "min_coffee_intake cannot be greater than max_coffee_intake" in response.text


def test_records_validation_min_max_bmi_conflict(standalone_client: TestClient) -> None:
    response = standalone_client.get("/records?min_bmi=35.0&max_bmi=20.0")
    assert response.status_code == 422
    assert "min_bmi cannot be greater than max_bmi" in response.text


def test_records_validation_invalid_limit(standalone_client: TestClient) -> None:
    response = standalone_client.get("/records?limit=0")
    assert response.status_code == 422

    response = standalone_client.get("/records?limit=150")
    assert response.status_code == 422


def test_records_keyset_pagination_mocked(standalone_client: TestClient) -> None:
    # Mock database connection to return 3 rows when limit=2 (has_more = True)
    mock_conn = MagicMock()
    mock_rows = [
        {
            "id": 1,
            "age": 30,
            "gender": "Male",
            "country": "Germany",
            "coffee_intake": Decimal("2.0"),
            "caffeine_mg": Decimal("180.0"),
            "sleep_hours": Decimal("7.0"),
            "sleep_quality": "Good",
            "bmi": Decimal("22.5"),
            "heart_rate": 68,
            "stress_level": "Low",
            "physical_activity_hours": Decimal("8.0"),
            "health_issues": "None",
            "occupation": "Office",
            "smoking": False,
            "alcohol_consumption": False,
        },
        {
            "id": 2,
            "age": 45,
            "gender": "Female",
            "country": "Spain",
            "coffee_intake": Decimal("3.0"),
            "caffeine_mg": Decimal("270.0"),
            "sleep_hours": Decimal("6.5"),
            "sleep_quality": "Good",
            "bmi": Decimal("24.0"),
            "heart_rate": 72,
            "stress_level": "Medium",
            "physical_activity_hours": Decimal("6.0"),
            "health_issues": "Mild",
            "occupation": "Healthcare",
            "smoking": False,
            "alcohol_consumption": True,
        },
        {
            "id": 3,
            "age": 28,
            "gender": "Other",
            "country": "Canada",
            "coffee_intake": Decimal("1.5"),
            "caffeine_mg": Decimal("120.0"),
            "sleep_hours": Decimal("8.0"),
            "sleep_quality": "Excellent",
            "bmi": Decimal("21.0"),
            "heart_rate": 62,
            "stress_level": "Low",
            "physical_activity_hours": Decimal("10.0"),
            "health_issues": "None",
            "occupation": "Student",
            "smoking": False,
            "alcohol_consumption": False,
        },
    ]

    mock_conn.execute.return_value.fetchall.return_value = mock_rows
    app.dependency_overrides[get_conn] = lambda: mock_conn

    try:
        response = standalone_client.get("/records?limit=2")
        assert response.status_code == 200
        data = response.json()

        assert len(data["items"]) == 2
        assert data["items"][0]["id"] == 1
        assert data["items"][1]["id"] == 2
        assert data["has_more"] is True
        assert data["next_cursor"] == 2
        assert data["limit"] == 2
    finally:
        app.dependency_overrides.clear()


def test_records_summary_mocked(standalone_client: TestClient) -> None:
    mock_conn = MagicMock()
    mock_summary_rows = [
        {
            "group_key": "Germany",
            "record_count": 497,
            "avg_sleep_hours": Decimal("6.9"),
            "avg_caffeine_mg": Decimal("285.4"),
            "avg_bmi": Decimal("23.8"),
            "avg_heart_rate": Decimal("71.2"),
        }
    ]
    mock_conn.execute.return_value.fetchall.return_value = mock_summary_rows
    app.dependency_overrides[get_conn] = lambda: mock_conn

    try:
        response = standalone_client.get("/records/summary?group_by=country")
        assert response.status_code == 200
        data = response.json()
        assert data["grouped_by"] == "country"
        assert data["total_records"] == 497
        assert len(data["groups"]) == 1
        assert data["groups"][0]["group_key"] == "Germany"
    finally:
        app.dependency_overrides.clear()


def test_records_validation_invalid_categorical(standalone_client: TestClient) -> None:
    response = standalone_client.get("/records?gender=Alien")
    assert response.status_code == 422
    assert "Invalid gender filter" in response.text

    response = standalone_client.get("/records?sleep_quality=Terrible")
    assert response.status_code == 422
    assert "Invalid sleep_quality filter" in response.text

    response = standalone_client.get("/records?stress_level=Extreme")
    assert response.status_code == 422
    assert "Invalid stress_level filter" in response.text


def test_records_real_db_filtering_and_pagination(client: TestClient) -> None:
    """Live integration test against Postgres validating keyset pagination and filtering."""
    # 1. Fetch first page of German records with limit 3
    response_p1 = client.get("/records?country=Germany&limit=3")
    assert response_p1.status_code == 200
    data_p1 = response_p1.json()

    assert len(data_p1["items"]) == 3
    assert all(item["country"] == "Germany" for item in data_p1["items"])
    assert data_p1["has_more"] is True
    assert data_p1["next_cursor"] is not None

    p1_ids = [item["id"] for item in data_p1["items"]]
    assert p1_ids == sorted(p1_ids)

    # 2. Fetch second page using next_cursor
    cursor = data_p1["next_cursor"]
    response_p2 = client.get(f"/records?country=Germany&limit=3&cursor={cursor}")
    assert response_p2.status_code == 200
    data_p2 = response_p2.json()

    assert len(data_p2["items"]) == 3
    p2_ids = [item["id"] for item in data_p2["items"]]
    assert all(item["country"] == "Germany" for item in data_p2["items"])
    # Keyset invariant: all IDs in page 2 are strictly greater than cursor
    assert all(record_id > cursor for record_id in p2_ids)


def test_records_summary_real_db(client: TestClient) -> None:
    """Live integration test against Postgres verifying aggregation grouping."""
    response = client.get("/records/summary?group_by=gender")
    assert response.status_code == 200
    data = response.json()

    assert data["grouped_by"] == "gender"
    assert data["total_records"] > 0
    group_keys = {g["group_key"] for g in data["groups"]}
    assert "Male" in group_keys
    assert "Female" in group_keys
