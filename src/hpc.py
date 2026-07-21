"""Client for the public HPC API (api.hpc.tools): plans, caseloads, FTS funding.

No authentication required. Plan detail comes from the v1 measurements endpoint,
which carries the plan-level caseLoad attachment (PiN / target / population
totals) and per-cluster governing entities with their own caseLoad and cost
attachments. Funding totals come from the FTS flow endpoint grouped by plan.
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)

BASE = "https://api.hpc.tools"
REQUEST_DELAY_S = 0.2
# Metric labels vary slightly across plans/years; map on the stable `type` field
# first and fall back to the English name.
METRIC_MAP = {
    "totalPopulation": "total_population",
    "inNeed": "in_need",
    "target": "targeted",
    "affected": "affected",
    "expectedReach": "expected_reach",
    "reached": "reached",
    "total population": "total_population",
    "people in need": "in_need",
    "people targeted": "targeted",
    "people affected": "affected",
    "people reached (expected)": "expected_reach",
    "people reached": "reached",
}


def _get(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=120)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{r.status_code}", response=r)
            r.raise_for_status()
            time.sleep(REQUEST_DELAY_S)
            return r.json()
        except (requests.RequestException, ValueError):
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def list_plans(year):
    """All plans for a year (may be empty for years with no appeals)."""
    data = _get(f"{BASE}/v2/public/plan", params={"year": year})
    return data.get("data") or []


def fetch_plan_detail(plan_id):
    return _get(f"{BASE}/v1/public/plan/id/{plan_id}", params={"content": "measurements"})[
        "data"
    ]


def fetch_funding_total(plan_id):
    """Total reported funding (USD) against a plan from FTS; None if unavailable."""
    try:
        data = _get(f"{BASE}/v1/public/fts/flow", params={"planid": plan_id, "groupby": "plan"})
        return data["data"]["report3"]["fundingTotals"]["total"]
    except Exception:
        logger.warning("No FTS funding for plan %s", plan_id)
        return None


def _caseload_totals(attachment):
    """Map a caseLoad attachment's totals onto our metric column names."""
    out = {}
    value = attachment.get("attachmentVersion", {}).get("value", {})
    totals = value.get("metrics", {}).get("values", {}).get("totals") or []
    for t in totals:
        key = t.get("type") or ""
        name = (t.get("name") or {}).get("en") or ""
        col = METRIC_MAP.get(key) or METRIC_MAP.get(name.strip().lower())
        if col is None:
            continue
        v = t.get("value")
        out[col] = int(v) if isinstance(v, (int, float)) else None
    return out


def _first_caseload(attachments):
    for a in attachments or []:
        if a.get("type") == "caseLoad":
            return a
    return None


def _cost(attachments):
    for a in attachments or []:
        if a.get("type") == "cost":
            v = a.get("attachmentVersion", {}).get("value", {}).get("cost")
            if isinstance(v, (int, float)):
                return int(v)
    return None


def parse_plan(detail, year, funding_total=None):
    """Flatten a plan-detail payload into (plan_row, caseload_rows)."""
    pv = detail.get("planVersion", {})
    locations = detail.get("locations") or []
    iso3s = sorted(
        {
            (loc.get("iso3") or "").upper()
            for loc in locations
            if loc.get("adminLevel") == 0 and loc.get("iso3")
        }
    )
    categories = detail.get("categories") or []

    plan_row = {
        "plan_id": detail["id"],
        "code": pv.get("code"),
        "name": pv.get("name"),
        "short_name": pv.get("shortName"),
        "plan_type": categories[0].get("name") if categories else None,
        "iso3": ";".join(iso3s) or None,
        "year": year,
        "start_date": pv.get("startDate"),
        "end_date": pv.get("endDate"),
        "is_gho": pv.get("isPartOfGHO"),
        "released_date": detail.get("releasedDate"),
        "source_updated_at": detail.get("updatedAt"),
        "orig_requirements": detail.get("origRequirements"),
        "revised_requirements": detail.get("revisedRequirements"),
        "funding_total": funding_total,
        "total_population": None,
        "in_need": None,
        "targeted": None,
        "affected": None,
        "expected_reach": None,
        "reached": None,
    }
    plan_caseload = _first_caseload(detail.get("attachments"))
    if plan_caseload is not None:
        plan_row.update(_caseload_totals(plan_caseload))

    caseload_rows = []
    for ge in detail.get("governingEntities") or []:
        cl = _first_caseload(ge.get("attachments"))
        if cl is None:
            continue
        row = {
            "plan_id": detail["id"],
            "entity_id": ge["id"],
            "cluster_name": ge.get("governingEntityVersion", {}).get("name"),
            "requirements": _cost(ge.get("attachments")),
            "total_population": None,
            "in_need": None,
            "targeted": None,
            "affected": None,
            "expected_reach": None,
            "reached": None,
        }
        row.update(_caseload_totals(cl))
        caseload_rows.append(row)
    return plan_row, caseload_rows
