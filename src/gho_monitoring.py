"""Client for OCHA's GHO monitoring dashboard (people targeted / prioritized / reached).

The **only** public surface for subnational response monitoring is the
"Hyper-Prioritized Global Humanitarian Overview — Status Update" Power BI report
embedded on humanitarianaction.info. Neither the HPC API nor HAPI carries it:
the HPC plan endpoint returns empty `measurements` with
`measurementsGenerated: false`, and HAPI's 2026 `population_status` rows stop at
admin-0. The report's semantic model, however, holds a `disaggregated_caseloads`
table keyed by p-code with PiN, target, prioritized target, reached and
prioritized reached — which is exactly the missing layer.

Publish-to-web reports are queryable anonymously; the resource key in the embed
URL is the only credential. The awkward part is the routing:

1. the article page carries an iframe `.../view?r=<base64 {k: resourceKey, t: tenantId}>`
2. that page's HTML names a *cluster* host, but queries go to the **API** host —
   drop `-redirect`, append `-api` (`getAPIMUrl` in their bootstrap)
3. `GET <api>/public/routing/cluster/<tenantId>` resolves the real cluster
4. `GET  <api>/public/reports/<key>/modelsAndExploration` → report + model ids
   `POST <api>/public/reports/conceptualschema`                → entity columns
   `POST <api>/public/reports/querydata?synchronous=true`      → the data

Skipping step 2 fails in a way that looks like a network fault rather than a
wrong host: `/public/*` resets the connection mid-response on the cluster host.

THIS IS A SCRAPE, NOT AN API CONTRACT. Nothing obliges OCHA to keep the column
names stable — one of them is literally misspelled (`People priritized`), and
the day that typo is fixed a trusting parser writes zeros over a good snapshot.
So `fetch_all` validates every expected column against the conceptual schema
before issuing a single data query, and raises rather than returning partial
data. Callers must let that abort the write.
"""

import base64
import json
import logging
import re
import time
import uuid
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

ARTICLE_URL = (
    "https://humanitarianaction.info/document/global-humanitarian-overview-2026"
    "/article/monitoring-humanitarian-action-interactive-dashboard"
)
# Fallback if the article page moves or stops embedding the report: the values
# decoded from its iframe on 2026-08-10. A re-publish mints a new resource key,
# so discovery from the page is preferred and these are the safety net.
FALLBACK_RESOURCE_KEY = "32d3cc00-a510-4931-bf1b-0ac0bba169a7"
FALLBACK_TENANT_ID = "0f9e35db-544f-4f60-bdcc-5ea416e6dc70"
SEED_CLUSTER = "https://wabi-north-europe-j-primary-redirect.analysis.windows.net"

ENTITY = "disaggregated_caseloads"
PERIOD_ENTITY = "tbl_MonitoringPeriod"

# Grouping columns and summed measures, as named in the semantic model.
# `People priritized` is misspelled upstream — mirrored verbatim on purpose.
DIM_COLS = [
    "planId", "country", "Year", "Pcode", "Disaggregation location", "AdmLevel",
    "Admin 0 P-Code", "Admin 1 P-Code", "Cluster Name", "IC Severity",
    "IC Severity class",
]
MEASURE_COLS = [
    "People in need", "People targeted", "People priritized",
    "People reached", "People prioritized reached",
]
PERIOD_COLS = ["planId", "Year", "Country", "Latest update date"]

# Model column -> our column.
COLUMN_MAP = {
    "planId": "plan_id",
    "country": "country",
    "Year": "year",
    "Pcode": "pcode",
    "Disaggregation location": "location_path",
    "AdmLevel": "admin_level",
    "Admin 0 P-Code": "admin0_code",
    "Admin 1 P-Code": "admin1_code",
    "Cluster Name": "cluster_name",
    "IC Severity": "ic_severity",
    "IC Severity class": "ic_severity_class",
    "People in need": "in_need",
    "People targeted": "targeted",
    "People priritized": "prioritized_target",
    "People reached": "reached",
    "People prioritized reached": "prioritized_reached",
}

# The intersectoral row. Every plan publishes one alongside its cluster rows;
# it is the figure the dashboard's country table shows.
INTERSECTORAL_CLUSTER = "HNRP"

REQUEST_DELAY_S = 0.3
# One query per country stays well inside the row cap (the widest is Colombia at
# ~1,100 units x 18 clusters). A single global query silently truncates at the
# window size instead of erroring, which is the worst possible failure here.
WINDOW = 30_000


class SchemaChanged(RuntimeError):
    """An expected entity or column is missing from the published model."""


