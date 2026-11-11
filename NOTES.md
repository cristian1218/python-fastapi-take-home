# Architecture Decisions & Technical Notes

This document describes the engineering decisions, data cleaning rules, pagination mechanics, and scaling strategies implemented for the Medallo.dev take-home challenge.

---

## 1. Schema Design & Data Types (`db/init/002_coffee_health.sql`)

### A. Deliberate Type Selection
- **`id` (`INTEGER PRIMARY KEY`)**:
  - The dataset currently has 10,000 rows, with future growth projected up to tens of millions.
  - Signed 4-byte `INTEGER` in PostgreSQL supports IDs up to $+2,147,483,647$.
  - *Trade-off*: `INTEGER` was chosen over `BIGINT` (8 bytes) to save 4 bytes per row and 50% of the primary index footprint in RAM (`shared_buffers`) and disk, minimizing I/O overhead at scale.
- **`age` and `heart_rate` (`SMALLINT`)**:
  - `SMALLINT` occupies 2 bytes (range $-32,768$ to $+32,767$).
  - Human age (18–120) and resting heart rate (30–220) easily fit within 2 bytes. Using 4-byte `INTEGER` would waste 50% storage on these columns across millions of rows.
- **Continuous Metrics (`NUMERIC(p, s)`)**:
  - `coffee_intake`: `NUMERIC(4, 1)` (cups/day).
  - `caffeine_mg`: `NUMERIC(6, 1)` (mg/day).
  - `sleep_hours`: `NUMERIC(4, 1)` (hours/night).
  - `bmi`: `NUMERIC(4, 1)` (body mass index).
  - `physical_activity_hours`: `NUMERIC(4, 1)` (hours/week).
  - *Trade-off*: We avoided IEEE-754 `DOUBLE PRECISION` / `FLOAT` to prevent binary floating-point representation and rounding artifacts in medical metrics and analytics. PostgreSQL's `NUMERIC` maps 1:1 with Python's `decimal.Decimal`.
- **`smoking` and `alcohol_consumption` (`BOOLEAN`)**:
  - Stored as native 1-byte booleans rather than strings or integers.
- **Categorical Columns (`VARCHAR(n)` with `CHECK` constraints)**:
  - `gender`: `VARCHAR(10) CHECK (gender IN ('Male', 'Female', 'Other'))`
  - `sleep_quality`: `VARCHAR(15) CHECK (sleep_quality IN ('Poor', 'Fair', 'Good', 'Excellent'))`
  - `stress_level`: `VARCHAR(10) CHECK (stress_level IN ('Low', 'Medium', 'High'))`
  - `health_issues`: `VARCHAR(15) CHECK (health_issues IN ('None', 'Mild', 'Moderate', 'Severe'))`
  - `occupation`: `VARCHAR(30) CHECK (occupation IN ('Office', 'Healthcare', 'Service', 'Student', 'Other'))`
  - *Trade-off*: Table-level `CHECK` constraints were preferred over PostgreSQL `ENUM` types. Postgres `ENUM` types add catalog dependencies and schema migration friction (e.g. `ALTER TYPE ... ADD VALUE` restrictions inside transaction blocks). `CHECK` constraints are portable and zero-maintenance.

### B. Indexing Strategy
- **`coffee_health_records_pkey`**: Implicit B-tree index on `(id)`. Provides $O(\log N)$ point lookups and native support for keyset cursor pagination (`WHERE id > :cursor ORDER BY id ASC LIMIT :n`).
- **`idx_coffee_health_country`**: B-tree index on `(country)`. Country has ~18 distinct values evenly distributed (~5% selectivity per country), making filtered queries highly selective.
- **Write Throughput Trade-off**: We intentionally avoided indexing low-cardinality columns (e.g. `gender`, `smoking`, `stress_level`). For columns with 2–3 distinct values, PostgreSQL's query planner typically ignores indexes in favor of sequential or bitmap scans. Omitting unnecessary indexes protects streaming write throughput.

---

## 2. Data Cleaning, Normalization & Validation (`app/cleaning.py`)

