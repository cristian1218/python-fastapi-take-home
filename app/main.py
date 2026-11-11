from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from psycopg import Connection, sql
from psycopg.rows import DictRow
from pydantic import BaseModel

from app.cleaning import VALID_GENDERS, VALID_SLEEP_QUALITY, VALID_STRESS_LEVEL
from app.db import create_pool, get_conn


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.pool = create_pool()
    yield
    app.state.pool.close()


app = FastAPI(
    title="Coffee & Health API",
    description="High-performance FastAPI service with keyset pagination and dynamic filters.",
    version="1.0.0",
    lifespan=lifespan,
)

Conn = Annotated[Connection[DictRow], Depends(get_conn)]


# ---------------------------------------------------------------------------
# Pydantic Response Models
# ---------------------------------------------------------------------------


class Health(BaseModel):
    status: str
    database: str


class Note(BaseModel):
    id: int
    title: str


class CoffeeRecordResponse(BaseModel):
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


class PaginatedRecordsResponse(BaseModel):
    items: list[CoffeeRecordResponse]
    next_cursor: int | None
    has_more: bool
    limit: int


class GroupSummary(BaseModel):
    group_key: str
    record_count: int
    avg_sleep_hours: Decimal
    avg_caffeine_mg: Decimal
    avg_bmi: Decimal
    avg_heart_rate: Decimal


class RecordsSummaryResponse(BaseModel):
    grouped_by: str
    total_records: int
    groups: list[GroupSummary]


# ---------------------------------------------------------------------------
# Existing Health & Notes Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["System"])
def health(conn: Conn) -> Health:
    conn.execute("select 1")
    return Health(status="ok", database="ok")


@app.get("/notes", tags=["System"])
def list_notes(conn: Conn) -> list[Note]:
    """Example endpoint backed by the sample `notes` table."""
    rows = conn.execute("select id, title from notes order by id").fetchall()
    return [Note.model_validate(row) for row in rows]


# ---------------------------------------------------------------------------
# Coffee Health Endpoints (Part 2)
# ---------------------------------------------------------------------------


def _validate_filter_parameters(
    gender: list[str] | None,
    sleep_quality: list[str] | None,
    stress_level: list[str] | None,
    min_age: int | None,
    max_age: int | None,
    min_coffee_intake: Decimal | None,
    max_coffee_intake: Decimal | None,
    min_bmi: Decimal | None,
    max_bmi: Decimal | None,
) -> None:
    """Validate range boundaries and categorical filter constraints, returning 422 on violations."""
    if min_age is not None and max_age is not None and min_age > max_age:
        raise HTTPException(
            status_code=422,
            detail="min_age cannot be greater than max_age",
        )
    if (
        min_coffee_intake is not None
        and max_coffee_intake is not None
        and min_coffee_intake > max_coffee_intake
    ):
        raise HTTPException(
            status_code=422,
            detail="min_coffee_intake cannot be greater than max_coffee_intake",
        )
    if min_bmi is not None and max_bmi is not None and min_bmi > max_bmi:
        raise HTTPException(
            status_code=422,
            detail="min_bmi cannot be greater than max_bmi",
        )

    if gender:
        invalid_genders = [g for g in gender if g not in VALID_GENDERS]
        if invalid_genders:
            allowed = sorted(VALID_GENDERS)
            raise HTTPException(
                status_code=422,
                detail=f"Invalid gender filter: {invalid_genders}. Allowed: {allowed}",
            )
    if sleep_quality:
        invalid_sq = [sq for sq in sleep_quality if sq not in VALID_SLEEP_QUALITY]
        if invalid_sq:
            allowed = sorted(VALID_SLEEP_QUALITY)
            raise HTTPException(
                status_code=422,
                detail=f"Invalid sleep_quality filter: {invalid_sq}. Allowed: {allowed}",
            )
    if stress_level:
        invalid_sl = [sl for sl in stress_level if sl not in VALID_STRESS_LEVEL]
        if invalid_sl:
            allowed = sorted(VALID_STRESS_LEVEL)
            raise HTTPException(
                status_code=422,
                detail=f"Invalid stress_level filter: {invalid_sl}. Allowed: {allowed}",
            )


