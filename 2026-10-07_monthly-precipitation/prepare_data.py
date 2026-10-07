"""Prepare PostgreSQL data for practice-analysis.

Creates or extends the trusted canonical weather table as needed, then
materializes the exercise-specific practice table.

Requires a running PostgreSQL instance accessible through libpq defaults
or standard PG* environment variables.
"""

import time

import pandas as pd
import psycopg
import requests
from psycopg import sql


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

DATABASE_NAME = "analytical_workflow_fluency"

CANONICAL_SCHEMA = "canonical"
CANONICAL_TABLE = "weather_hourly"

PRACTICE_SCHEMA = "practice_008"
PRACTICE_TABLE = "weather_hourly"

LOCAL_TIMEZONE = "America/Edmonton"

PRACTICE_START_LOCAL = pd.Timestamp(
    "2000-01-01 00:00",
    tz=LOCAL_TIMEZONE,
)

PRACTICE_END_LOCAL = pd.Timestamp(
    "2026-10-01 00:00",
    tz=LOCAL_TIMEZONE,
)

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"

LATITUDE = 51.0447
LONGITUDE = -114.0719
MODEL = "era5"

# Empirical pacing from prior development; not an API guarantee.
REQUEST_DELAY_SECONDS = 4

HOURLY_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "precipitation",
    "rain",
    "snowfall",
    "weather_code",
    "cloud_cover",
    "pressure_msl",
    "surface_pressure",
    "wind_speed_10m",
    "wind_direction_10m",
    "shortwave_radiation",
    "vapour_pressure_deficit",
    "soil_temperature_0_to_7cm",
]

COLUMNS = [
    "time",
    *HOURLY_VARIABLES,
]

EXPECTED_CANONICAL_COLUMNS = [
    ("time", "timestamp with time zone", "NO"),
    ("temperature_2m", "double precision", "YES"),
    ("relative_humidity_2m", "double precision", "YES"),
    ("dew_point_2m", "double precision", "YES"),
    ("precipitation", "double precision", "YES"),
    ("rain", "double precision", "YES"),
    ("snowfall", "double precision", "YES"),
    ("weather_code", "integer", "YES"),
    ("cloud_cover", "double precision", "YES"),
    ("pressure_msl", "double precision", "YES"),
    ("surface_pressure", "double precision", "YES"),
    ("wind_speed_10m", "double precision", "YES"),
    ("wind_direction_10m", "double precision", "YES"),
    ("shortwave_radiation", "double precision", "YES"),
    ("vapour_pressure_deficit", "double precision", "YES"),
    ("soil_temperature_0_to_7cm", "double precision", "YES"),
]

STAGING_TABLE = "staging_weather"


# ---------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------

def interval_contract(
    start_local,
    end_local,
):
    start_local = pd.Timestamp(start_local)
    end_local = pd.Timestamp(end_local)

    if (
        start_local.tz is None
        or end_local.tz is None
    ):
        raise ValueError(
            "Interval boundaries must be timezone-aware."
        )

    if start_local >= end_local:
        raise ValueError(
            "Interval start must precede end."
        )

    start_utc = start_local.tz_convert("UTC")
    end_utc = end_local.tz_convert("UTC")

    expected_times = pd.date_range(
        start=start_utc,
        end=end_utc,
        freq="h",
        inclusive="left",
    )

    return (
        start_utc,
        end_utc,
        expected_times,
    )


# ---------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------

