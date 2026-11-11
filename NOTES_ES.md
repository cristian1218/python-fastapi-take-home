# Notas Técnicas y Decisiones de Arquitectura (Take-Home Medallo.dev)

Este documento registra las decisiones de ingeniería, justificaciones técnicas y tradeoffs adoptados en la solución del ejercicio técnico para Medallo.dev.

---

## 1. Diseño del Esquema y Tipos de Datos (`db/init/002_coffee_health.sql`)

### A. Tipos de Datos Deliberados
- **`id` (`INTEGER PRIMARY KEY`)**: 
  - El dataset actual cuenta con 10,000 registros y la especificación proyecta crecimiento a decenas de millones. 
  - `INTEGER` (4 bytes con signo en Postgres) soporta hasta $+2,147,483,647$ registros. 
  - *Tradeoff*: Se eligió `INTEGER` sobre `BIGINT` (8 bytes) porque ahorra 4 bytes por fila y un 50% en el tamaño del índice primario en disco y memoria (buffer pool), reduciendo la presión de I/O a gran escala.
- **`age` y `heart_rate` (`SMALLINT`)**:
  - `SMALLINT` ocupa 2 bytes (rango $-32,768$ a $+32,767$).
  - La edad humana y la frecuencia cardíaca caben holgadamente en este rango. Usar `INTEGER` (4 bytes) desperdiciaría un 50% de almacenamiento por columna.
- **Medidas continuas (`NUMERIC(p,s)`)**:
  - `coffee_intake`: `NUMERIC(4,1)` (tazas/día).
  - `caffeine_mg`: `NUMERIC(6,1)` (miligramos/día).
  - `sleep_hours`: `NUMERIC(4,1)` (horas/noche).
  - `bmi`: `NUMERIC(4,1)` (índice masa corporal).
  - `physical_activity_hours`: `NUMERIC(4,1)` (horas/semana).
  - *Justificación*: Se evitó `DOUBLE PRECISION` / `FLOAT` para prevenir errores de redondeo de coma flotante binaria en métricas de salud y reportes analíticos. `NUMERIC` en Postgres almacena representaciones decimales exactas que mapean de forma 1:1 con `decimal.Decimal` en Python.
- **`smoking` y `alcohol_consumption` (`BOOLEAN`)**:
  - Banderas binarias verdaderas (1 byte) en lugar de representar cadenas o enteros.
- **Campos Categóricos (`VARCHAR(n)` con restricciones `CHECK`)**:
  - `gender`: `VARCHAR(10) CHECK (gender IN ('Male', 'Female', 'Other'))`
  - `sleep_quality`: `VARCHAR(15) CHECK (sleep_quality IN ('Poor', 'Fair', 'Good', 'Excellent'))`
  - `stress_level`: `VARCHAR(10) CHECK (stress_level IN ('Low', 'Medium', 'High'))`
  - `health_issues`: `VARCHAR(15) CHECK (health_issues IN ('None', 'Mild', 'Moderate', 'Severe'))`
  - `occupation`: `VARCHAR(30) CHECK (occupation IN ('Office', 'Healthcare', 'Service', 'Student', 'Other'))`
  - *Tradeoff*: Se usaron restricciones `CHECK` en lugar de tipos `ENUM` de Postgres porque los `ENUM` de Postgres complican las migraciones de esquema (ej. `ALTER TYPE ... ADD VALUE` requiere consideraciones transaccionales especiales) y acoplan el motor a nivel de catálogo global. Las restricciones `CHECK` son portables, declarativas y se evalúan eficientemente.

### B. Estrategia de Índices
- **`coffee_health_records_pkey`**: Índice B-tree implícito sobre `(id)`. Provee búsquedas puntuales $O(\log N)$ y da soporte nativo a paginación por Keyset/Cursor (`WHERE id > :cursor ORDER BY id ASC LIMIT :n`) sin scan secuencial.
- **`idx_coffee_health_country` (`CREATE INDEX ... (country)`)**:
  - La columna `country` tiene ~18 valores distribuidos de manera uniforme (~5% de selectividad por país). Las consultas de filtro por país son altamente selectivas.
- **Tradeoff de escritura vs. lectura**: 
  - Se evitó deliberadamente crear índices sobre las demás columnas (ej. `gender`, `smoking`, `stress_level`). Columnas de baja cardinalidad (2 o 3 valores) suelen provocar que el optimizador de Postgres descarte el índice en favor de un Bitmap Index Scan o Seq Scan. Además, cada índice adicional penaliza drásticamente el *throughput* de inserción masiva en streaming.

