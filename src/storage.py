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
    "admin2_code", "admin2_name", "admin_level", "sector_code", "sector_name",
    "category", "population_status", "population",
    "reference_period_start", "reference_period_end", "resource_hdx_id",
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