def fetch_weather_interval(
    start_local,
    end_local,
):
    (
        start_utc,
        end_utc,
        expected_times,
    ) = interval_contract(
        start_local,
        end_local,
    )

    api_start_date = start_utc.date().isoformat()

    api_end_date = (
        end_utc
        - pd.Timedelta(hours=1)
    ).date().isoformat()

    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "start_date": api_start_date,
        "end_date": api_end_date,
        "hourly": ",".join(HOURLY_VARIABLES),
        "models": MODEL,
        "timezone": "GMT",
        "timeformat": "unixtime",
    }

    response = requests.get(
        OPEN_METEO_URL,
        params=params,
        timeout=60,
    )
    response.raise_for_status()

    payload = response.json()

    if (
        payload.get("timezone") != "GMT"
        or payload.get("utc_offset_seconds") != 0
    ):
        raise ValueError(
            "Unexpected source timezone."
        )

    hourly = payload.get("hourly")

    if not isinstance(hourly, dict):
        raise ValueError(
            "Missing hourly data."
        )

    expected_keys = {
        "time",
        *HOURLY_VARIABLES,
    }

    if set(hourly) != expected_keys:
        raise ValueError(
            "Unexpected hourly fields."
        )

    lengths = {
        len(values)
        for values in hourly.values()
    }

    if len(lengths) != 1:
        raise ValueError(
            "Hourly arrays have unequal lengths."
        )

    frame = pd.DataFrame(hourly)

    frame["time"] = pd.to_datetime(
        frame["time"],
        unit="s",
        utc=True,
    )

    frame = (
        frame
        .loc[
            frame["time"].ge(start_utc)
            & frame["time"].lt(end_utc),
            COLUMNS,
        ]
        .reset_index(drop=True)
    )

    actual_times = pd.DatetimeIndex(
        frame["time"]
    )

    missing_times = expected_times.difference(
        actual_times
    )
    unexpected_times = actual_times.difference(
        expected_times
    )

    if (
        frame["time"].isna().any()
        or not frame["time"].is_unique
        or not frame["time"].is_monotonic_increasing
        or len(missing_times) > 0
        or len(unexpected_times) > 0
    ):
        raise ValueError(
            "Source timestamp validation failed."
        )

    return frame


def split_request_interval(
    start_utc,
    end_utc,
):
    start_local = start_utc.tz_convert(
        LOCAL_TIMEZONE
    )
    end_local = end_utc.tz_convert(
        LOCAL_TIMEZONE
    )

    batches = []
    current_start = start_local

    while current_start < end_local:
        next_year = pd.Timestamp(
            f"{current_start.year + 1}-01-01 00:00",
            tz=LOCAL_TIMEZONE,
        )

        batch_end = min(
            next_year,
            end_local,
        )

        batches.append(
            (
                current_start,
                batch_end,
            )
        )

        current_start = batch_end

    return batches


def fetch_missing_intervals(
    missing_intervals,
):
    request_intervals = []

    for (
        start_utc,
        end_utc,
    ) in missing_intervals:
        request_intervals.extend(
            split_request_interval(
                start_utc,
                end_utc,
            )
        )

    if not request_intervals:
        return None

    frames = []

    for index, (
        start_local,
        end_local,
    ) in enumerate(request_intervals):
        print(
            "Fetching:",
            start_local,
            "to",
            end_local,
        )

        frames.append(
            fetch_weather_interval(
                start_local,
                end_local,
            )
        )

        if index < len(request_intervals) - 1:
            time.sleep(
                REQUEST_DELAY_SECONDS
            )

    frame = pd.concat(
        frames,
        ignore_index=True,
    )

    if frame["time"].duplicated().any():
        raise ValueError(
            "Duplicate timestamps across source batches."
        )

    return frame


# ---------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------

