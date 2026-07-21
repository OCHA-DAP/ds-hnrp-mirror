"""Refresh the JIAF intersectoral-severity mirror from per-country HDX workbooks."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src import jiaf, storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    storage.ensure_tables()
    df, skipped = jiaf.fetch_all_severity()
    if df.empty:
        raise RuntimeError("No severity rows parsed from any JIAF workbook")
    storage.replace_severity(df)
    logger.info(
        "Done: %s rows, %s country-years, severity 4+ rows: %s",
        len(df),
        df.groupby(["iso3", "year"]).ngroups,
        (df["final_severity"] >= 4).sum(),
    )
    if skipped:
        logger.warning("Skipped (unparseable/no data): %s", ", ".join(skipped))


if __name__ == "__main__":
    main()
