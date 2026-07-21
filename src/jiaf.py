"""Per-country JIAF workbooks on HDX — intersectoral severity (1–5).

Severity is published ONLY in per-country '<iso3>-jiaf-humanitarian-needs-*'
datasets (country-office orgs). The JIAF 2.0 template is standardized but
country offices localize it (EN/FR/ES headers, renamed sheets, extra admin-3
columns), so parsing is anchor-based: find a sheet whose name mentions
severity, find the header row containing admin-1 and admin-2 columns, then map
columns by normalized (lowercased, accent-stripped) names. Files that still
don't parse are logged and reported, never silently dropped.
"""

import io
import logging
import re
import unicodedata

import pandas as pd
import requests

logger = logging.getLogger(__name__)

HDX_SEARCH = "https://data.humdata.org/api/3/action/package_search"
NAME_RE = re.compile(r"^([a-z]{3})[-_]jiaf[-_]humanitarian[-_]needs")
SHEET_RE = re.compile(r"ws\s*-?\s*3\.?2|severit|severid", re.I)
SHEET_EXCLUDE_RE = re.compile(r"pin & sev|graph|final loval", re.I)
# final-severity column: needs a severity word AND a finality word, not preliminary
SEV_WORD = re.compile(r"sever|séver")
FINAL_WORD = re.compile(r"final|definitiv|intersec|inter sector|inter cluster")
EXCLUDE_WORD = re.compile(r"prelim|preliminaire|media|maxima|mayor|2a|based")


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def discover_datasets():
    """All per-country JIAF needs+severity datasets, as (iso3, dataset dict)."""
    r = requests.get(
        HDX_SEARCH, params={"q": "jiaf humanitarian needs", "rows": 100}, timeout=60
    )
    r.raise_for_status()
    out = []
    for ds in r.json()["result"]["results"]:
        m = NAME_RE.match(ds["name"])
        if m and "1-1" not in ds["name"]:  # skip the one JIAF 1.1 legacy workbook
            out.append((m.group(1).upper(), ds))
    return out


def _find_header(raw):
    for i in range(min(10, len(raw))):
        cells = [_norm(c) for c in raw.iloc[i]]
        has1 = any(re.match(r"^(admin ?1|adm1)", c) for c in cells)
        has2 = any(re.match(r"^(admin ?2|adm2)", c) for c in cells)
        if has1 and has2:
            return i
    return None


def _map_columns(cells):
    idx = {}

    def find(pred, exclude=None):
        for j, c in enumerate(cells):
            if pred(c) and not (exclude and exclude(c)):
                return j
        return None

    for lvl in (1, 2, 3):
        pat = rf"^(admin ?{lvl}|adm{lvl})"
        idx[f"admin{lvl}_code"] = find(
            lambda c, p=pat: re.match(p, c) and re.search(r"p ?code|codigo", c)
        )
        idx[f"admin{lvl}_name"] = find(
            lambda c, p=pat: re.match(p, c) and not re.search(r"p ?code|codigo", c)
        )
    idx["population"] = find(lambda c: c in ("population", "poblacion", "populaton"))
    idx["population_group"] = find(
        lambda c: c in ("population group", "groupe de population", "grupo de poblacion")
    )
    # search from the right: final results sit at the end, after sectoral columns
    for j in range(len(cells) - 1, -1, -1):
        c = cells[j]
        if SEV_WORD.search(c) and FINAL_WORD.search(c) and not EXCLUDE_WORD.search(c):
            idx["final_severity"] = j
            break
    else:
        idx["final_severity"] = None
    return idx


def parse_severity_xlsx(content, iso3, year):
    """Rows of admin-level final severity from one workbook; [] if unparseable."""
    try:
        xl = pd.ExcelFile(io.BytesIO(content))
    except Exception:
        logger.warning("%s %s: not a readable xlsx", iso3, year)
        return []
    sheets = [
        s for s in xl.sheet_names if SHEET_RE.search(s) and not SHEET_EXCLUDE_RE.search(s)
    ]
    for sheet in sheets:
        raw = xl.parse(sheet, header=None, dtype=object)
        hi = _find_header(raw)
        if hi is None:
            continue
        cells = [_norm(c) for c in raw.iloc[hi]]
        idx = _map_columns(cells)
        if idx["final_severity"] is None or (
            idx["admin1_name"] is None and idx["admin1_code"] is None
        ):
            logger.warning("%s %s [%s]: key columns not found", iso3, year, sheet)
            continue
        rows = []
        for _, r in raw.iloc[hi + 1 :].iterrows():
            def get(key):
                j = idx[key]
                v = r.iloc[j] if j is not None else None
                if v is None or pd.isna(v):
                    return None
                v = str(v).strip()
                return v or None

            if (get("admin1_name") or "").startswith("#"):  # HXL tag row
                continue
            sev = pd.to_numeric(get("final_severity"), errors="coerce")
            if pd.isna(sev) or not 1 <= sev <= 5:
                continue
            if get("admin1_name") is None and get("admin1_code") is None:
                continue
            pop = pd.to_numeric(get("population"), errors="coerce")
            rows.append(
                {
                    "iso3": iso3,
                    "year": year,
                    "admin1_code": get("admin1_code"),
                    "admin1_name": get("admin1_name"),
                    "admin2_code": get("admin2_code"),
                    "admin2_name": get("admin2_name"),
                    "admin3_code": get("admin3_code"),
                    "admin3_name": get("admin3_name"),
                    "population_group": get("population_group"),
                    "population": None if pd.isna(pop) else int(pop),
                    "final_severity": int(sev),
                }
            )
        if rows:
            return rows
        logger.warning("%s %s [%s]: header found but no data rows", iso3, year, sheet)
    return []


def fetch_all_severity():
    """Parse every discoverable JIAF workbook; returns (DataFrame, skipped list).

    First successful resource per (iso3, year) wins — some datasets ship both a
    country-custom export and the standard JIAF workbook for the same year.
    """
    all_rows, skipped, done = [], [], set()
    for iso3, ds in sorted(discover_datasets()):
        for res in ds.get("resources", []):
            if res.get("format") != "XLSX":
                continue
            m = re.search(r"(20\d\d)", res["name"])
            if not m:
                continue
            year = int(m.group(1))
            if (iso3, year) in done:
                continue
            try:
                r = requests.get(res["url"], timeout=300)
                r.raise_for_status()
                rows = parse_severity_xlsx(r.content, iso3, year)
            except Exception:
                logger.exception("%s %s: fetch/parse failed", iso3, year)
                rows = []
            if rows:
                all_rows.extend(rows)
                done.add((iso3, year))
                logger.info("%s %s: %s severity rows", iso3, year, len(rows))
    for iso3, ds in sorted(discover_datasets()):
        for res in ds.get("resources", []):
            m = re.search(r"(20\d\d)", res.get("name", ""))
            if res.get("format") == "XLSX" and m and (iso3, int(m.group(1))) not in done:
                skipped.append(f"{iso3} {m.group(1)}")
    return pd.DataFrame(all_rows), sorted(set(skipped))
