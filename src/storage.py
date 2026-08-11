"""DB layer: hpc schema on the team Postgres via ocha-stratus.

Stage is selected with the STAGE env var (default "dev"). Writers need the
*_UID_WRITE / *_PW_WRITE credentials; PGSSLMODE=require is enforced here.
"""

import logging
import os
from datetime import datetime, timezone

os.environ.setdefault("PGSSLMODE", "require")

import ocha_stratus as stratus
import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)

SCHEMA = "hpc"
STAGE = os.environ.get("STAGE", "dev")

PLAN_COLS = [
    "plan_id", "code", "name", "short_name", "plan_type", "iso3", "year",
    "start_date", "end_date", "is_gho", "released_date", "source_updated_at",
    "orig_requirements", "revised_requirements", "funding_total",
    "total_population", "in_need", "targeted", "affected", "expected_reach",
    "reached",
]
CASELOAD_COLS = [
    "plan_id", "entity_id", "cluster_name", "requirements",
    "total_population", "in_need", "targeted", "affected", "expected_reach",
    "reached",
]
NEEDS_COLS = [
    "location_code", "location_name", "admin1_code", "admin1_name",
    "admin2_code", "admin2_name", "admin3_code", "admin3_name",
    "admin_level", "sector_code", "sector_name",
    "category", "population_status", "population",
    "reference_period_start", "reference_period_end", "resource_hdx_id",
]
SEVERITY_COLS = [
    "iso3", "year", "admin1_code", "admin1_name", "admin2_code", "admin2_name",
    "admin3_code", "admin3_name",
    "population_group", "population", "final_severity",
]
PIN_COLS = [
    "iso3", "year", "admin1_code", "admin1_name", "admin2_code", "admin2_name",
    "admin3_code", "admin3_name",
    "population_group", "population", "severity", "final_severity",
    "preliminary_pin", "final_pin",
]
MONITORING_COLS = [
    "snapshot_date", "plan_id", "iso3", "country", "year", "pcode",
    "location_path", "admin_level", "admin0_code", "admin1_code",
    "cluster_name", "ic_severity", "ic_severity_class",
    "in_need", "targeted", "prioritized_target", "reached", "prioritized_reached",
]
MONITORING_PERIOD_COLS = [
    "snapshot_date", "plan_id", "year", "country", "latest_update",
]
MONITORING_NATIONAL_COLS = [
    "snapshot_date", "plan_id", "iso3", "country", "year", "admin0_code",
    "cluster_name",
    "in_need", "targeted", "prioritized_target", "reached", "prioritized_reached",
]


def get_engine(write=False):
    return stratus.get_engine(stage=STAGE, write=write)


