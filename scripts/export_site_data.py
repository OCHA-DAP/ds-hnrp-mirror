"""Export DB contents to static JSON for the GitHub Pages explorer.

Writes site/data/plans.json, site/data/needs_index.json, and one compact
site/data/needs/{ISO3}_{YEAR}.json per country-year (arrays, not objects,
to keep payloads small).
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import pandas as pd  # noqa: E402

from src import storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SITE_DATA = Path(__file__).parent.parent / "site" / "data"

NEEDS_ROW_COLS = [
    "admin_level", "admin1_code", "admin1_name", "admin2_code", "admin2_name",
    "admin3_code", "admin3_name",
    "sector_code", "sector_name", "category", "population_status", "population",
]
SEVERITY_ROW_COLS = [
    "admin1_code", "admin1_name", "admin2_code", "admin2_name",
    "admin3_code", "admin3_name", "population_group", "population",
    "final_severity",
]
PIN_ROW_COLS = [
    "admin1_code", "admin1_name", "admin2_code", "admin2_name",
    "admin3_code", "admin3_name", "population_group", "population",
    "severity", "preliminary_pin", "final_pin",
]


def _clean(v):
    return None if pd.isna(v) else (int(v) if isinstance(v, float) and v == int(v) else v)


def export_plans(generated_at):
    plans, caseloads = storage.read_plans()
    by_plan = {
        pid: g.drop(columns=["plan_id", "refreshed_at"]).to_dict("records")
        for pid, g in caseloads.groupby("plan_id")
    }
    records = []
    for _, p in plans.iterrows():
        rec = {k: _clean(v) for k, v in p.drop("refreshed_at").items()}
        for c in ("start_date", "end_date", "released_date", "source_updated_at"):
            rec[c] = str(rec[c]) if rec[c] is not None else None
        rec["caseloads"] = [
            {k: _clean(v) for k, v in cl.items()} for cl in by_plan.get(p["plan_id"], [])
        ]
        records.append(rec)
    out = {"generated_at": generated_at, "plans": records}
    (SITE_DATA / "plans.json").write_text(json.dumps(out))
    logger.info("plans.json: %s plans", len(records))


def export_needs(generated_at):
    needs = storage.read_needs()
    if needs.empty:
        logger.warning("needs_admin is empty; skipping needs export")
        return
    needs["year"] = pd.to_datetime(needs["reference_period_start"]).dt.year
    (SITE_DATA / "needs").mkdir(parents=True, exist_ok=True)
    index = []
    for iso3, g_iso in sorted(needs.groupby("location_code")):
        years = sorted(g_iso["year"].unique().tolist())
        index.append(
            {"iso3": iso3, "name": g_iso["location_name"].iloc[0], "years": years}
        )
        for year, g in g_iso.groupby("year"):
            rows = [
                [_clean(v) for v in row]
                for row in g[NEEDS_ROW_COLS].itertuples(index=False, name=None)
            ]
            payload = {
                "generated_at": generated_at,
                "iso3": iso3,
                "year": int(year),
                "columns": NEEDS_ROW_COLS,
                "rows": rows,
            }
            (SITE_DATA / "needs" / f"{iso3}_{year}.json").write_text(json.dumps(payload))
    (SITE_DATA / "needs_index.json").write_text(
        json.dumps({"generated_at": generated_at, "countries": index})
    )
    logger.info("needs export: %s countries, %s rows", len(index), len(needs))


def export_severity(generated_at):
    sev = storage.read_severity()
    if sev.empty:
        logger.warning("severity_admin is empty; skipping severity export")
        return
    (SITE_DATA / "severity").mkdir(parents=True, exist_ok=True)
    index = []
    for iso3, g_iso in sorted(sev.groupby("iso3")):
        index.append({"iso3": iso3, "years": sorted(int(y) for y in g_iso["year"].unique())})
        for year, g in g_iso.groupby("year"):
            rows = [
                [_clean(v) for v in row]
                for row in g[SEVERITY_ROW_COLS].itertuples(index=False, name=None)
            ]
            payload = {
                "generated_at": generated_at,
                "iso3": iso3,
                "year": int(year),
                "columns": SEVERITY_ROW_COLS,
                "rows": rows,
            }
            (SITE_DATA / "severity" / f"{iso3}_{year}.json").write_text(json.dumps(payload))
    (SITE_DATA / "severity_index.json").write_text(
        json.dumps({"generated_at": generated_at, "countries": index})
    )
    logger.info("severity export: %s countries, %s rows", len(index), len(sev))


def export_pin(generated_at):
    pin = storage.read_pin()
    if pin.empty:
        logger.warning("pin_admin is empty; skipping pin export")
        return
    (SITE_DATA / "pin").mkdir(parents=True, exist_ok=True)
    index = []
    for iso3, g_iso in sorted(pin.groupby("iso3")):
        index.append({"iso3": iso3, "years": sorted(int(y) for y in g_iso["year"].unique())})
        for year, g in g_iso.groupby("year"):
            rows = [
                [_clean(v) for v in row]
                for row in g[PIN_ROW_COLS].itertuples(index=False, name=None)
            ]
            payload = {
                "generated_at": generated_at,
                "iso3": iso3,
                "year": int(year),
                "columns": PIN_ROW_COLS,
                "rows": rows,
            }
            (SITE_DATA / "pin" / f"{iso3}_{year}.json").write_text(json.dumps(payload))
    (SITE_DATA / "pin_index.json").write_text(
        json.dumps({"generated_at": generated_at, "countries": index})
    )
    logger.info("pin export: %s countries, %s rows", len(index), len(pin))


def main():
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    export_plans(generated_at)
    export_needs(generated_at)
    export_severity(generated_at)
    export_pin(generated_at)


if __name__ == "__main__":
    main()
