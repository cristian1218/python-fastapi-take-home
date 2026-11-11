"""Load the coffee health CSV into Postgres with streaming, validation, and idempotency.

Usage:
    uv run python scripts/load_data.py data/coffee_health_raw.csv
"""

import argparse
import csv
import sys
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from itertools import islice
from pathlib import Path
from typing import Any, TextIO

# Ensure project root is in sys.path when script is run directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402
from psycopg import Connection  # noqa: E402

from app.cleaning import CleanedRecord, RowRejectedError, clean_and_validate_row  # noqa: E402
from app.config import settings  # noqa: E402

BATCH_SIZE = 1000

INSERT_SQL = """
INSERT INTO public.coffee_health_records (
    id, age, gender, country, coffee_intake, caffeine_mg,
    sleep_hours, sleep_quality, bmi, heart_rate, stress_level,
    physical_activity_hours, health_issues, occupation, smoking, alcohol_consumption
) VALUES (
    %(id)s, %(age)s, %(gender)s, %(country)s, %(coffee_intake)s, %(caffeine_mg)s,
    %(sleep_hours)s, %(sleep_quality)s, %(bmi)s, %(heart_rate)s, %(stress_level)s,
    %(physical_activity_hours)s, %(health_issues)s, %(occupation)s,
    %(smoking)s, %(alcohol_consumption)s
)
ON CONFLICT (id) DO NOTHING
RETURNING id;
"""


@dataclass
class IngestionStats:
    """Accounting counters for the data ingestion process."""

    read: int = 0
    loaded: int = 0
    corrected: int = 0
    rejected: int = 0
    duplicated: int = 0

    @property
    def is_balanced(self) -> bool:
        """Enforces strict accounting equation: read == loaded + rejected + duplicated."""
        return self.read == (self.loaded + self.rejected + self.duplicated)


def stream_and_validate_csv(
    csv_file: TextIO,
    on_reject: Callable[[int, str], None] | None = None,
    on_duplicate: Callable[[int, int, str], None] | None = None,
    track_duplicates_in_memory: bool = False,
) -> tuple[Iterator[CleanedRecord], IngestionStats]:
    """Stream, clean, and validate CSV rows with O(1) memory complexity.

    By default (track_duplicates_in_memory=False), deduplication is delegated to PostgreSQL's
    ON CONFLICT DO NOTHING, ensuring strict O(1) RAM usage regardless of dataset size (10M-50M).
    If track_duplicates_in_memory=True (e.g. offline dry-run), seen IDs are tracked in RAM.
    """
    stats = IngestionStats()
    reader = csv.reader(csv_file)

    try:
        header = next(reader)
    except StopIteration:
        return iter(()), stats

    col_names = [c.strip() for c in header]
    expected_cols = len(col_names)
    seen_ids: set[int] = set() if track_duplicates_in_memory else set()

    def record_generator() -> Iterator[CleanedRecord]:
        for line_num, row in enumerate(reader, start=2):
            # Skip empty lines
            if not row or all(col.strip() == "" for col in row):
                continue

            stats.read += 1

            if len(row) != expected_cols:
                reason = f"Expected {expected_cols} columns, got {len(row)}"
                stats.rejected += 1
                if on_reject:
                    on_reject(line_num, reason)
                continue

            raw_dict: dict[str, str | None] = dict(zip(col_names, row, strict=True))
            try:
                record, was_corrected = clean_and_validate_row(raw_dict, line_num)
            except RowRejectedError as err:
                stats.rejected += 1
                if on_reject:
                    on_reject(line_num, err.reason)
                continue

            if track_duplicates_in_memory:
                if record.id in seen_ids:
                    stats.duplicated += 1
                    if on_duplicate:
                        on_duplicate(line_num, record.id, "Duplicate ID")
                    continue
                seen_ids.add(record.id)

            if was_corrected:
                stats.corrected += 1
            stats.loaded += 1
            yield record

    return record_generator(), stats


