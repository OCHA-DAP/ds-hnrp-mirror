"""Refresh the HPC plan mirror (plans + cluster caseloads + FTS funding).

Default refreshes the current, previous, and next plan years (funding moves
continuously; needs revise within a cycle). Use --all for the full historical
backfill, or --years for a specific set.
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src import hpc, storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

FIRST_PLAN_YEAR = 2004


def target_years(args):
    if args.all:
        return list(range(FIRST_PLAN_YEAR, date.today().year + 2))
    if args.years:
        return [int(y) for y in args.years.split(",")]
    y = date.today().year
    return [y - 1, y, y + 1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="backfill all years")
    parser.add_argument("--years", help="comma-separated years, e.g. 2024,2025")
    args = parser.parse_args()

    storage.ensure_tables()
    for year in target_years(args):
        plans = hpc.list_plans(year)
        logger.info("%s: %s plans", year, len(plans))
        plan_rows, caseload_rows, plan_ids = [], [], []
        for p in plans:
            plan_id = p["id"]
            try:
                detail = hpc.fetch_plan_detail(plan_id)
                funding = hpc.fetch_funding_total(plan_id)
                row, caseloads = hpc.parse_plan(detail, year, funding)
            except Exception:
                logger.exception("Failed to fetch/parse plan %s (%s)", plan_id, year)
                continue
            plan_rows.append(row)
            caseload_rows.extend(caseloads)
            plan_ids.append(plan_id)
        storage.upsert_plans(plan_rows)
        storage.replace_caseloads(plan_ids, caseload_rows)
    logger.info("Done")


if __name__ == "__main__":
    main()