def ensure_database():
    with psycopg.connect(
        dbname="postgres",
        autocommit=True,
    ) as conn:
        exists = conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_database
                WHERE datname = %s
            );
            """,
            (DATABASE_NAME,),
        ).fetchone()[0]

        if not exists:
            conn.execute(
                sql.SQL("""
                    CREATE DATABASE {}
                """).format(
                    sql.Identifier(
                        DATABASE_NAME
                    )
                )
            )

        conn.execute(
            sql.SQL("""
                ALTER DATABASE {}
                SET timezone TO {}
            """).format(
                sql.Identifier(
                    DATABASE_NAME
                ),
                sql.Literal("UTC"),
            )
        )


def ensure_canonical_storage(conn):
    conn.execute(
        sql.SQL("""
            CREATE SCHEMA IF NOT EXISTS {}
        """).format(
            sql.Identifier(
                CANONICAL_SCHEMA
            )
        )
    )

    conn.execute(
        sql.SQL("""
            CREATE TABLE IF NOT EXISTS {}.{} (
                time TIMESTAMPTZ PRIMARY KEY,
                temperature_2m DOUBLE PRECISION,
                relative_humidity_2m DOUBLE PRECISION,
                dew_point_2m DOUBLE PRECISION,
                precipitation DOUBLE PRECISION,
                rain DOUBLE PRECISION,
                snowfall DOUBLE PRECISION,
                weather_code INTEGER,
                cloud_cover DOUBLE PRECISION,
                pressure_msl DOUBLE PRECISION,
                surface_pressure DOUBLE PRECISION,
                wind_speed_10m DOUBLE PRECISION,
                wind_direction_10m DOUBLE PRECISION,
                shortwave_radiation DOUBLE PRECISION,
                vapour_pressure_deficit DOUBLE PRECISION,
                soil_temperature_0_to_7cm DOUBLE PRECISION
            )
        """).format(
            sql.Identifier(
                CANONICAL_SCHEMA
            ),
            sql.Identifier(
                CANONICAL_TABLE
            ),
        )
    )


def validate_canonical_storage(conn):
    columns = conn.execute(
        """
        SELECT
            column_name,
            data_type,
            is_nullable
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY ordinal_position;
        """,
        (
            CANONICAL_SCHEMA,
            CANONICAL_TABLE,
        ),
    ).fetchall()

    if columns != EXPECTED_CANONICAL_COLUMNS:
        raise ValueError(
            "Canonical column contract mismatch."
        )

    primary_key_columns = (
        conn.execute(
            """
            SELECT
                kcu.column_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = %s
              AND tc.table_name = %s
            ORDER BY kcu.ordinal_position;
            """,
            (
                CANONICAL_SCHEMA,
                CANONICAL_TABLE,
            ),
        )
        .fetchall()
    )

    if primary_key_columns != [("time",)]:
        raise ValueError(
            "Canonical primary-key contract mismatch."
        )


# ---------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------

def get_canonical_times(
    conn,
    start_utc,
    end_utc,
):
    rows = conn.execute(
        sql.SQL("""
            SELECT time
            FROM {}.{}
            WHERE time >= %s
              AND time < %s
            ORDER BY time
        """).format(
            sql.Identifier(
                CANONICAL_SCHEMA
            ),
            sql.Identifier(
                CANONICAL_TABLE
            ),
        ),
        (
            start_utc.to_pydatetime(),
            end_utc.to_pydatetime(),
        ),
    ).fetchall()

    if not rows:
        return pd.DatetimeIndex(
            [],
            tz="UTC",
        )

    return pd.DatetimeIndex(
        [row[0] for row in rows]
    ).tz_convert("UTC")


def coverage_diff(
    conn,
    start_local,
    end_local,
):
    (
        start_utc,
        end_utc,
        expected_times,
    ) = interval_contract(
        start_local,
        end_local,
    )

    actual_times = get_canonical_times(
        conn,
        start_utc,
        end_utc,
    )

    missing_times = (
        expected_times
        .difference(actual_times)
        .sort_values()
    )

    unexpected_times = (
        actual_times
        .difference(expected_times)
        .sort_values()
    )

    return (
        missing_times,
        unexpected_times,
    )


def timestamps_to_intervals(
    timestamps,
):
    if len(timestamps) == 0:
        return []

    timestamps = pd.DatetimeIndex(
        timestamps
    ).sort_values()

    intervals = []
    interval_start = timestamps[0]
    previous = timestamps[0]

    for timestamp in timestamps[1:]:
        if (
            timestamp - previous
            != pd.Timedelta(hours=1)
        ):
            intervals.append(
                (
                    interval_start,
                    previous
                    + pd.Timedelta(hours=1),
                )
            )
            interval_start = timestamp

        previous = timestamp

    intervals.append(
        (
            interval_start,
            previous
            + pd.Timedelta(hours=1),
        )
    )

    return intervals


def find_missing_intervals(
    conn,
    start_local,
    end_local,
):
    (
        missing_times,
        unexpected_times,
    ) = coverage_diff(
        conn,
        start_local,
        end_local,
    )

    if len(unexpected_times) > 0:
        raise ValueError(
            "Canonical contains unexpected timestamps."
        )

    return timestamps_to_intervals(
        missing_times
    )


def validate_coverage(
    conn,
    start_local,
    end_local,
):
    (
        missing_times,
        unexpected_times,
    ) = coverage_diff(
        conn,
        start_local,
        end_local,
    )

    if (
        len(missing_times) > 0
        or len(unexpected_times) > 0
    ):
        raise ValueError(
            "Canonical coverage is invalid."
        )


# ---------------------------------------------------------------------
# Canonical load
# ---------------------------------------------------------------------

def create_staging_table(conn):
    conn.execute(
        sql.SQL("""
            CREATE TEMP TABLE {}
            ON COMMIT DROP
            AS
            SELECT *
            FROM {}.{}
            WITH NO DATA
        """).format(
            sql.Identifier(
                STAGING_TABLE
            ),
            sql.Identifier(
                CANONICAL_SCHEMA
            ),
            sql.Identifier(
                CANONICAL_TABLE
            ),
        )
    )


def copy_to_staging(
    conn,
    frame,
):
    column_sql = sql.SQL(", ").join(
        sql.Identifier(column)
        for column in COLUMNS
    )

    copy_statement = sql.SQL("""
        COPY {} ({})
        FROM STDIN
    """).format(
        sql.Identifier(
            STAGING_TABLE
        ),
        column_sql,
    )

    with conn.cursor() as cur:
        with cur.copy(
            copy_statement
        ) as copy:
            for row in frame[
                COLUMNS
            ].itertuples(
                index=False,
                name=None,
            ):
                output_row = []

                for (
                    column,
                    value,
                ) in zip(
                    COLUMNS,
                    row,
                ):
                    if pd.isna(value):
                        output_row.append(
                            None
                        )

                    elif column == "time":
                        output_row.append(
                            value.to_pydatetime()
                        )

                    elif column == "weather_code":
                        numeric_value = float(
                            value
                        )

                        if not numeric_value.is_integer():
                            raise ValueError(
                                "weather_code is not integral."
                            )

                        output_row.append(
                            int(numeric_value)
                        )

                    else:
                        output_row.append(
                            float(value)
                        )

                copy.write_row(
                    tuple(output_row)
                )


def validate_staging(
    conn,
    expected_rows,
):
    row_count = conn.execute(
        sql.SQL("""
            SELECT COUNT(*)
            FROM {}
        """).format(
            sql.Identifier(
                STAGING_TABLE
            )
        )
    ).fetchone()[0]

    null_keys = conn.execute(
        sql.SQL("""
            SELECT COUNT(*)
            FROM {}
            WHERE time IS NULL
        """).format(
            sql.Identifier(
                STAGING_TABLE
            )
        )
    ).fetchone()[0]

    duplicate_keys = conn.execute(
        sql.SQL("""
            SELECT COUNT(*)
            FROM (
                SELECT time
                FROM {}
                GROUP BY time
                HAVING COUNT(*) > 1
            ) AS duplicates
        """).format(
            sql.Identifier(
                STAGING_TABLE
            )
        )
    ).fetchone()[0]

    if (
        row_count != expected_rows
        or null_keys != 0
        or duplicate_keys != 0
    ):
        raise ValueError(
            "Staging validation failed."
        )


def load_canonical(
    conn,
    frame,
):
    create_staging_table(conn)

    copy_to_staging(
        conn,
        frame,
    )

    validate_staging(
        conn,
        len(frame),
    )

    column_sql = sql.SQL(", ").join(
        sql.Identifier(column)
        for column in COLUMNS
    )

    result = conn.execute(
        sql.SQL("""
            INSERT INTO {}.{} ({})
            SELECT {}
            FROM {}
            ON CONFLICT (time) DO NOTHING
        """).format(
            sql.Identifier(
                CANONICAL_SCHEMA
            ),
            sql.Identifier(
                CANONICAL_TABLE
            ),
            column_sql,
            column_sql,
            sql.Identifier(
                STAGING_TABLE
            ),
        )
    )

    return result.rowcount


# ---------------------------------------------------------------------
# Practice materialization
# ---------------------------------------------------------------------

def materialize_practice_table(conn):
    (
        start_utc,
        end_utc,
        _,
    ) = interval_contract(
        PRACTICE_START_LOCAL,
        PRACTICE_END_LOCAL,
    )

    conn.execute(
        sql.SQL("""
            CREATE SCHEMA IF NOT EXISTS {}
        """).format(
            sql.Identifier(
                PRACTICE_SCHEMA
            )
        )
    )

    conn.execute(
        sql.SQL("""
            DROP TABLE IF EXISTS {}.{}
        """).format(
            sql.Identifier(
                PRACTICE_SCHEMA
            ),
            sql.Identifier(
                PRACTICE_TABLE
            ),
        )
    )

    column_sql = sql.SQL(", ").join(
        sql.Identifier(column)
        for column in COLUMNS
    )

    conn.execute(
        sql.SQL("""
            CREATE TABLE {}.{} AS
            SELECT {}
            FROM {}.{}
            WHERE time >= %s
              AND time < %s
        """).format(
            sql.Identifier(
                PRACTICE_SCHEMA
            ),
            sql.Identifier(
                PRACTICE_TABLE
            ),
            column_sql,
            sql.Identifier(
                CANONICAL_SCHEMA
            ),
            sql.Identifier(
                CANONICAL_TABLE
            ),
        ),
        (
            start_utc.to_pydatetime(),
            end_utc.to_pydatetime(),
        ),
    )


def validate_practice_table(conn):
    (
        _,
        _,
        expected_times,
    ) = interval_contract(
        PRACTICE_START_LOCAL,
        PRACTICE_END_LOCAL,
    )

    summary = conn.execute(
        sql.SQL("""
            SELECT
                COUNT(*),
                MIN(time),
                MAX(time)
            FROM {}.{}
        """).format(
            sql.Identifier(
                PRACTICE_SCHEMA
            ),
            sql.Identifier(
                PRACTICE_TABLE
            ),
        )
    ).fetchone()

    if summary[0] != len(expected_times):
        raise ValueError(
            "Practice row-count validation failed."
        )

    actual_start = pd.Timestamp(
        summary[1]
    ).tz_convert("UTC")

    actual_end = pd.Timestamp(
        summary[2]
    ).tz_convert("UTC")

    if (
        actual_start != expected_times[0]
        or actual_end != expected_times[-1]
    ):
        raise ValueError(
            "Practice boundary validation failed."
        )

    return summary


# ---------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------

def main():
    ensure_database()

    with psycopg.connect(
        dbname=DATABASE_NAME
    ) as conn:
        ensure_canonical_storage(conn)
        validate_canonical_storage(conn)

        missing_intervals = (
            find_missing_intervals(
                conn,
                PRACTICE_START_LOCAL,
                PRACTICE_END_LOCAL,
            )
        )

    incoming_df = fetch_missing_intervals(
        missing_intervals
    )

    with psycopg.connect(
        dbname=DATABASE_NAME
    ) as conn:
        inserted_rows = 0

        if incoming_df is not None:
            inserted_rows = load_canonical(
                conn,
                incoming_df,
            )

        validate_coverage(
            conn,
            PRACTICE_START_LOCAL,
            PRACTICE_END_LOCAL,
        )

        materialize_practice_table(conn)

        practice_summary = (
            validate_practice_table(conn)
        )

    print(
        "Canonical rows inserted:",
        inserted_rows,
    )

    print(
        "Practice table:",
        f"{PRACTICE_SCHEMA}.{PRACTICE_TABLE}",
    )

    print(
        "Practice summary:",
        practice_summary,
    )


if __name__ == "__main__":
    main()
