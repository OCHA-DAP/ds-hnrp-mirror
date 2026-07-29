"""Per-country JIAF workbooks on HDX — intersectoral severity (1–5) and PiN.

Severity and admin-level overall PiN are published ONLY in per-country
'<iso3>-jiaf-humanitarian-needs-*' datasets (country-office orgs). The JIAF
2.0 template is standardized but country offices localize it (EN/FR/ES
headers, renamed sheets, extra admin-3 columns), so parsing is anchor-based:
find a sheet whose name matches the product, find the header row containing
admin-1 and admin-2 columns, then map columns by normalized (lowercased,
accent-stripped) names. Files that still don't parse are logged and reported,
never silently dropped.

Two products per workbook:
- severity: 'WS - 3.2 Intersectoral Severity' / 'Severity' — final severity
  (1–5) per admin area × population group.
- pin: 'WS - 3.1 Overall PiN' / 'PiN' — preliminary/final intersectoral PiN
  per admin area × population group. The 2026-cycle template (post
  "Humanitarian Reset", which reintroduced the distribution of PiN by
  severity level) also carries the row's final severity, so final PiN grouped
  by that column IS the intersectoral PiN-by-severity distribution. 2025
  workbooks have no severity column in the PiN sheet — join to severity rows
  on (iso3, year, admin codes, population_group) instead.
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

# --- overall-PiN sheet (WS - 3.1) ---
# sheet: any 'PiN' sheet that isn't a chart/diagnostic/target pivot
PIN_SHEET_RE = re.compile(r"ws\s*-?\s*3\.?1|\bpin\b|people[_ ]?in[_ ]?need", re.I)
PIN_SHEET_EXCLUDE_RE = re.compile(
    r"visual|historic|trend|graph|corr|overlap|highest|cible|gravit", re.I
)
# pin columns: a PiN word AND a finality/preliminary word, not target/prioritized
PIN_WORD = re.compile(r"\bpin\b|\binneed\b|\bin need\b|people in need")
# 'definiti' (not 'definitiv'): 'PiN définitif' is masculine, no v;
# 'joint' = UKR ('Overall PiN Joint'), 'total' = SLV ('PiN total'),
# 'global' = COD FINAL-PIN-SEV fallback ('PIN-Global')
PIN_FINAL_WORD = re.compile(
    r"final|definiti|intersec|inter sector|inter cluster|joint|\btotal\b|\bglobal\b"
)
PIN_FINAL_EXACT = {"pin", "inneed", "in need"}  # YEM 2025 HXL '#inneed'
PIN_PRELIM_WORD = re.compile(r"prelim")
PIN_EXCLUDE_WORD = re.compile(r"target|cible|prior|percent")
# exact severity headers seen in PiN sheets (2026 template and variants)
PIN_SEV_EXACT = {"sev", "severity", "severite", "severidad"}
PIN_SEV_WORD = re.compile(r"\bsev\b|sever")  # UKR: 'Overall Sev Intersectoral'


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
        code = r"p ?code|codigo|\bcode\b"  # \bcode\b: YEM 2025 HXL '#adm1 +code'
        idx[f"admin{lvl}_code"] = find(
            lambda c, p=pat, k=code: re.match(p, c) and re.search(k, c)
        )
        idx[f"admin{lvl}_name"] = find(
            lambda c, p=pat, k=code: re.match(p, c) and not re.search(k, c)
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


def _map_pin_columns(cells):
    """Column indices for a PiN sheet header, or Nones where not found."""
    idx = _map_columns(cells)

    def find_from_right(pred):
        for j in range(len(cells) - 1, -1, -1):
            if pred(cells[j]):
                return j
        return None

    idx["final_pin"] = find_from_right(
        lambda c: PIN_WORD.search(c)
        and (PIN_FINAL_WORD.search(c) or c in PIN_FINAL_EXACT)
        and not PIN_EXCLUDE_WORD.search(c)
        and not PIN_PRELIM_WORD.search(c)
    )
    idx["preliminary_pin"] = find_from_right(
        lambda c: PIN_WORD.search(c)
        and PIN_PRELIM_WORD.search(c)
        and not PIN_EXCLUDE_WORD.search(c)
    )
    # 2026-template fallback: final PiN sits right of preliminary but under a
    # free-text header (AFG: 'PiN\n(No Boundaries, >= targets')
    j = idx["preliminary_pin"]
    if idx["final_pin"] is None and j is not None and j + 1 < len(cells):
        if PIN_WORD.search(cells[j + 1]) and not PIN_PRELIM_WORD.search(cells[j + 1]):
            idx["final_pin"] = j + 1
    # row severity: exact header (2026 template), else severity+finality words
    # ('Inter-sector Severity', 'Overall Sev Intersectoral', ...)
    idx["severity"] = next((j for j, c in enumerate(cells) if c in PIN_SEV_EXACT), None)
    if idx["severity"] is None:
        idx["severity"] = find_from_right(
            lambda c: PIN_SEV_WORD.search(c)
            and FINAL_WORD.search(c)
            and not EXCLUDE_WORD.search(c)
        )
    return idx


def parse_pin_xlsx(content, iso3, year):
    """Rows of admin-level overall PiN from one workbook; [] if unparseable."""
    try:
        xl = pd.ExcelFile(io.BytesIO(content))
    except Exception:
        logger.warning("%s %s: not a readable xlsx", iso3, year)
        return []
    sheets = [
        s
        for s in xl.sheet_names
        if PIN_SHEET_RE.search(s.strip()) and not PIN_SHEET_EXCLUDE_RE.search(s)
    ]
    for sheet in sheets:
        raw = xl.parse(sheet, header=None, dtype=object)
        hi = _find_header(raw)
        if hi is None:
            continue
        cells = [_norm(c) for c in raw.iloc[hi]]
        idx = _map_pin_columns(cells)
        if idx["final_pin"] is None and idx["preliminary_pin"] is None:
            logger.warning("%s %s [%s]: no PiN column found", iso3, year, sheet)
            continue
        if idx["admin1_name"] is None and idx["admin1_code"] is None:
            logger.warning("%s %s [%s]: no admin columns found", iso3, year, sheet)
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
            if get("admin1_name") is None and get("admin1_code") is None:
                continue
            if get("admin1_code") is None and re.match(
                r"^(grand )?total", _norm(get("admin1_name"))
            ):
                continue
            final = pd.to_numeric(get("final_pin"), errors="coerce")
            prelim = pd.to_numeric(get("preliminary_pin"), errors="coerce")
            if pd.isna(final) and pd.isna(prelim):
                continue
            sev = pd.to_numeric(get("severity"), errors="coerce")
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
                    "population": None if pd.isna(pop) else int(round(pop)),
                    "severity": int(sev) if not pd.isna(sev) and 1 <= sev <= 5 else None,
                    "preliminary_pin": None if pd.isna(prelim) else int(round(prelim)),
                    "final_pin": None if pd.isna(final) else int(round(final)),
                }
            )
        if rows:
            return rows
        logger.warning("%s %s [%s]: header found but no data rows", iso3, year, sheet)
    return []


def attach_final_severity(pin_df, sev_df):
    """Join WS-3.2 final severity onto PiN rows as `final_severity`.

    The PiN sheet's own severity column is only a lookup of WS-3.2 and
    country offices break it (SSD 2026 pastes a constant; LBN 2026 leaves
    pcodes blank, collapsing the template's ID key so every row inherits the
    first unit's severity). WS-3.2 is authoritative, so PBS should group
    final_pin by COALESCE(final_severity, severity).

    Key: (iso3, year, population_group, deepest admin code — falling back to
    deepest admin name). Rows that miss retry without the population group
    (2025 workbooks often classify severity at area level only); area keys
    with conflicting severities are treated as ambiguous and skipped.
    """
    if pin_df.empty or sev_df.empty:
        pin_df["final_severity"] = pd.NA
        return pin_df

    def keys(df):
        adm = df[["admin3_code", "admin2_code", "admin1_code"]].bfill(axis=1).iloc[:, 0]
        name = df[["admin3_name", "admin2_name", "admin1_name"]].bfill(axis=1).iloc[:, 0]
        adm = adm.where(adm.notna(), name).map(lambda v: _norm(v) if pd.notna(v) else "")
        pg = df["population_group"].map(lambda v: _norm(v) if pd.notna(v) else "")
        base = df["iso3"] + "|" + df["year"].astype(str) + "|" + adm
        return base + "|" + pg, base

    sev_k1, sev_k2 = keys(sev_df)
    sev = sev_df.assign(_k1=sev_k1, _k2=sev_k2)
    unit_map = sev.groupby("_k1")["final_severity"].agg(
        lambda s: s.iloc[0] if s.nunique() == 1 else None
    )
    area_map = sev.groupby("_k2")["final_severity"].agg(
        lambda s: s.iloc[0] if s.nunique() == 1 else None
    )
    pin_k1, pin_k2 = keys(pin_df)
    joined = pin_k1.map(unit_map)
    joined = joined.where(joined.notna(), pin_k2.map(area_map))
    pin_df = pin_df.copy()
    pin_df["final_severity"] = joined.astype("Int64")
    return pin_df


def _year_resources(ds):
    """(year, resource) pairs for a dataset's XLSX resources, newest first.

    Newest-first means revised re-uploads ('[Revised] DRC_..._2026.xlsx')
    supersede the original workbook for the same year.
    """
    out = []
    for res in ds.get("resources", []):
        if res.get("format") != "XLSX":
            continue
        m = re.search(r"(20\d\d)", res.get("name", ""))
        if m:
            out.append((int(m.group(1)), res))
    out.sort(key=lambda t: t[1].get("last_modified") or "", reverse=True)
    return out


def fetch_all():
    """Parse every discoverable JIAF workbook for severity and overall PiN.

    Returns (severity_df, pin_df, skipped) where skipped maps product ->
    sorted 'ISO3 year' strings that had workbooks but no parseable sheet.
    Per (iso3, year, product) the newest resource that parses wins — some
    datasets ship several workbooks per year (country-custom exports, revised
    re-uploads, reprioritization annexes).
    """
    sev_rows, pin_rows = [], []
    done = {"severity": set(), "pin": set()}
    tried = set()
    for iso3, ds in sorted(discover_datasets()):
        for year, res in _year_resources(ds):
            key = (iso3, year)
            tried.add(key)
            if key in done["severity"] and key in done["pin"]:
                continue
            try:
                r = requests.get(res["url"], timeout=300)
                r.raise_for_status()
                content = r.content
            except Exception:
                logger.exception("%s %s: fetch failed (%s)", iso3, year, res["name"])
                continue
            for product, parse, rows_out in (
                ("severity", parse_severity_xlsx, sev_rows),
                ("pin", parse_pin_xlsx, pin_rows),
            ):
                if key in done[product]:
                    continue
                try:
                    rows = parse(content, iso3, year)
                except Exception:
                    logger.exception("%s %s: %s parse failed", iso3, year, product)
                    rows = []
                if rows:
                    rows_out.extend(rows)
                    done[product].add(key)
                    logger.info("%s %s: %s %s rows", iso3, year, len(rows), product)
    skipped = {
        product: sorted(f"{i} {y}" for i, y in tried - keys)
        for product, keys in done.items()
    }
    return pd.DataFrame(sev_rows), pd.DataFrame(pin_rows), skipped