def ensure_tables():
    ddl = f"""
    CREATE SCHEMA IF NOT EXISTS {SCHEMA};
    CREATE TABLE IF NOT EXISTS {SCHEMA}.plans (
        plan_id integer PRIMARY KEY,
        code text,
        name text,
        short_name text,
        plan_type text,
        iso3 text,
        year integer,
        start_date date,
        end_date date,
        is_gho boolean,
        released_date timestamptz,
        source_updated_at timestamptz,
        orig_requirements bigint,
        revised_requirements bigint,
        funding_total bigint,
        total_population bigint,
        in_need bigint,
        targeted bigint,
        affected bigint,
        expected_reach bigint,
        reached bigint,
        refreshed_at timestamptz
    );
    CREATE TABLE IF NOT EXISTS {SCHEMA}.plan_caseloads (
        plan_id integer,
        entity_id integer,
        cluster_name text,
        requirements bigint,
        total_population bigint,
        in_need bigint,
        targeted bigint,
        affected bigint,
        expected_reach bigint,
        reached bigint,
        refreshed_at timestamptz,
        PRIMARY KEY (plan_id, entity_id)
    );
    CREATE TABLE IF NOT EXISTS {SCHEMA}.needs_admin (
        location_code text,
        location_name text,
        admin1_code text,
        admin1_name text,
        admin2_code text,
        admin2_name text,
        admin_level integer,
        sector_code text,
        sector_name text,
        category text,
        population_status text,
        population bigint,
        reference_period_start date,
        reference_period_end date,
        resource_hdx_id text,
        refreshed_at timestamptz
    );
    CREATE INDEX IF NOT EXISTS needs_admin_loc_idx
        ON {SCHEMA}.needs_admin (location_code, admin_level);
    ALTER TABLE {SCHEMA}.needs_admin ADD COLUMN IF NOT EXISTS admin3_code text;
    ALTER TABLE {SCHEMA}.needs_admin ADD COLUMN IF NOT EXISTS admin3_name text;
    CREATE TABLE IF NOT EXISTS {SCHEMA}.severity_admin (
        iso3 text,
        year integer,
        admin1_code text,
        admin1_name text,
        admin2_code text,
        admin2_name text,
        admin3_code text,
        admin3_name text,
        population_group text,
        population bigint,
        final_severity integer,
        refreshed_at timestamptz
    );
    ALTER TABLE {SCHEMA}.severity_admin ADD COLUMN IF NOT EXISTS admin3_code text;
    ALTER TABLE {SCHEMA}.severity_admin ADD COLUMN IF NOT EXISTS admin3_name text;
    CREATE INDEX IF NOT EXISTS severity_admin_loc_idx
        ON {SCHEMA}.severity_admin (iso3, year, final_severity);
    CREATE TABLE IF NOT EXISTS {SCHEMA}.pin_admin (
        iso3 text,
        year integer,
        admin1_code text,
        admin1_name text,
        admin2_code text,
        admin2_name text,
        admin3_code text,
        admin3_name text,
        population_group text,
        population bigint,
        severity integer,
        final_severity integer,
        preliminary_pin bigint,
        final_pin bigint,
        refreshed_at timestamptz
    );
    ALTER TABLE {SCHEMA}.pin_admin ADD COLUMN IF NOT EXISTS final_severity integer;
    CREATE INDEX IF NOT EXISTS pin_admin_loc_idx
        ON {SCHEMA}.pin_admin (iso3, year, severity);
    -- Response monitoring, from the GHO dashboard's semantic model.
    -- APPEND-ONLY, unlike every other table here: `reached` is cumulative and
    -- climbs through the plan year, and the dashboard only ever shows current
    -- state. Overwriting each refresh would throw away the only thing worth
    -- refreshing often for. One row per (snapshot, plan, area, cluster);
    -- re-running on the same day overwrites that day's rows and nothing else.
    CREATE TABLE IF NOT EXISTS {SCHEMA}.monitoring_admin (
        snapshot_date date,
        plan_id integer,
        iso3 text,
        country text,
        year integer,
        pcode text,
        location_path text,
        admin_level integer,
        admin0_code text,
        admin1_code text,
        cluster_name text,
        ic_severity integer,
        ic_severity_class text,
        in_need bigint,
        targeted bigint,
        prioritized_target bigint,
        reached bigint,
        prioritized_reached bigint,
        refreshed_at timestamptz,
        PRIMARY KEY (snapshot_date, plan_id, pcode, cluster_name)
    );
    CREATE INDEX IF NOT EXISTS monitoring_admin_latest_idx
        ON {SCHEMA}.monitoring_admin (iso3, year, cluster_name, snapshot_date DESC);
    -- The published national caseload, mirrored alongside the subnational rows
    -- rather than summed from them. The two do NOT agree and are not meant to:
    -- monitoring_admin is an attribution of this figure to areas, and the source
    -- leaves part of it unattributed in most countries. Any country total shown
    -- to a reader comes from here.
    CREATE TABLE IF NOT EXISTS {SCHEMA}.monitoring_national (
        snapshot_date date,
        plan_id integer,
        iso3 text,
        country text,
        year integer,
        admin0_code text,
        cluster_name text,
        in_need bigint,
        targeted bigint,
        prioritized_target bigint,
        reached bigint,
        prioritized_reached bigint,
        refreshed_at timestamptz,
        PRIMARY KEY (snapshot_date, plan_id, cluster_name)
    );
    CREATE INDEX IF NOT EXISTS monitoring_national_latest_idx
        ON {SCHEMA}.monitoring_national (iso3, year, cluster_name, snapshot_date DESC);
    CREATE TABLE IF NOT EXISTS {SCHEMA}.monitoring_periods (
        snapshot_date date,
        plan_id integer,
        year integer,
        country text,
        latest_update text,
        refreshed_at timestamptz,
        PRIMARY KEY (snapshot_date, plan_id)
    );
    """
    with get_engine(write=True).begin() as conn:
        conn.execute(text(ddl))


