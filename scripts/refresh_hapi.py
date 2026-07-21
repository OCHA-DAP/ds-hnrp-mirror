"""Refresh the admin-level PiN mirror from HDX HAPI (full replace)."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import pandas as pd  # noqa: E402

from src import hapi, storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MIN_EXPECTED_ROWS = 100_000  # guard: never wipe the mirror for a bad/partial pull


def main():
    storage.ensure_tables()
    rows = hapi.fetch_humanitarian_needs()
    df = pd.DataFrame(rows)
    df["reference_period_start"] = pd.to_datetime(df["reference_period_start"]).dt.date
    df["reference_period_end"] = pd.to_datetime(df["reference_period_end"]).dt.date
    if len(df) < MIN_EXPECTED_ROWS:
        raise RuntimeError(
            f"HAPI returned only {len(df)} rows (expected >{MIN_EXPECTED_ROWS}); "
            "refusing to replace the mirror"
        )
    storage.replace_needs(df)
    logger.info("Done: %s rows", len(df))


if __name__ == "__main__":
    main()
