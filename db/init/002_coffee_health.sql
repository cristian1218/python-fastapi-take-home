-- Medallo Take-Home: Schema for coffee and health dataset
-- Deliberate types: SMALLINT for compact integers, NUMERIC(p,s) for exact decimals, BOOLEAN for flags.
-- Domain check constraints enforce physical plausibility at database level.

CREATE TABLE IF NOT EXISTS public.coffee_health_records (
    id INTEGER PRIMARY KEY,
    age SMALLINT NOT NULL CHECK (age >= 18 AND age <= 120),
    gender VARCHAR(10) NOT NULL CHECK (gender IN ('Male', 'Female', 'Other')),
    country VARCHAR(100) NOT NULL,
    coffee_intake NUMERIC(4, 1) NOT NULL CHECK (coffee_intake >= 0.0 AND coffee_intake <= 30.0),
    caffeine_mg NUMERIC(6, 1) NOT NULL CHECK (caffeine_mg >= 0.0 AND caffeine_mg <= 3000.0),
    sleep_hours NUMERIC(4, 1) NOT NULL CHECK (sleep_hours >= 0.0 AND sleep_hours <= 24.0),
    sleep_quality VARCHAR(15) NOT NULL CHECK (sleep_quality IN ('Poor', 'Fair', 'Good', 'Excellent')),
    bmi NUMERIC(4, 1) NOT NULL CHECK (bmi >= 10.0 AND bmi <= 80.0),
    heart_rate SMALLINT NOT NULL CHECK (heart_rate >= 30 AND heart_rate <= 220),
    stress_level VARCHAR(10) NOT NULL CHECK (stress_level IN ('Low', 'Medium', 'High')),
    physical_activity_hours NUMERIC(4, 1) NOT NULL CHECK (physical_activity_hours >= 0.0 AND physical_activity_hours <= 168.0),
    health_issues VARCHAR(15) NOT NULL CHECK (health_issues IN ('None', 'Mild', 'Moderate', 'Severe')),
    occupation VARCHAR(30) NOT NULL CHECK (occupation IN ('Office', 'Healthcare', 'Service', 'Student', 'Other')),
    smoking BOOLEAN NOT NULL,
    alcohol_consumption BOOLEAN NOT NULL
);

-- Index for the most selective categorical filter in the API (country).
-- Other indexes are intentionally avoided to maximize write throughput during bulk ingestion.
CREATE INDEX IF NOT EXISTS idx_coffee_health_country ON public.coffee_health_records (country);