def upsert_plans(plan_rows):
    if not plan_rows:
        return
    now = datetime.now(timezone.utc)
    cols = PLAN_COLS + ["refreshed_at"]
    collist = ", ".join(cols)
    params = ", ".join(f":{c}" for c in cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "plan_id")
    sql = text(
        f"INSERT INTO {SCHEMA}.plans ({collist}) VALUES ({params}) "
        f"ON CONFLICT (plan_id) DO UPDATE SET {updates}"
    )
    with get_engine(write=True).begin() as conn:
        for row in plan_rows:
            conn.execute(sql, {**{c: row.get(c) for c in PLAN_COLS}, "refreshed_at": now})
    logger.info("Upserted %s plans", len(plan_rows))


def replace_caseloads(plan_ids, caseload_rows):
    """Replace cluster caseloads for the given plans (delete + insert)."""
    if not plan_ids:
        return
    now = datetime.now(timezone.utc)
    cols = CASELOAD_COLS + ["refreshed_at"]
    collist = ", ".join(cols)
    params = ", ".join(f":{c}" for c in cols)
    sql = text(f"INSERT INTO {SCHEMA}.plan_caseloads ({collist}) VALUES ({params})")
    with get_engine(write=True).begin() as conn:
        conn.execute(
            text(f"DELETE FROM {SCHEMA}.plan_caseloads WHERE plan_id = ANY(:ids)"),
            {"ids": list(plan_ids)},
        )
        for row in caseload_rows:
            conn.execute(sql, {**{c: row.get(c) for c in CASELOAD_COLS}, "refreshed_at": now})
    logger.info("Replaced caseloads for %s plans (%s rows)", len(plan_ids), len(caseload_rows))


def replace_needs(df):
    """Full transactional replace of the HAPI needs mirror (~1M rows)."""
    df = df[NEEDS_COLS].copy()
    df["refreshed_at"] = datetime.now(timezone.utc)
    engine = get_engine(write=True)
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {SCHEMA}.needs_admin"))
        df.to_sql(
            "needs_admin",
            conn,
            schema=SCHEMA,
            if_exists="append",
            index=False,
            chunksize=10_000,
            method="multi",
        )
    logger.info("Replaced needs_admin with %s rows", len(df))


def replace_severity(df):
    """Full transactional replace of the JIAF severity mirror."""
    df = df[SEVERITY_COLS].copy()
    df["refreshed_at"] = datetime.now(timezone.utc)
    with get_engine(write=True).begin() as conn:
        conn.execute(text(f"DELETE FROM {SCHEMA}.severity_admin"))
        df.to_sql(
            "severity_admin",
            conn,
            schema=SCHEMA,
            if_exists="append",
            index=False,
            chunksize=10_000,
            method="multi",
        )
    logger.info("Replaced severity_admin with %s rows", len(df))