def insert_records_batch(conn: Connection[Any], records_chunk: list[CleanedRecord]) -> int:
    """Insert a chunk of cleaned records into Postgres using ON CONFLICT DO NOTHING.

    Returns the number of rows actually inserted.
    """
    if not records_chunk:
        return 0

    params = [asdict(record) for record in records_chunk]
    with conn.cursor() as cur:
        cur.executemany(INSERT_SQL, params, returning=True)
        inserted_count = 0
        while True:
            inserted_count += len(cur.fetchall())
            if not cur.nextset():
                break
        return inserted_count


def chunked(iterable: Iterator[CleanedRecord], size: int) -> Iterator[list[CleanedRecord]]:
    """Yield successive chunks of specified size from an iterator."""
    while True:
        chunk = list(islice(iterable, size))
        if not chunk:
            break
        yield chunk


def print_summary(stats: IngestionStats) -> None:
    """Print the final structured summary table."""
    print("\n" + "=" * 52)
    print("            RESUMEN DE INGESTIÓN DE DATOS           ")
    print("=" * 52)
    print(f"  Filas leídas:       {stats.read:>8,}")
    print(f"  Filas cargadas:     {stats.loaded:>8,}")
    print(f"  Filas corregidas:   {stats.corrected:>8,}  (subconjunto de cargadas)")
    print(f"  Filas rechazadas:   {stats.rejected:>8,}")
    print(f"  Filas duplicadas:   {stats.duplicated:>8,}")
    print("-" * 52)
    status = "OK (Ecuación contable válida)" if stats.is_balanced else "ERROR (Descuadre contable)"
    print(f"  Estado de balance:  {status}")
    print("=" * 52 + "\n")


def load_csv(
    csv_path: Path,
    database_url: str | None = None,
    batch_size: int = BATCH_SIZE,
    dry_run: bool = False,
) -> IngestionStats:
    """Load and ingest a CSV file into PostgreSQL in streaming chunks.

    If dry_run is True, streams and validates all rows without connecting to the database.
    """
    db_url = database_url or settings.database_url

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    def on_reject(line: int, reason: str) -> None:
        print(f"[RECHAZADA]  Línea {line:>5}: {reason}", file=sys.stderr)

    def on_duplicate(line: int, record_id: int, reason: str) -> None:
        print(f"[DUPLICADA]  Línea {line:>5}: ID {record_id} ({reason})", file=sys.stderr)

    with open(csv_path, encoding="utf-8-sig") as f:
        records_iter, stats = stream_and_validate_csv(
            f,
            on_reject=on_reject,
            on_duplicate=on_duplicate,
            track_duplicates_in_memory=dry_run,
        )

        if dry_run:
            # Consume iterator to process all rows without DB connection
            for _ in records_iter:
                pass
            print_summary(stats)
            return stats

        with psycopg.connect(db_url) as conn, conn.transaction():
            total_inserted = 0
            for chunk in chunked(records_iter, batch_size):
                inserted = insert_records_batch(conn, chunk)
                total_inserted += inserted

            stats.duplicated = stats.loaded - total_inserted
            stats.loaded = total_inserted
            if stats.loaded == 0:
                stats.corrected = 0
            else:
                stats.corrected = min(stats.corrected, stats.loaded)

    print_summary(stats)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv_path", type=Path, help="Path to coffee_health_raw.csv")
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="Optional database connection URL (defaults to DATABASE_URL)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report metrics on the CSV without connecting to Postgres",
    )
    args = parser.parse_args()

    try:
        load_csv(args.csv_path, database_url=args.db_url, dry_run=args.dry_run)
    except psycopg.OperationalError as err:
        msg = (
            f"\n[ERROR DE CONEXIÓN A POSTGRES]: {err}\n"
            "Verifica que la base de datos esté corriendo."
        )
        sys.exit(msg)
    except Exception as err:
        sys.exit(f"\n[ERROR]: {err}")


if __name__ == "__main__":
    main()