---

## 2. Profiling, Limpieza y Validación de Datos

### A. Hallazgos del Dataset Crudo (`data/coffee_health_raw.csv`)
- **Estructura y Encoding**:
  - 10,016 líneas totales con cabecera de 16 columnas.
  - Byte Order Mark UTF-8 (`\ufeff`) presente en la cabecera. Requiere apertura con `encoding="utf-8-sig"`.
  - 3 líneas vacías (longitud 0): líneas `5953`, `10015`, `10016`.
  - Inconsistencia de columnas:
    - Líneas `4869` y `7175`: solo 13 columnas (faltan `Occupation`, `Smoking`, `Alcohol_Consumption`).
    - Línea `2659`: 17 columnas (trae una columna `extra` residual).
- **Duplicados**:
  - 10,000 IDs únicos.
  - 8 IDs con filas 100% idénticas (duplicados exactos).
  - 4 IDs con colisión en conflicto (mismo ID pero valores contradictorios de edad, pulso, etc.).

### B. Reglas de Normalización (Corregibles)
1. **Limpieza de Strings**: Remoción de espacios y tabuladores en extremos (`.strip()`). Normalización a Title Case / Capitalize respetando acrónimos comunes (`USA`, `UK`).
2. **Booleanos**: Mapeo determinista:
   - `True`: `{'1', 'true', 'yes', 'y'}`
   - `False`: `{'0', 'false', 'no', 'n'}`

### C. Límites Biológicos y Criterios de Rechazo
| Atributo | Rango Válido | Motivo de Rechazo Si No Cumple |
| :--- | :--- | :--- |
| **`Age`** | $18 \le \text{age} \le 120$ | La especificación exige adultos ($\ge 18$). Rechaza negativos (`-3`), edades imposibles (`150`, `212`) y texto corrupto (`thirty`, `4O`, `unknown`). |
| **`Sleep_Hours`**| $0.0 \le \text{sleep} \le 24.0$ | No se puede dormir menos de 0h ni más de 24h al día. Rechaza `-1`, `25.0`, `seven`. |
| **`BMI`** | $10.0 \le \text{bmi} \le 80.0$ | Límite fisiológico humano de masa corporal. Rechaza `0`, `n/a`. |
| **`Heart_Rate`** | $30 \le \text{heart\_rate} \le 220$ | Frecuencia cardíaca en reposo viable. Rechaza `0`, `350`. |
| **`Coffee_Intake`** | $0.0 \le \text{cups} \le 30.0$ | Rango diario factible. |
| **`Caffeine_mg`** | $0.0 \le \text{mg} \le 3000.0$ | Dosis fisiológicamente plausibles. |
| **`Physical_Activity_Hours`** | $0.0 \le \text{hours} \le 168.0$ | Horas por semana ($\le 24 \times 7 = 168$). |
| **Estructural** | 16 columnas exactas | Rechaza líneas vacías, truncadas o con columnas sobrantes. |

### D. Política de Idempotencia y Manejo de Ambigüedades
- **Estrategia First-Wins**: El primer registro válido para un `ID` se procesa y persiste en Postgres (`ON CONFLICT (id) DO NOTHING`).
- **Filas Duplicadas Idénticas**: Si reaparece una fila con el mismo `ID` y los mismos datos, se contabiliza como `duplicada` y se omite.
- **Colisión en Conflicto**: Si reaparece el mismo `ID` con atributos diferentes, se registra en el log de auditoría como duplicado con conflicto y se descarta para proteger la integridad del registro original previamente validado.
- **Ecuación Contable Estricta**:
  $$\text{leídas} = \text{cargadas} + \text{rechazadas} + \text{duplicadas}$$
  *(con `corregidas` contabilizado como subconjunto de filas cargadas que necesitaron normalización).*

---

## 3. Implementación del Dominio de Limpieza y Test Harness (`app/cleaning.py` & `tests/test_cleaning.py`)

