"""Prepare PostgreSQL weather data for Practice 13.

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


# Configuration
DATABASE_NAME = "analytical_workflow_fluency"

CANONICAL_SCHEMA = "canonical"
CANONICAL_TABLE = "weather_hourly"

PRACTICE_SCHEMA = "practice_013"
PRACTICE_TABLE = "weather_hourly"

URL = "https://archive-api.open-meteo.com/v1/archive"

LATITUDE = 51.0447
LONGITUDE = -114.0719
MODEL = "era5"
LOCAL_TIMEZONE = "America/Edmonton"

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

COLUMNS = ["time", *HOURLY_VARIABLES]

START_LOCAL = pd.Timestamp(
    "2000-01-01 00:00",
    tz=LOCAL_TIMEZONE,
)

END_LOCAL = pd.Timestamp(
    "2026-10-01 00:00",
    tz=LOCAL_TIMEZONE,
)

REQUEST_DELAY_SECONDS = 4


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
            )
            """,
            (DATABASE_NAME,),
        ).fetchone()[0]

        if not exists:
            conn.execute(
                sql.SQL(
                    "CREATE DATABASE {}"
                ).format(
                    sql.Identifier(DATABASE_NAME)
                )
            )

        # Keep TIMESTAMPTZ display consistent across environments.
        conn.execute(
            sql.SQL(
                "ALTER DATABASE {} SET timezone TO 'UTC'"
            ).format(
                sql.Identifier(DATABASE_NAME)
            )
        )


def create_year_batches(start_local, end_local):
    batches = []
    batch_start = start_local

    while batch_start < end_local:
        next_year = pd.Timestamp(
            year=batch_start.year + 1,
            month=1,
            day=1,
            tz=batch_start.tz,
        )

        batch_end = min(next_year, end_local)
        batches.append((batch_start, batch_end))
        batch_start = batch_end

    return batches


def fetch_weather_batch(start_local, end_local):
    start_utc = start_local.tz_convert("UTC")
    end_utc = end_local.tz_convert("UTC")

    # API accepts dates, so request the UTC envelope then trim.
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "start_date": start_utc.date().isoformat(),
        "end_date": (
            end_utc - pd.Timedelta(hours=1)
        ).date().isoformat(),
        "hourly": ",".join(HOURLY_VARIABLES),
        "models": MODEL,
        "timezone": "GMT",
        "timeformat": "unixtime",
    }

    response = requests.get(
        URL,
        params=params,
        timeout=60,
    )
    response.raise_for_status()

    batch_df = pd.DataFrame(
        response.json()["hourly"]
    )

    batch_df["time"] = pd.to_datetime(
        batch_df["time"],
        unit="s",
        utc=True,
    )

    return (
        batch_df.loc[
            batch_df["time"].ge(start_utc)
            & batch_df["time"].lt(end_utc),
            COLUMNS,
        ]
        .reset_index(drop=True)
    )


def fetch_weather(start_local, end_local):
    batches = create_year_batches(
        start_local,
        end_local,
    )

    frames = []

    for i, (batch_start, batch_end) in enumerate(
        batches,
        start=1,
    ):
        print(
            f"Request {i}/{len(batches)}: "
            f"{batch_start} -> {batch_end}"
        )

        frames.append(
            fetch_weather_batch(
                batch_start,
                batch_end,
            )
        )

        if i < len(batches):
            time.sleep(REQUEST_DELAY_SECONDS)

    df = pd.concat(
        frames,
        ignore_index=True,
    )

    expected_time = pd.date_range(
        start=start_local.tz_convert("UTC"),
        end=end_local.tz_convert("UTC"),
        freq="h",
        inclusive="left",
    )

    if not pd.DatetimeIndex(
        df["time"]
    ).equals(expected_time):
        raise ValueError(
            "Unexpected hourly coverage."
        )

    return df


def ensure_canonical_table(conn):
    conn.execute(
        sql.SQL(
            "CREATE SCHEMA IF NOT EXISTS {}"
        ).format(
            sql.Identifier(CANONICAL_SCHEMA)
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
            sql.Identifier(CANONICAL_SCHEMA),
            sql.Identifier(CANONICAL_TABLE),
        )
    )


def get_canonical_times(
    conn,
    start_local,
    end_local,
):
    start_utc = start_local.tz_convert("UTC")
    end_utc = end_local.tz_convert("UTC")

    rows = conn.execute(
        sql.SQL("""
            SELECT time
            FROM {}.{}
            WHERE time >= %s
              AND time < %s
            ORDER BY time
        """).format(
            sql.Identifier(CANONICAL_SCHEMA),
            sql.Identifier(CANONICAL_TABLE),
        ),
        (
            start_utc.to_pydatetime(),
            end_utc.to_pydatetime(),
        ),
    ).fetchall()

    if not rows:
        return pd.DatetimeIndex([], tz="UTC")

    return pd.DatetimeIndex(
        [row[0] for row in rows]
    ).tz_convert("UTC")