def _build_filter_conditions(
    country: list[str] | None,
    gender: list[str] | None,
    sleep_quality: list[str] | None,
    stress_level: list[str] | None,
    min_age: int | None,
    max_age: int | None,
    min_coffee_intake: Decimal | None,
    max_coffee_intake: Decimal | None,
    min_bmi: Decimal | None,
    max_bmi: Decimal | None,
) -> tuple[list[sql.Composable], dict[str, Any]]:
    """Build parameterized WHERE conditions for filter parameters."""
    conditions: list[sql.Composable] = []
    params: dict[str, Any] = {}

    if country:
        conditions.append(sql.SQL("country = ANY(%(countries)s)"))
        params["countries"] = country
    if gender:
        conditions.append(sql.SQL("gender = ANY(%(genders)s)"))
        params["genders"] = gender
    if sleep_quality:
        conditions.append(sql.SQL("sleep_quality = ANY(%(sleep_qualities)s)"))
        params["sleep_qualities"] = sleep_quality
    if stress_level:
        conditions.append(sql.SQL("stress_level = ANY(%(stress_levels)s)"))
        params["stress_levels"] = stress_level
    if min_age is not None:
        conditions.append(sql.SQL("age >= %(min_age)s"))
        params["min_age"] = min_age
    if max_age is not None:
        conditions.append(sql.SQL("age <= %(max_age)s"))
        params["max_age"] = max_age
    if min_coffee_intake is not None:
        conditions.append(sql.SQL("coffee_intake >= %(min_coffee_intake)s"))
        params["min_coffee_intake"] = min_coffee_intake
    if max_coffee_intake is not None:
        conditions.append(sql.SQL("coffee_intake <= %(max_coffee_intake)s"))
        params["max_coffee_intake"] = max_coffee_intake
    if min_bmi is not None:
        conditions.append(sql.SQL("bmi >= %(min_bmi)s"))
        params["min_bmi"] = min_bmi
    if max_bmi is not None:
        conditions.append(sql.SQL("bmi <= %(max_bmi)s"))
        params["max_bmi"] = max_bmi

    return conditions, params


@app.get(
    "/records",
    response_model=PaginatedRecordsResponse,
    tags=["Coffee Health"],
    summary="List and filter coffee health records using Keyset pagination",
)
def list_records(
    conn: Conn,
    cursor: Annotated[
        int | None,
        Query(ge=1, description="Keyset pagination cursor (ID of the last seen record)"),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Maximum number of records to return (1-100)"),
    ] = 50,
    country: Annotated[
        list[str] | None,
        Query(description="Filter by country (single or multiple)"),
    ] = None,
    gender: Annotated[
        list[str] | None,
        Query(description="Filter by gender (Male, Female, Other)"),
    ] = None,
    sleep_quality: Annotated[
        list[str] | None,
        Query(description="Filter by sleep quality (Poor, Fair, Good, Excellent)"),
    ] = None,
    stress_level: Annotated[
        list[str] | None,
        Query(description="Filter by stress level (Low, Medium, High)"),
    ] = None,
    min_age: Annotated[int | None, Query(ge=18, le=120)] = None,
    max_age: Annotated[int | None, Query(ge=18, le=120)] = None,
    min_coffee_intake: Annotated[Decimal | None, Query(ge=0)] = None,
    max_coffee_intake: Annotated[Decimal | None, Query(ge=0)] = None,
    min_bmi: Annotated[Decimal | None, Query(ge=10)] = None,
    max_bmi: Annotated[Decimal | None, Query(ge=10)] = None,
) -> PaginatedRecordsResponse:
    _validate_filter_parameters(
        gender=gender,
        sleep_quality=sleep_quality,
        stress_level=stress_level,
        min_age=min_age,
        max_age=max_age,
        min_coffee_intake=min_coffee_intake,
        max_coffee_intake=max_coffee_intake,
        min_bmi=min_bmi,
        max_bmi=max_bmi,
    )

    conditions, params = _build_filter_conditions(
        country=country,
        gender=gender,
        sleep_quality=sleep_quality,
        stress_level=stress_level,
        min_age=min_age,
        max_age=max_age,
        min_coffee_intake=min_coffee_intake,
        max_coffee_intake=max_coffee_intake,
        min_bmi=min_bmi,
        max_bmi=max_bmi,
    )

    if cursor is not None:
        conditions.append(sql.SQL("id > %(cursor)s"))
        params["cursor"] = cursor

    params["limit_plus_one"] = limit + 1

    where_sql = sql.SQL("WHERE ") + sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("")

    query = sql.SQL("""
        SELECT id, age, gender, country, coffee_intake, caffeine_mg,
               sleep_hours, sleep_quality, bmi, heart_rate, stress_level,
               physical_activity_hours, health_issues, occupation, smoking, alcohol_consumption
        FROM public.coffee_health_records
        {where}
        ORDER BY id ASC
        LIMIT %(limit_plus_one)s
    """).format(where=where_sql)

    rows = conn.execute(query, params).fetchall()

    has_more = len(rows) > limit
    page_items = rows[:limit]
    next_cursor = page_items[-1]["id"] if has_more and page_items else None

    return PaginatedRecordsResponse(
        items=[CoffeeRecordResponse.model_validate(row) for row in page_items],
        next_cursor=next_cursor,
        has_more=has_more,
        limit=limit,
    )