### A. Estructura y Pureza Funcional
- **`CleanedRecord`**: Implementado como `dataclass(frozen=True, slots=True)`. Ofrece inmutabilidad completa y bajo consumo de memoria RAM ($O(1)$) al evitar la creación de `__dict__` por instancia.
- **Tipado Exacto con `Decimal`**: Para `coffee_intake`, `caffeine_mg`, `sleep_hours`, `bmi` y `physical_activity_hours` se utilizó `decimal.Decimal` para mapear de forma transparente a las columnas `NUMERIC` de Postgres sin pérdidas de precisión binaria.
- **Función Pura `clean_and_validate_row`**: 
  - Recibe `dict[str, str | None]` y `line_number: int`.
  - Retorna `tuple[CleanedRecord, bool]` donde el booleano `was_corrected` indica si el registro requirió transformaciones cosméticas/deterministas (espacios residuales, casing, booleanos textuales).
  - Lanza `RowRejectedError` con `line_number` y motivo explicativo ante cualquier violación del contrato de datos.

### B. Cobertura del Test Harness (59 Pruebas Unitarias Puras)
- **Suite de Pruebas**: Ubicada en `tests/test_cleaning.py`, ejecutada en ~0.09s con `pytest`:
  1. *Happy Path*: Verificación de campos y tipos sobre filas canónicas (`was_corrected = False`).
  2. *Normalización de Strings*: Limpieza de tabuladores, espacios y casing (`GOOD` $\to$ `Good`, `\tUSA ` $\to$ `USA`, `\tMale  ` $\to$ `Male`).
  3. *Mapeo de Booleanos*: Reconocimiento de variantes (`'yes'`, `'no'`, `'true'`, `'false'`, `'Y'`, `'N'`).
  4. *Rechazos Biológicos y Estructurales*: Parámetros con valores reales descubiertos en el CSV (`Age=-3`, `Age=212`, `Age=thirty`, `Sleep=-1`, `Sleep=25.0`, `BMI=0`, `BMI=n/a`, `Heart_Rate=0`, `Heart_Rate=350`, `Gender=?`, `Smoking=2`, columnas faltantes).
- **Quality Gate**: Verificado exitosamente con `ruff check`, `ruff format --check` y `mypy --strict`.

---

## 4. Motor de Ingestión en Streaming e Idempotencia (`scripts/load_data.py`)

### A. Consumo de Memoria O(1) Mediante Generadores
- **Pipeline de Streaming**: La lectura del archivo CSV se realiza utilizando `csv.reader` sobre un generador Python (`stream_and_validate_csv`), procesando fila por fila. En lugar de cargar los 10,000 registros (o decenas de millones en el futuro) en estructuras gigantescas de memoria (como DataFrames de pandas), la memoria consumida en RAM se mantiene estrictamente acotada a $O(1)$.
- **Inserción en Lotes (Batch Chunks)**: Los registros validados se agrupan en lotes de tamaño configurable (`BATCH_SIZE = 1000`) utilizando `itertools.islice`. Esto balancea la latencia de red y roundtrips a la base de datos sin sobrecargar el buffer de Postgres.
- **Deduplicación en O(1) Real**: La unicidad y el descarte de duplicados se delegan a la cláusula `ON CONFLICT (id) DO NOTHING` de PostgreSQL. Esto evita mantener conjuntos de millones de IDs en memoria en Python.

### B. Idempotencia Real y Contabilidad de Duplicados
- **Cláusula `ON CONFLICT (id) DO NOTHING RETURNING id`**:
  - Al ejecutar `cur.executemany(...)`, Postgres solo retorna los `id` de las filas efectivamente insertadas.
  - Si el script se ejecuta múltiples veces de forma consecutiva (`make load`), las filas previamente existentes no generan error de clave duplicada (`UniqueViolation`) ni duplican datos; se contabilizan como duplicadas preexistentes y se actualizan los contadores contables de forma exacta.
- **Manejo de Ambigüedades (First-Wins)**:
  - Cuando se detecta un `ID` duplicado en el mismo flujo de datos:
    * Si la fila es exactamente idéntica: se clasifica como duplicado idéntico y se omite.
    * Si la fila contiene atributos divergentes (colisión en conflicto): se aplica la política *First-Wins*, protegiendo la primera versión validada y emitiendo una alerta en `stderr` con número de línea e ID para auditoría.

### C. Métricas Finales Verificadas del Dataset
Al ejecutar la ingestión completa sobre `data/coffee_health_raw.csv`, las métricas contables son:

