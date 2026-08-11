"""Refresh the subnational response-monitoring mirror (targeted / prioritized / reached).

Pulls the GHO monitoring dashboard's `disaggregated_caseloads` table and writes
it as a dated snapshot into `hpc.monitoring_admin`, plus the per-plan reporting
vintage into `hpc.monitoring_periods`.

This is the only public source for subnational **reached**: the HPC API returns
no measurements and HAPI's 2026 rows stop at admin-0. See src/gho_monitoring.py
for how the endpoint is reached and why a schema change must abort the run.

    uv run python scripts/refresh_monitoring.py
    uv run python scripts/refresh_monitoring.py --intersectoral-only --dry-run
"""

import argparse
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from src import gho_monitoring, storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NUMERIC = ["in_need", "targeted", "prioritized_target", "reached",
           "prioritized_reached"]


def attach_iso3(df):
    """Join ISO3 from the plan mirror — the dashboard only carries plan ids.

    Regional plans span several countries and have no single ISO3; their rows
    keep a null and are identified by plan_id.
    """
    # write=True even though this only reads: the refresh workflow carries the
    # WRITE credentials alone (the read pair is only in the deploy job), so a
    # read engine here connects as user "None" and the job dies on auth. Every
    # other refresh script touches the DB solely through the writer for the same
    # reason. The writer can read.
    plans = pd.read_sql("SELECT plan_id, iso3 FROM hpc.plans",
                        storage.get_engine(write=True))
    plans = plans[~plans["iso3"].fillna("").str.contains(";")]
    df = df.merge(plans, on="plan_id", how="left")
    missing = sorted(df.loc[df["iso3"].isna(), "country"].dropna().unique())
    if missing:
        logger.warning("No single-country plan match (regional or unmirrored plan): %s",
                       ", ".join(missing))
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--intersectoral-only", action="store_true",
                        help="keep only the HNRP (intersectoral) rows")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and summarise, write nothing")
    parser.add_argument("--date", help="snapshot date (default today, UTC)")
    args = parser.parse_args()

    # UTC, so a snapshot taken by the 04:17 UTC job and one taken locally in the
    # evening never land on two different dates for the same refresh.
    snapshot = (date.fromisoformat(args.date) if args.date
                else datetime.now(timezone.utc).date())

    rows = gho_monitoring.fetch_all(intersectoral_only=args.intersectoral_only)
    if not rows:
        raise SystemExit("No monitoring rows returned — refusing to write an empty snapshot")
    df = pd.DataFrame(rows)
    for c in NUMERIC + ["admin_level", "ic_severity", "plan_id", "year"]:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = attach_iso3(df)

    inter = df[df["cluster_name"] == gho_monitoring.INTERSECTORAL_CLUSTER]
    logger.info("Fetched %s rows (%s intersectoral) across %s plans, %s areas; "
                "dashboard model last refreshed %s",
                len(df), len(inter), df["plan_id"].nunique(), df["pcode"].nunique(),
                gho_monitoring.dataset_refreshed_at())
    totals = inter[NUMERIC].sum()
    logger.info("Intersectoral totals: " + " · ".join(
        f"{c}={int(totals[c]):,}" for c in NUMERIC))
    nat_rows = gho_monitoring.fetch_national(
        intersectoral_only=args.intersectoral_only)
    if not nat_rows:
        raise SystemExit("No national rows returned — refusing to write an empty snapshot")
    nat = pd.DataFrame(nat_rows)
    for c in NUMERIC + ["plan_id", "year"]:
        if c in nat:
            nat[c] = pd.to_numeric(nat[c], errors="coerce")
    nat = attach_iso3(nat)

    # The subnational rows sum BELOW these national figures, and that is a
    # property of the source rather than a broken join: partners report a lot of
    # delivery with no location attached, and some countries attribute none of a
    # measure to any area at all. Log the size of the gap per plan so a NEW gap
    # is distinguishable from the standing ones.
    nat_inter = nat[nat["cluster_name"] == gho_monitoring.INTERSECTORAL_CLUSTER]
    cmp = (inter.groupby("plan_id")[NUMERIC].sum()
           .join(nat_inter.set_index("plan_id")[NUMERIC], rsuffix="_national",
                 how="inner"))
    who = nat_inter.set_index("plan_id")["iso3"].fillna("?").to_dict()
    for measure in ("in_need", "targeted"):
        national = cmp[f"{measure}_national"].replace(0, pd.NA)
        share = (cmp[measure] / national).dropna()
        low = share[share < 0.9].sort_values()
        if len(low):
            logger.info("%s: %s of %s plan(s) attribute <90%% of the national "
                        "figure to areas — %s", measure, len(low), len(share),
                        ", ".join(f"{who.get(p, p)}={s:.0%}" for p, s in low.items()))

    periods = pd.DataFrame(gho_monitoring.fetch_periods())
    logger.info("Monitoring vintages for %s plans", len(periods))

    if args.dry_run:
        logger.info("Dry run — nothing written")
        return

    storage.ensure_tables()
    storage.write_monitoring(df, snapshot)
    storage.write_monitoring_national(nat, snapshot)
    storage.write_monitoring_periods(periods, snapshot)


if __name__ == "__main__":
    main()