def find_missing_intervals(
    conn,
    start_local,
    end_local,
):
    expected = pd.date_range(
        start=start_local.tz_convert("UTC"),
        end=end_local.tz_convert("UTC"),
        freq="h",
        inclusive="left",
    )

    existing = get_canonical_times(
        conn,
        start_local,
        end_local,
    )

    missing = expected.difference(existing)

    if len(missing) == 0:
        return []

    intervals = []
    interval_start = missing[0]
    previous = missing[0]

    for timestamp in missing[1:]:
        if (
            timestamp - previous
            != pd.Timedelta(hours=1)
        ):
            intervals.append(
                (
                    interval_start,
                    previous + pd.Timedelta(hours=1),
                )
            )
            interval_start = timestamp

        previous = timestamp

    intervals.append(
        (
            interval_start,
            previous + pd.Timedelta(hours=1),
        )
    )

    return intervals


def copy_to_canonical(conn, df):
    columns_sql = sql.SQL(", ").join(
        sql.Identifier(column)
        for column in COLUMNS
    )

    statement = sql.SQL("""
        COPY {}.{} ({})
        FROM STDIN
    """).format(
        sql.Identifier(CANONICAL_SCHEMA),
        sql.Identifier(CANONICAL_TABLE),
        columns_sql,
    )

    with conn.cursor().copy(statement) as copy:
        for row in df[COLUMNS].itertuples(
            index=False,
            name=None,
        ):
            output = []

            # Preserve source nulls as SQL NULL.
            for column, value in zip(COLUMNS, row):
                if pd.isna(value):
                    output.append(None)

                elif column == "time":
                    output.append(
                        value.to_pydatetime()
                    )

                elif column == "weather_code":
                    number = float(value)

                    if not number.is_integer():
                        raise ValueError(
                            "weather_code is not integral."
                        )

                    output.append(int(number))

                else:
                    output.append(float(value))

            copy.write_row(output)


def materialize_practice_table(conn):
    start_utc = START_LOCAL.tz_convert("UTC")
    end_utc = END_LOCAL.tz_convert("UTC")

    conn.execute(
        sql.SQL(
            "DROP SCHEMA IF EXISTS {} CASCADE"
        ).format(
            sql.Identifier(PRACTICE_SCHEMA)
        )
    )

    conn.execute(
        sql.SQL(
            "CREATE SCHEMA {}"
        ).format(
            sql.Identifier(PRACTICE_SCHEMA)
        )
    )

    columns_sql = sql.SQL(", ").join(
        sql.Identifier(column)
        for column in COLUMNS
    )

    # CTAS copies data/types without canonical constraints.
    conn.execute(
        sql.SQL("""
            CREATE TABLE {}.{} AS
            SELECT {}
            FROM {}.{}
            WHERE time >= %s
              AND time < %s
        """).format(
            sql.Identifier(PRACTICE_SCHEMA),
            sql.Identifier(PRACTICE_TABLE),
            columns_sql,
            sql.Identifier(CANONICAL_SCHEMA),
            sql.Identifier(CANONICAL_TABLE),
        ),
        (
            start_utc.to_pydatetime(),
            end_utc.to_pydatetime(),
        ),
    )


def main():
    ensure_database()

    with psycopg.connect(
        dbname=DATABASE_NAME
    ) as conn:
        ensure_canonical_table(conn)

        missing_intervals = find_missing_intervals(
            conn,
            START_LOCAL,
            END_LOCAL,
        )

    frames = []

    for i, (start_utc, end_utc) in enumerate(
        missing_intervals
    ):
        frames.append(
            fetch_weather(
                start_utc.tz_convert(
                    LOCAL_TIMEZONE
                ),
                end_utc.tz_convert(
                    LOCAL_TIMEZONE
                ),
            )
        )

        if i < len(missing_intervals) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)

    incoming_df = (
        pd.concat(
            frames,
            ignore_index=True,
        )
        if frames
        else None
    )

    with psycopg.connect(
        dbname=DATABASE_NAME
    ) as conn:
        if incoming_df is not None:
            copy_to_canonical(
                conn,
                incoming_df,
            )

        expected = pd.date_range(
            start=START_LOCAL.tz_convert("UTC"),
            end=END_LOCAL.tz_convert("UTC"),
            freq="h",
            inclusive="left",
        )

        actual = get_canonical_times(
            conn,
            START_LOCAL,
            END_LOCAL,
        )

        if not actual.equals(expected):
            raise ValueError(
                "Unexpected canonical coverage."
            )

        materialize_practice_table(conn)

    print(
        "Canonical rows added:",
        0 if incoming_df is None else len(incoming_df),
    )
    print(
        "Practice table:",
        f"{PRACTICE_SCHEMA}.{PRACTICE_TABLE}",
    )


if __name__ == "__main__":
    main()