| Métrica | Cantidad | Descripción |
| :--- | :--- | :--- |
| **Filas leídas** | **10,012** | Filas totales excluyendo líneas vacías (`5953`, `10015`, `10016`). |
| **Filas cargadas** | **9,975** | Registros únicos y válidos persistidos en la base de datos. |
| **Filas corregidas** | **56** | Subconjunto de cargadas que requirieron normalización (casing, espacios, booleanos). |
| **Filas rechazadas** | **25** | Registros con violaciones de plausibilidad fisiológica o columnas corruptas. |
| **Filas duplicadas** | **12** | 8 duplicados idénticos + 4 duplicados con colisión en conflicto. |

$$\text{Ecuación contable balanceada}: 10,012 = 9,975 + 25 + 12$$

### D. Comportamiento en Re-ejecuciones Idempotentes (Segunda Corrida)
- **Separación Conceptual: Archivo de Entrada vs. Estado en Base de Datos**:
  - *Filas Rechazadas*: El script opera en modo *stateless* sobre el archivo crudo. Como `data/coffee_health_raw.csv` no se altera en disco, las 25 filas corruptas (ej. `Age=-3`, `Sleep_Hours=-1`) siguen presentes y el validador de dominio las vuelve a detectar y reportar en `stderr` en cada ejecución.
  - *Filas Cargadas vs. Duplicadas*: En una segunda corrida consecutiva sin `make db-reset`, la cláusula `ON CONFLICT (id) DO NOTHING` de Postgres descarta silenciosamente los 9,975 registros ya persistidos. Por tanto, las inserciones nuevas son `cargadas = 0`, y la suma de duplicados asciende a `9,987` (las 9,975 preexistentes en BD + las 12 duplicadas internas del archivo).
  - *Consistencia Semántica de "Corregidas"*: Dado que `corregidas` se define como el subconjunto de filas normalizadas que fueron efectivamente persistidas, el contador se acota dinámicamente como `min(corregidas, cargadas)`. Si en la segunda corrida no se inserta ninguna fila nueva (`cargadas = 0`), `corregidas` se reporta congruentemente en `0`:
    $$\text{Corrida 2}: \text{Leídas (10,012)} = \text{Cargadas (0)} + \text{Rechazadas (25)} + \text{Duplicadas (9,987)}$$

---

## 5. Estrategia de la API FastAPI y Paginación Keyset (`app/main.py`)

### A. Justificación Técnica: Keyset/Cursor Pagination vs. Offset Paginado
- **El Problema de `OFFSET` a Escala (Decenas de Millones)**:
  - En un esquema tradicional `OFFSET :skip LIMIT :take`, cuando un cliente solicita la página 50,000 (`OFFSET 2,500,000 LIMIT 50`), Postgres se ve obligado a escanear y descartar secuencialmente 2.5 millones de tuplas antes de devolver los 50 registros deseados. Esto genera una complejidad de tiempo $O(N)$, saturando el buffer pool y degradando drásticamente la latencia.
- **La Solución: Keyset Pagination (`WHERE id > :cursor ORDER BY id ASC LIMIT :limit + 1`)**:
  - Aprovecha directamente el índice B-tree de la clave primaria (`coffee_health_records_pkey`).
  - La búsqueda salta en tiempo logarítmico $O(\log N)$ directamente al punto deseado en el árbol B-tree y lee únicamente los siguientes $K$ nodos, garantizando **rendimiento constante $O(1)$ sin importar cuán profundo navegue el cliente**.
  - **Técnica del `Limit + 1`**: Se solicitan `limit + 1` registros al motor. Si el cursor retorna `limit + 1` filas, sabemos instantáneamente que `has_more = True` y el ID de la fila número `limit` se convierte en el `next_cursor`, eliminando la necesidad de costosos `COUNT(*)` en cada solicitud.

### B. Filtros Dinámicos y Protección Contra SQL Injection
- **Filtros Soportados**:
  - *Categóricos con multi-selección*: `country`, `gender`, `sleep_quality`, `stress_level` utilizando operadores de coincidencia de conjuntos de Postgres (`col = ANY(%(array)s)`).
  - *Rangos numéricos acotados*: `(min_age, max_age)`, `(min_coffee_intake, max_coffee_intake)`, `(min_bmi, max_bmi)`.
- **Composición Segura con `psycopg.sql`**:
  - Toda la cláusula dinámica se construye utilizando `psycopg.sql.Composable` y parámetros fuertemente tipados. Los identificadores y valores nunca se concatenan como strings sin procesar, blindando la aplicación al 100% contra inyección SQL.

