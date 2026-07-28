"""Refresh the JIAF severity + overall-PiN mirrors from per-country HDX workbooks."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import pandas as pd  # noqa: E402

from src import jiaf, storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    storage.ensure_tables()
    sev_df, pin_df, skipped = jiaf.fetch_all()
    if sev_df.empty:
        raise RuntimeError("No severity rows parsed from any JIAF workbook")
    if pin_df.empty:
        raise RuntimeError("No PiN rows parsed from any JIAF workbook")
    storage.replace_severity(sev_df)
    storage.replace_pin(pin_df)
    logger.info(
        "Severity done: %s rows, %s country-years, severity 4+ rows: %s",
        len(sev_df),
        sev_df.groupby(["iso3", "year"]).ngroups,
        (sev_df["final_severity"] >= 4).sum(),
    )
    sev = pd.to_numeric(pin_df["severity"], errors="coerce")
    logger.info(
        "PiN done: %s rows, %s country-years, rows with own severity: %s "
        "(2025 sheets carry no severity column - join severity_admin), "
        "final PiN in severity 4+ areas: %s",
        len(pin_df),
        pin_df.groupby(["iso3", "year"]).ngroups,
        int(sev.notna().sum()),
        int(pin_df.loc[sev >= 4, "final_pin"].fillna(0).sum()),
    )
    for product, missing in skipped.items():
        if missing:
            logger.warning("Skipped %s (unparseable/no data): %s", product, ", ".join(missing))


if __name__ == "__main__":
    main()