def replace_pin(df):
    """Full transactional replace of the JIAF overall-PiN mirror."""
    df = df[PIN_COLS].copy()
    df["refreshed_at"] = datetime.now(timezone.utc)
    with get_engine(write=True).begin() as conn:
        conn.execute(text(f"DELETE FROM {SCHEMA}.pin_admin"))
        df.to_sql(
            "pin_admin",
            conn,
            schema=SCHEMA,
            if_exists="append",
            index=False,
            chunksize=10_000,
            method="multi",
        )
    logger.info("Replaced pin_admin with %s rows", len(df))


def _upsert_snapshot(table, cols, df, snapshot_date):
    """Insert one day's snapshot, replacing that same day if it already ran.

    Deliberately NOT a full replace: earlier snapshots are the time series.
    """
    df = df[[c for c in cols if c != "snapshot_date"]].copy()
    df["snapshot_date"] = snapshot_date
    df["refreshed_at"] = datetime.now(timezone.utc)
    with get_engine(write=True).begin() as conn:
        conn.execute(
            text(f"DELETE FROM {SCHEMA}.{table} WHERE snapshot_date = :d"),
            {"d": snapshot_date},
        )
        df.to_sql(table, conn, schema=SCHEMA, if_exists="append", index=False,
                  chunksize=10_000, method="multi")
    logger.info("Wrote %s rows to %s for %s", len(df), table, snapshot_date)


def write_monitoring(df, snapshot_date):
    _upsert_snapshot("monitoring_admin", MONITORING_COLS, df, snapshot_date)


def write_monitoring_periods(df, snapshot_date):
    _upsert_snapshot("monitoring_periods", MONITORING_PERIOD_COLS, df, snapshot_date)


def write_monitoring_national(df, snapshot_date):
    _upsert_snapshot("monitoring_national", MONITORING_NATIONAL_COLS, df, snapshot_date)


def read_monitoring_national(latest_only=True, cluster=None):
    """Published national caseloads; by default the most recent snapshot per plan."""
    where = "WHERE cluster_name = %(cluster)s" if cluster else ""
    if latest_only:
        sql = f"""
        SELECT m.* FROM {SCHEMA}.monitoring_national m
        JOIN (SELECT plan_id, max(snapshot_date) AS d
              FROM {SCHEMA}.monitoring_national GROUP BY plan_id) l
          ON l.plan_id = m.plan_id AND l.d = m.snapshot_date
        {where}
        """
    else:
        sql = f"SELECT * FROM {SCHEMA}.monitoring_national m {where}"
    return pd.read_sql(sql, get_engine(), params={"cluster": cluster} if cluster else None)


def read_monitoring(latest_only=True, cluster=None):
    """Monitoring rows; by default only the most recent snapshot per plan."""
    where = "WHERE cluster_name = %(cluster)s" if cluster else ""
    if latest_only:
        sql = f"""
        SELECT m.* FROM {SCHEMA}.monitoring_admin m
        JOIN (SELECT plan_id, max(snapshot_date) AS d
              FROM {SCHEMA}.monitoring_admin GROUP BY plan_id) l
          ON l.plan_id = m.plan_id AND l.d = m.snapshot_date
        {where}
        """
    else:
        sql = f"SELECT * FROM {SCHEMA}.monitoring_admin m {where}"
    return pd.read_sql(sql, get_engine(), params={"cluster": cluster} if cluster else None)


def read_severity():
    return pd.read_sql(f"SELECT * FROM {SCHEMA}.severity_admin", get_engine())


def read_pin():
    return pd.read_sql(f"SELECT * FROM {SCHEMA}.pin_admin", get_engine())


def read_plans():
    plans = pd.read_sql(
        f"SELECT * FROM {SCHEMA}.plans ORDER BY year DESC, name", get_engine()
    )
    caseloads = pd.read_sql(
        f"SELECT * FROM {SCHEMA}.plan_caseloads ORDER BY plan_id, cluster_name",
        get_engine(),
    )
    return plans, caseloads


def read_needs():
    return pd.read_sql(f"SELECT * FROM {SCHEMA}.needs_admin", get_engine())