@app.get(
    "/records/summary",
    response_model=RecordsSummaryResponse,
    tags=["Coffee Health"],
    summary="Aggregated metrics summary grouped by categorical dimension",
)
def get_summary(
    conn: Conn,
    group_by: Annotated[
        Literal["country", "gender", "sleep_quality", "stress_level", "occupation"],
        Query(description="Dimension to group aggregated metrics by"),
    ] = "country",
    country: Annotated[list[str] | None, Query()] = None,
    gender: Annotated[list[str] | None, Query()] = None,
    sleep_quality: Annotated[list[str] | None, Query()] = None,
    stress_level: Annotated[list[str] | None, Query()] = None,
    min_age: Annotated[int | None, Query(ge=18, le=120)] = None,
    max_age: Annotated[int | None, Query(ge=18, le=120)] = None,
    min_coffee_intake: Annotated[Decimal | None, Query(ge=0)] = None,
    max_coffee_intake: Annotated[Decimal | None, Query(ge=0)] = None,
    min_bmi: Annotated[Decimal | None, Query(ge=10)] = None,
    max_bmi: Annotated[Decimal | None, Query(ge=10)] = None,
) -> RecordsSummaryResponse:
    """Returns aggregated summary metrics for health and coffee intake."""
    _validate_filter_parameters(
        gender=gender,
        sleep_quality=sleep_quality,
        stress_level=stress_level,
        min_age=min_age,
        max_age=max_age,
        min_coffee_intake=min_coffee_intake,
        max_coffee_intake=max_coffee_intake,
        min_bmi=min_bmi,
        max_bmi=max_bmi,
    )

    conditions, params = _build_filter_conditions(
        country=country,
        gender=gender,
        sleep_quality=sleep_quality,
        stress_level=stress_level,
        min_age=min_age,
        max_age=max_age,
        min_coffee_intake=min_coffee_intake,
        max_coffee_intake=max_coffee_intake,
        min_bmi=min_bmi,
        max_bmi=max_bmi,
    )

    where_sql = sql.SQL("WHERE ") + sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("")

    query = sql.SQL("""
        SELECT {group_field} AS group_key,
               COUNT(*)::INTEGER AS record_count,
               ROUND(AVG(sleep_hours), 2) AS avg_sleep_hours,
               ROUND(AVG(caffeine_mg), 2) AS avg_caffeine_mg,
               ROUND(AVG(bmi), 2) AS avg_bmi,
               ROUND(AVG(heart_rate), 2) AS avg_heart_rate
        FROM public.coffee_health_records
        {where}
        GROUP BY {group_field}
        ORDER BY record_count DESC
    """).format(
        group_field=sql.Identifier(group_by),
        where=where_sql,
    )

    rows = conn.execute(query, params).fetchall()
    total_records = sum(row["record_count"] for row in rows)

    return RecordsSummaryResponse(
        grouped_by=group_by,
        total_records=total_records,
        groups=[GroupSummary.model_validate(row) for row in rows],
    )