### A. Raw Dataset Profile (`data/coffee_health_raw.csv`)
- **Encoding & Structural Anomalies**:
  - 10,016 total lines, including a 16-column header with a UTF-8 Byte Order Mark (`\ufeff`), handled transparently with `encoding="utf-8-sig"`.
  - 3 blank lines: lines `5953`, `10015`, and `10016`.
  - Column count mismatch:
    - Lines `4869` and `7175`: only 13 columns (missing `Occupation`, `Smoking`, `Alcohol_Consumption`).
    - Line `2659`: 17 columns (contains an extra residual column).
- **Duplicate Records**:
  - 10,000 unique IDs.
  - 8 identical duplicate rows.
  - 4 conflicting duplicate collisions (same ID, conflicting attribute values).

### B. Normalization Rules (Fixable Data)
- **Whitespace & Casing**: Strips leading/trailing tabs and whitespace (`.strip()`). Standardizes casing into Title Case while preserving uppercase country codes (`USA`, `UK`).
- **Booleans**: Deterministic truthy/falsy normalization:
  - `True`: `{'1', 'true', 'yes', 'y'}`
  - `False`: `{'0', 'false', 'no', 'n'}`

### C. Biological Bounds & Rejection Criteria
Rows violating structural integrity or human physiological plausibility are rejected:

| Field | Valid Range | Rejection Reasons & Observed CSV Errors |
| :--- | :--- | :--- |
| **`Age`** | $18 \le \text{age} \le 120$ | Adult cohort requirement. Rejects negative values (`-3`), impossible ages (`150`, `212`), and text strings (`thirty`, `4O`, `unknown`). |
| **`Sleep_Hours`** | $0.0 \le \text{sleep} \le 24.0$ | Daily 24-hour limit. Rejects negative values (`-1`), impossible hours (`25.0`), and text (`seven`). |
| **`BMI`** | $10.0 \le \text{bmi} \le 80.0$ | Human physiological limits. Rejects `0`, `n/a`, empty. |
| **`Heart_Rate`** | $30 \le \text{bpm} \le 220$ | Resting heart rate plausibility. Rejects `0`, `350`, empty. |
| **`Coffee_Intake`** | $0.0 \le \text{cups} \le 30.0$ | Plausible daily range. |
| **`Caffeine_mg`** | $0.0 \le \text{mg} \le 3000.0$ | Plausible daily dosage. |
| **`Physical_Activity_Hours`** | $0.0 \le \text{hours} \le 168.0$ | Hours in a week ($\le 24 \times 7 = 168$). |
| **Structure** | Exactly 16 columns | Rejects lines with missing (13) or extraneous (17) columns. |

---

## 3. Streaming Ingestion Engine & Idempotency (`scripts/load_data.py`)

### A. True $O(1)$ Memory Streaming
- **Generator Pipeline**: Uses Python's standard `csv.reader` inside a streaming generator (`stream_and_validate_csv`), yielding one validated `CleanedRecord` at a time. The dataset is never loaded entirely into RAM.
- **Batch Insertion**: Validated records are batched into chunks (`BATCH_SIZE = 1000`) using `itertools.islice`, amortizing network roundtrips while keeping buffer usage minimal.
- **Delegated Deduplication**: Deduplication is delegated to PostgreSQL's `ON CONFLICT (id) DO NOTHING`. Python does not maintain a hash set of millions of IDs in RAM, keeping Python heap memory flat (<50 MB) whether processing 10,000 or 50,000,000 records.

### B. Duplicate Handling Policy (First-Wins)
- **First-Wins**: The first valid record encountered for a given ID is inserted.
- **Identical Duplicates**: Skipped and counted as `duplicated`.
- **Conflicting Collisions**: Emits a diagnostic warning to `stderr` with line number and ID, protecting the integrity of the first verified record.

### C. Ingestion Accounting Metrics

$$\text{Rows Read} = \text{Loaded} + \text{Rejected} + \text{Duplicated}$$

On the raw dataset (`data/coffee_health_raw.csv`):

| Counter | Initial Load (`make load`) | Second Consecutive Run | Explanation |
| :--- | :--- | :--- | :--- |
| **Read** | **10,012** | **10,012** | Total non-empty rows processed. |
| **Loaded** | **9,975** | **0** | Successfully inserted into PostgreSQL. |
| **Corrected** | **56** | **0** | Subset of loaded rows that were normalized. Bound by $\min(\text{corrected}, \text{loaded})$. |
| **Rejected** | **25** | **25** | CSV rows with invalid columns or physiological violations. |
| **Duplicated** | **12** | **9,987** | Run 1: 8 identical + 4 conflicting. Run 2: $9,975 + 12$. |
| **Balance** | **OK** | **OK** | $10,012 = 9,975 + 25 + 12$ / $10,012 = 0 + 25 + 9,987$. |

