"""Global HPC HNO CSVs on HDX — used ONLY for admin-3 rows.

The global-hpc-hno dataset is the upstream of HAPI's humanitarian-needs but
carries admin-3 detail (BFA, COD, MMR) that HAPI truncates to admin-2. It lags
HAPI within the current plan cycle (2026 was national-only while HAPI already
had AFG admin-2), so HAPI stays the primary source and this contributes the
admin_level=3 rows HAPI can never have.
"""

import io
import logging
import re

import pandas as pd
import requests

logger = logging.getLogger(__name__)

DATASET = "global-hpc-hno"
HDX = "https://data.humdata.org/api/3/action/package_show"
STATUS_COLS = {
    "In Need": "INN",
    "Targeted": "TGT",
    "Affected": "AFF",
    "Reached": "REA",
}


def _resources():
    r = requests.get(HDX, params={"id": DATASET}, timeout=60)
    r.raise_for_status()
    out = []
    for res in r.json()["result"]["resources"]:
        m = re.search(r"(20\d\d)", res["name"])
        if m and res["format"] == "CSV":
            out.append((int(m.group(1)), res["id"], res["url"]))
    return out


def fetch_adm3_needs():
    """Long-format admin-3 rows across all Global HNO years, HAPI-compatible."""
    frames = []
    for year, res_id, url in _resources():
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        # row 2 is the HXL tag line — drop it
        df = pd.read_csv(io.StringIO(r.text), skiprows=[1], dtype=str)
        if "Admin 3 PCode" not in df.columns:
            logger.info("GHNO %s: no admin-3 columns, skipping", year)
            continue
        adm3 = df[df["Admin 3 PCode"].notna() & (df["Admin 3 PCode"].str.strip() != "")]
        if adm3.empty:
            logger.info("GHNO %s: no admin-3 rows", year)
            continue
        for col, status in STATUS_COLS.items():
            sub = adm3[adm3[col].notna()].copy()
            if sub.empty:
                continue
            frames.append(
                pd.DataFrame(
                    {
                        "location_code": sub["Country ISO3"],
                        "location_name": sub["Country ISO3"],
                        "admin1_code": sub["Admin 1 PCode"],
                        "admin1_name": sub["Admin 1 Name"],
                        "admin2_code": sub["Admin 2 PCode"],
                        "admin2_name": sub["Admin 2 Name"],
                        "admin3_code": sub["Admin 3 PCode"],
                        "admin3_name": sub["Admin 3 Name"],
                        "admin_level": 3,
                        "sector_code": sub["Cluster"],
                        "sector_name": sub["Description"],
                        "category": sub["Category"].fillna(""),
                        "population_status": status,
                        "population": pd.to_numeric(sub[col], errors="coerce"),
                        "reference_period_start": f"{year}-01-01",
                        "reference_period_end": f"{year}-12-31",
                        "resource_hdx_id": res_id,
                    }
                )
            )
        logger.info("GHNO %s: %s admin-3 source rows", year, len(adm3))
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True).dropna(subset=["population"])
    out["population"] = out["population"].astype("int64")
    return out