def _headers(post=False):
    h = {
        "Accept": "application/json",
        "ActivityId": str(uuid.uuid4()),
        "RequestId": str(uuid.uuid4()),
        "X-PowerBI-ResourceKey": _session()["resource_key"],
    }
    if post:
        h["Content-Type"] = "application/json;charset=UTF-8"
    return h


def _apim(uri):
    """Cluster host -> API host, per the report bootstrap's getAPIMUrl."""
    p = urlparse(uri if "//" in uri else "https://" + uri)
    host = p.hostname.split(".")
    host[0] = host[0].replace("-redirect", "").replace("global-", "") + "-api"
    return f"{p.scheme}://{'.'.join(host)}"


def discover_embed():
    """Read the resource key and tenant id out of the article page's iframe."""
    try:
        r = requests.get(ARTICLE_URL, timeout=60, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ds-hnrp-mirror/1.0)"})
        r.raise_for_status()
        m = re.search(r"app\.powerbi\.com/view\?r=([A-Za-z0-9_-]+)", r.text)
        if not m:
            raise ValueError("no Power BI iframe on the article page")
        blob = m.group(1)
        blob += "=" * (-len(blob) % 4)
        cfg = json.loads(base64.b64decode(blob))
        return cfg["k"], cfg["t"]
    except Exception as exc:
        logger.warning(
            "Could not discover the embed from %s (%s) — falling back to the "
            "resource key pinned on 2026-08-10", ARTICLE_URL, exc)
        return FALLBACK_RESOURCE_KEY, FALLBACK_TENANT_ID


_STATE = {}


def _session():
    if not _STATE:
        key, tenant = discover_embed()
        _STATE.update(resource_key=key, tenant_id=tenant)
        base = _apim(SEED_CLUSTER)
        r = requests.get(f"{base}/public/routing/cluster/{tenant}",
                         headers={"Accept": "application/json",
                                  "ActivityId": str(uuid.uuid4()),
                                  "RequestId": str(uuid.uuid4()),
                                  "X-PowerBI-ResourceKey": key},
                         timeout=60)
        r.raise_for_status()
        _STATE["base"] = _apim(r.json()["FixedClusterUri"])
        meta = requests.get(
            f"{_STATE['base']}/public/reports/{key}/modelsAndExploration"
            "?preferReadOnlySession=true",
            headers=_headers(), timeout=120)
        meta.raise_for_status()
        m = meta.json()
        _STATE["dataset_id"] = m["models"][0]["dbName"]
        _STATE["model_id"] = m["models"][0]["id"]
        _STATE["report_id"] = m["exploration"]["report"]["objectId"]
        _STATE["dataset_refreshed"] = m["models"][0].get("LastRefreshTime")
        logger.info("Power BI model %s, last refreshed %s",
                    _STATE["model_id"], _STATE["dataset_refreshed"])
    return _STATE


def dataset_refreshed_at():
    return _session().get("dataset_refreshed")


def conceptual_schema():
    s = _session()
    r = requests.post(f"{s['base']}/public/reports/conceptualschema",
                      headers=_headers(post=True),
                      data=json.dumps({"modelIds": [s["model_id"]]}), timeout=120)
    r.raise_for_status()
    return r.json()


def validate_schema():
    """Raise unless every entity and column we read still exists."""
    def entities(o):
        if isinstance(o, dict):
            if "Entities" in o:
                return o["Entities"]
            for v in o.values():
                found = entities(v)
                if found:
                    return found
        if isinstance(o, list):
            for v in o:
                found = entities(v)
                if found:
                    return found
        return None

    ents = {e["Name"]: {p["Name"] for p in e.get("Properties", [])}
            for e in entities(conceptual_schema()) or []}
    for name, cols in ((ENTITY, DIM_COLS + MEASURE_COLS),
                       (PERIOD_ENTITY, PERIOD_COLS)):
        if name not in ents:
            raise SchemaChanged(f"entity {name!r} is gone from the published model")
        missing = [c for c in cols if c not in ents[name]]
        if missing:
            raise SchemaChanged(f"{name}: columns no longer published: {missing}")
    logger.info("Schema check passed (%s, %s)", ENTITY, PERIOD_ENTITY)


def _field(alias, prop):
    return {"Expression": {"SourceRef": {"Source": alias}}, "Property": prop}


