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
        adm3 = derive_adm3_parents(adm3, df)
        df = pd.concat([df, adm3], ignore_index=True)
    storage.replace_needs(df)
    logger.info("Done: %s rows (%s admin-3)", len(df), len(adm3))


def derive_adm3_parents(adm3, hapi_df):
    """Fill the blank admin-1/2 parent columns on Global-HNO adm3 rows.

    The CSV leaves parents empty, and for the adm3-only countries (BFA, COD,
    ETH, SYR) HAPI has no subnational rows to borrow codes from either — so the
    parent universe is public.polygon (the team COD-AB reference, prod DB),
    supplemented by any codes HAPI does have. Pcodes are hierarchical (adm3 =
    adm2 code + suffix), so parents are the longest-prefix match.
    """
    import ocha_stratus as stratus

    poly = pd.read_sql(
        "SELECT pcode, name, adm_level FROM public.polygon WHERE adm_level IN (1,2)",
        stratus.get_engine(stage="prod"),
    )
    for lvl in (1, 2):
        code_col, name_col = f"admin{lvl}_code", f"admin{lvl}_name"
        parents = poly[poly.adm_level == lvl].set_index("pcode")["name"].to_dict()
        parents.update(
            hapi_df[hapi_df[code_col].notna()]
            .drop_duplicates(code_col)
            .set_index(code_col)[name_col]
            .to_dict()
        )
        codes_desc = sorted(parents, key=len, reverse=True)

        def derive(a3code):
            for code in codes_desc:
                if str(a3code).startswith(code):
                    return code
            return None

        blank = adm3[code_col].isna() | (adm3[code_col].astype(str).str.strip() == "")
        derived = adm3.loc[blank, "admin3_code"].map(derive)
        adm3.loc[blank, code_col] = derived
        adm3.loc[blank, name_col] = derived.map(parents)
        logger.info(
            "adm3 parent derivation: admin%s filled for %s/%s rows",
            lvl, int(derived.notna().sum()), int(blank.sum()),
        )
    return adm3


if __name__ == "__main__":
    main()