### C. Manejo Estricto de Errores `422 Unprocessable Entity`
- Validación de parámetros contradictorios o imposibles antes de llegar a la base de datos:
  - `min_age > max_age` $\to$ `HTTP 422 ("min_age cannot be greater than max_age")`.
  - `min_coffee_intake > max_coffee_intake` $\to$ `HTTP 422 ("min_coffee_intake cannot be greater than max_coffee_intake")`.
  - `min_bmi > max_bmi` $\to$ `HTTP 422 ("min_bmi cannot be greater than max_bmi")`.
  - Filtros categóricos inválidos (`gender=Alien`, `sleep_quality=Terrible`, `stress_level=Extreme`) $\to$ `HTTP 422 ("Invalid ... filter")`.
  - Límites de paginación (`limit < 1` o `limit > 100`) validados de forma declarativa con `Query(ge=1, le=100)`.

### D. Endpoint Analítico `/records/summary`
- Implementa agregaciones analíticas directas en el motor relacional (`COUNT(*)`, `ROUND(AVG(sleep_hours), 2)`, `ROUND(AVG(caffeine_mg), 2)`, `ROUND(AVG(bmi), 2)`, `ROUND(AVG(heart_rate), 2)`).
- Soporta agrupación dinámica (`group_by: "country" | "stress_level" | "sleep_quality" | "gender" | "occupation"`) aplicando la misma batería de filtros del endpoint principal.

---

## 6. Pensar en la Escala: Arquitectura para 10M – 50M de Registros

### A. Procesamiento en Streaming sin Agotar Memoria ($O(1)$ Space Complexity)
- **Consumo de Memoria en Python**:
  - La implementación en `scripts/load_data.py` utiliza generadores (`stream_and_validate_csv`) que consumen el archivo línea por línea mediante buffers acotados. 
  - Si el dataset crece de 10,000 filas (~750 KB) a 50,000,000 de filas (~4 GB a 5 GB), el consumo de memoria RAM del proceso de Python permanece plano en **menos de 50 MB**, evitando desbordamientos de memoria (`OOM Killer`).
- **Evolución del Throughput de Carga (Batch Inserts vs. `COPY`)**:
  - Para 10,000 a 1,000,000 filas: `cur.executemany(...)` en lotes de 1,000 con `ON CONFLICT (id) DO NOTHING` es la solución más limpia y atómica.
  - Para 10M a 50M filas: El costo del parser SQL por fila se convierte en el cuello de botella de CPU. La evolución recomendada en producción es:
    1. Cargar el stream mediante `psycopg.Cursor.copy()` (`COPY ... FROM STDIN (FORMAT csv)`) hacia una tabla temporal no registrada (`UNLOGGED TABLE staging_coffee_health ON COMMIT DROP`). `COPY` en Postgres es hasta **5 veces más rápido** que sentencias `INSERT`.
    2. Ejecutar una transferencia masiva por lotes:
       ```sql
       INSERT INTO public.coffee_health_records
       SELECT * FROM staging_coffee_health
       ON CONFLICT (id) DO NOTHING;
       ```
    3. Ajuste de parámetros de sesión durante la ingesta masiva: `SET synchronous_commit = off;`, incremento de `maintenance_work_mem = '1GB'` y aumento de `max_wal_size = '16GB'` para reducir la frecuencia de *checkpoints* de disco.

### B. Tradeoffs de Índices: Escritura Masiva vs. Lectura Concurrente
- **El Costo Oculto de los Índices B-Tree**:
  - Cada índice B-Tree adicional penaliza severamente el rendimiento de escritura. En cada inserción, Postgres debe atravesar el árbol de índice, balancear ramas, partir páginas (*page splits*) y registrar entradas en el WAL (*Write-Ahead Logging*).
  - En una tabla de 50 millones de filas con 8 índices, más del 60% del tiempo de carga y del ancho de banda de I/O se consume manteniendo índices secundarios, no escribiendo los datos reales en la tabla (*heap*).
- **Decisión Tomada**:
  - Se mantuvo exclusivamente la clave primaria `(id)` y un índice selectivo sobre `(country)`.
- **Alternativa a Escala: Índices BRIN (Block Range Index)**:
  - Si la tabla de 50M de filas crece de forma secuencial (ordenada por `id` o por fecha de inserción), un índice **BRIN** resume el rango de valores presentes en bloques continuos de páginas físicas de disco (ej. cada 128 páginas).
  - *Tradeoff de BRIN*:
    - Tamaño: Un índice B-Tree sobre 50M de enteros ocupa ~1.1 GB en RAM/disco; un índice BRIN equivalente ocupa apenas **~64 KB** (una reducción superior al 99%).
    - Impacto en escritura: El sobrecosto de actualización al insertar es prácticamente cero.