def query(entity, cols=(), measures=(), where_col=None, where_value=None,
          window=WINDOW, retries=3):
    """Run one grouped semantic query; returns a list of dicts keyed by model name."""
    s, alias = _session(), "d"
    select, names = [], []
    for c in cols:
        select.append({"Column": _field(alias, c), "Name": f"{alias}.{c}"})
        names.append(c)
    for m in measures:
        select.append({"Aggregation": {"Expression": {"Column": _field(alias, m)},
                                       "Function": 0}, "Name": f"Sum({alias}.{m})"})
        names.append(m)
    q = {"Version": 2, "From": [{"Name": alias, "Entity": entity, "Type": 0}],
         "Select": select}
    if where_col is not None:
        q["Where"] = [{"Condition": {"In": {
            "Expressions": [{"Column": _field(alias, where_col)}],
            "Values": [[{"Literal": {"Value": "'" + str(where_value).replace("'", "''") + "'"}}]],
        }}}]
    body = {
        "version": "1.0.0",
        "queries": [{
            "Query": {"Commands": [{"SemanticQueryDataShapeCommand": {
                "Query": q,
                "Binding": {
                    "Primary": {"Groupings": [{"Projections": list(range(len(select)))}]},
                    "DataReduction": {"DataVolume": 4,
                                      "Primary": {"Window": {"Count": window}}},
                    "Version": 1,
                },
            }}]},
            "QueryId": "",
            "ApplicationContext": {"DatasetId": s["dataset_id"],
                                   "Sources": [{"ReportId": s["report_id"]}]},
        }],
        "cancelQueries": [],
        "modelId": s["model_id"],
    }
    url = f"{s['base']}/public/reports/querydata?synchronous=true"
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=_headers(post=True),
                              data=json.dumps(body), timeout=180)
            r.raise_for_status()
            rows = _decode(r.json(), names)
            time.sleep(REQUEST_DELAY_S)
            if len(rows) >= window:
                raise SchemaChanged(
                    f"{entity}/{where_value}: hit the {window}-row window — the "
                    "result is truncated, narrow the query before trusting it")
            return rows
        except (requests.RequestException, ValueError):
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def _decode(payload, names):
    """Undo Power BI's DSR row compression.

    Rows carry only what CHANGED from the row before: bitmask `R` marks columns
    repeated from the previous row, `Ø` marks nulls, and whatever remains is
    consumed in order from `C`. String columns are additionally dictionary
    encoded — the row holds an index into `ValueDicts[DN]`.
    """
    data = payload["results"][0]["result"]["data"]
    dsr = data["dsr"]
    if "DataShapes" in dsr:
        err = dsr["DataShapes"][0].get("odata.error")
        if err:
            raise SchemaChanged(err["message"]["value"])
    out = []
    for ds in dsr.get("DS", []):
        dicts = ds.get("ValueDicts", {})
        for ph in ds.get("PH", []):
            for segment in ph.values():
                if not isinstance(segment, list):
                    continue
                schema, prev = None, [None] * len(names)
                for row in segment:
                    if "S" in row:
                        schema = row["S"]
                    packed = row.get("C", [])
                    repeat, nulls = row.get("R", 0), row.get("Ø", 0)
                    values, i_packed = [], 0
                    for i in range(len(names)):
                        if nulls >> i & 1:
                            values.append(None)
                        elif repeat >> i & 1:
                            values.append(prev[i])
                        else:
                            v = packed[i_packed] if i_packed < len(packed) else None
                            i_packed += 1
                            dn = schema[i].get("DN") if schema and i < len(schema) else None
                            if dn and isinstance(v, int) and dn in dicts:
                                table = dicts[dn]
                                v = table[v] if v < len(table) else v
                            values.append(v)
                    prev = values
                    out.append(dict(zip(names, values)))
    return out


def list_countries():
    rows = query(ENTITY, cols=["country"], measures=["People in need"])
    return sorted({r["country"] for r in rows if r["country"]})


def fetch_all(intersectoral_only=False):
    """Every disaggregated caseload row, one query per country.

    Validates the model first: a rename upstream must abort the refresh, not
    quietly mirror zeros over a good snapshot.
    """
    validate_schema()
    rows = []
    for country in list_countries():
        got = query(ENTITY, cols=DIM_COLS, measures=MEASURE_COLS,
                    where_col="country", where_value=country)
        if intersectoral_only:
            got = [r for r in got if r.get("Cluster Name") == INTERSECTORAL_CLUSTER]
        logger.info("  %s: %s rows", country, len(got))
        rows.extend(got)
    return [{COLUMN_MAP[k]: v for k, v in r.items() if k in COLUMN_MAP} for r in rows]


def fetch_periods():
    """Per-plan monitoring vintage — which month each country last reported.

    Countries update on their own cadence (Afghanistan March, DRC May, Chad
    June, OPT January), so a figure is only interpretable next to its month.
    """
    rows = query(PERIOD_ENTITY, cols=PERIOD_COLS)
    out = []
    for r in rows:
        if r.get("planId") is None:
            continue
        out.append({
            "plan_id": int(r["planId"]),
            "year": int(r["Year"]) if r.get("Year") else None,
            "country": r.get("Country"),
            "latest_update": r.get("Latest update date"),
        })
    return out
