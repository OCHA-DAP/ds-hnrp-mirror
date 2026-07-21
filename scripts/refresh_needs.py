"""Refresh the admin-level PiN mirror: HAPI (admin 0-2, freshest) + Global HNO
CSV admin-3 rows (BFA/COD/MMR — detail HAPI truncates). Full replace."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import pandas as pd  # noqa: E402

from src import ghno, hapi, storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MIN_EXPECTED_ROWS = 100_000  # guard: never wipe the mirror for a bad/partial pull


def main():
    storage.ensure_tables()
    rows = hapi.fetch_humanitarian_needs()
    df = pd.DataFrame(rows)
    df["admin3_code"] = None
    df["admin3_name"] = None
    df["reference_period_start"] = pd.to_datetime(df["reference_period_start"]).dt.date
    df["reference_period_end"] = pd.to_datetime(df["reference_period_end"]).dt.date
    if len(df) < MIN_EXPECTED_ROWS:
        raise RuntimeError(
            f"HAPI returned only {len(df)} rows (expected >{MIN_EXPECTED_ROWS}); "
            "refusing to replace the mirror"
        )
    adm3 = ghno.fetch_adm3_needs()
    if not adm3.empty:
        df = pd.concat([df, adm3], ignore_index=True)
    storage.replace_needs(df)
    logger.info("Done: %s rows (%s admin-3)", len(df), len(adm3))


if __name__ == "__main__":
    main()