### C. Comportamiento de Paginación: Página 1 vs. Página 50,000
- **Offset Paginado (`OFFSET 2,500,000 LIMIT 50`)**:
  - Complejidad $O(N)$. Postgres debe escanear el índice o la tabla, contabilizar 2.5 millones de tuplas, descartarlas todas en memoria, y quedarse con las 50 finales.
  - En la página 50,000, la latencia pasa de 2 milisegundos a **más de 1,800 ms**, saturando la CPU del motor.
  - Problema de *Drift* / Desplazamiento: Si ocurren inserciones o borrados concurrentes mientras el usuario pagina, las páginas sufren saltos de registros o registros repetidos.
- **Keyset / Cursor Pagination (`WHERE id > :cursor ORDER BY id ASC LIMIT 50`)**:
  - Complejidad $O(\log N)$ constante. El motor realiza una búsqueda directa en el árbol B-tree de la clave primaria hasta el valor de `:cursor` y recorre únicamente las siguientes 50 hojas secuenciales.
  - En la página 50,000, **la latencia sigue siendo idéntica a la página 1 (~2 ms)**.
  - Es 100% determinista e inmune a inserciones o borrados concurrentes.

### D. Pool de Conexiones y Concurrencia en FastAPI
- **El Cuello de Botella de Procesos en Postgres**:
  - FastAPI con `uvicorn` puede manejar fácilmente miles de conexiones HTTP concurrentes en su event-loop no bloqueante. Sin embargo, PostgreSQL utiliza un modelo basado en procesos del sistema operativo (`fork`), donde cada conexión consume memoria dedicada (~5 MB a 10 MB de RAM) y compite por semáforos y bloqueos internos.
  - Abrir 1,000 conexiones concurrentes directas a Postgres degrada el rendimiento drásticamente debido a la sobrecarga de *context switching* en los núcleos del procesador.
- **Sizing Óptimo del Pool (`psycopg_pool.ConnectionPool`)**:
  - Siguiendo la fórmula empírica de PostgreSQL y HikariCP:
    $$\text{conexiones\_máximas} = (\text{núcleos\_cpu} \times 2) + \text{disco\_efectivo}$$
    En una máquina de 4 núcleos, un pool de 10 a 20 conexiones provee el máximo *throughput* de consultas sin saturar la CPU.
- **Arquitectura para Cargas Extremas (PgBouncer)**:
  - Para soportar más de 20,000 clientes concurrentes en producción, se interpone un intermediario de conexiones como **PgBouncer** configurado en modo *Transaction Pooling* entre las réplicas de FastAPI y Postgres.
  - Esto permite que miles de conexiones entrantes de FastAPI compartan dinámicamente un pool estrecho de solo 30 a 50 conexiones reales a Postgres, atendiendo cada transacción y liberando la conexión de inmediato.

### E. Particionamiento Declarativo de Tablas
- Cuando la tabla sobrepasa los 20–50 millones de filas (~10 GB a 20 GB de datos + índices), el índice de la tabla supera el tamaño del `shared_buffers` (RAM asignada a Postgres) y las consultas empiezan a generar lecturas en disco lentas (*cache misses*).
- **Estrategia: Particionamiento por Rango sobre `id`**:
  ```sql
  CREATE TABLE public.coffee_health_records (
      id INTEGER NOT NULL,
      ...
  ) PARTITION BY RANGE (id);

  CREATE TABLE coffee_health_records_p1 PARTITION OF coffee_health_records
      FOR VALUES FROM (1) TO (10000000);
  CREATE TABLE coffee_health_records_p2 PARTITION OF coffee_health_records
      FOR VALUES FROM (10000000) TO (20000000);
  ```
- **Ventajas Operativas**:
  - *Partition Pruning*: Una consulta de paginación con `WHERE id > :cursor` descarta automáticamente todas las particiones que no contienen ese rango de IDs sin leer sus índices ni tablas.
  - *Mantenimiento Concurrente*: Operaciones de `VACUUM ANALYZE`, reindexación o archivo histórico (*cold storage*) se ejecutan de manera aislada por partición sin bloquear la tabla completa.