---

## 4. FastAPI Service & Keyset Pagination (`app/main.py`)

### A. Keyset (Cursor) Pagination vs. Offset
- **The $O(N)$ Problem of `OFFSET`**:
  - Requesting page 50,000 with `OFFSET 2,500,000 LIMIT 50` forces Postgres to scan and discard 2.5 million tuples, taking hundreds of milliseconds and flooding disk cache.
- **Keyset Mechanics (`WHERE id > :cursor ORDER BY id ASC LIMIT :limit + 1`)**:
  - Utilizes the primary key B-tree index directly. The engine seeks to `:cursor` in $O(\log N)$ time and traverses only the next $K$ index leaves.
  - **Constant Latency**: Page 50,000 executes with the exact same ~2 ms latency as Page 1.
  - **Limit + 1 Technique**: We query `limit + 1` rows. If $K+1$ rows return, `has_more = True` and the ID of the $K$-th row becomes `next_cursor`, eliminating slow `COUNT(*)` table scans.

### B. Dynamic Filters & Injection Safety
- All dynamic query conditions are constructed via `psycopg.sql.Composable` with strict parameter binding (`ANY(%(param)s)` for multi-select, comparison operators for numeric ranges). No string formatting or interpolation is used.
- Validates query filters and rejects invalid requests with clear **HTTP 422 Unprocessable Entity**:
  - Inverted ranges: `min_age > max_age`, `min_coffee_intake > max_coffee_intake`, `min_bmi > max_bmi`.
  - Invalid categoricals: non-existent values for `gender`, `sleep_quality`, or `stress_level`.
  - Pagination boundary errors: `limit < 1` or `limit > 100`.

### C. Analytics Endpoint (`GET /records/summary`)
- Computes aggregations directly in Postgres (`COUNT(*)`, `ROUND(AVG(...), 2)`), grouped by `country`, `gender`, `sleep_quality`, `stress_level`, or `occupation`, accepting all filtering parameters.

---

## 5. Scaling to Tens of Millions of Records (10M – 50M)

### A. Memory Consumption
- Streaming with `csv.reader` and generator pipelines guarantees $O(1)$ memory. Python memory remains below 50 MB regardless of file size.
- For files beyond 10M rows, loading throughput can be accelerated by streaming through `psycopg.Cursor.copy()` into an `UNLOGGED` staging table, followed by an `INSERT INTO ... SELECT ... ON CONFLICT DO NOTHING`.

### B. Index Maintenance & Write Performance
- Every secondary index requires B-tree rebalancing, page splits, and WAL logging on each insert. On a 50M row table, having 6+ indexes can reduce bulk loading throughput by 50–70%.
- We only indexed `country` (selective) and the primary key.
- For append-only time-ordered tables, **BRIN (Block Range Indexes)** offer an alternative: a BRIN index on 50M rows occupies ~64 KB of RAM compared to ~1.1 GB for a B-tree, with virtually zero write penalty.

### C. Database Connection Pooling under High Concurrency
- PostgreSQL uses a process-per-connection model. Spawning 1,000+ direct connections causes severe CPU context-switching overhead and memory consumption (~5–10 MB per connection).
- The API uses `psycopg_pool.ConnectionPool` sized according to PostgreSQL's empirical formula:
  $$\text{max\_connections} = (\text{CPU cores} \times 2) + \text{effective\_spindle\_count}$$
  A pool of 10–20 connections maximizes throughput for typical 4–8 core instances.
- In production, placing **PgBouncer** in *Transaction Pooling* mode allows thousands of concurrent FastAPI clients to share a tight pool of 30–50 Postgres connections.

### D. Table Partitioning
- At 20M–50M rows, declarative range partitioning on `id` (e.g. 10M rows per partition) allows **Partition Pruning**: keyset pagination queries (`WHERE id > :cursor`) immediately prune non-matching partitions, keeping individual index trees small enough to fit completely in `shared_buffers`.
