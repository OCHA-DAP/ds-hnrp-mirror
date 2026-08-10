# ds-hnrp-mirror

Mirror of OCHA **HNRP/HRP plan data and People in Need (PiN)** figures in the team
Postgres (dev, schema `hpc`), refreshed automatically, with a
[GitHub Pages explorer](https://ocha-dap.github.io/ds-hnrp-mirror/).

## Sources

| Source | What | Granularity | Coverage |
|---|---|---|---|
| [HPC API](https://api.hpc.tools) (`/v2/public/plan`, `/v1/public/plan/id/{id}?content=measurements`) | Plan metadata, plan- and cluster-level caseloads (total population, in need, targeted, reached), requirements | plan / cluster | all plan years (2004+; caseloads ~2017+) |
| [FTS](https://api.hpc.tools/v1/public/fts/flow) | Reported funding per plan | plan | all years |
| [HDX HAPI](https://hapi.humdata.org) `affected-people/humanitarian-needs` | PiN by admin area, sector, category, population status (Global HNO) | up to **admin-2** | 2024+ (~24 HNRP countries) |
| [Global HPC HNO CSVs](https://data.humdata.org/dataset/global-hpc-hno) | Admin-3 PiN rows HAPI truncates | **admin-3** (BFA, COD, ETH, MMR, SYR) | 2024–2025 |
| [Per-country JIAF workbooks](https://data.humdata.org/search?q=jiaf%20humanitarian%20needs) (`*-jiaf-humanitarian-needs-*`) | Intersectoral **final severity (1–5)** and **overall PiN (preliminary/final)** per admin area × population group | admin-2 (admin-3: BFA, COD, SYR) | 2025+ (~20 countries) |
| [GHO monitoring dashboard](https://humanitarianaction.info/document/global-humanitarian-overview-2026/article/monitoring-humanitarian-action-interactive-dashboard) (Power BI semantic model) | **People targeted, prioritized target, reached, prioritized reached** per admin area × cluster, plus intersectoral severity | admin-2 (admin-3: COD, MMR, SYR) | 2026 (20 plans) |

### Coverage notes

- The HPC mirror covers **every plan type** — HNRPs/HRPs, **flash appeals**, regional
  response plans, and "other" appeals — for all years, at plan and cluster level.
- HAPI's admin-level PiN covers only **Global HNO countries** (~24 HNRPs, 2024+).
  Flash-appeal countries (e.g. OPT, Lebanon 2025) and RRPs have **no standardized
  admin-level PiN** there — for those, plan/cluster level (the HPC mirror) is the most
  granular public data.
- **Historical admin-level PiN (pre-2024)** exists publicly but not in standardized
  form: per-country `*_hpc_needs_<year>.xlsx` files (hand-formatted, heterogeneous)
  on HDX under the [`ocha-hpc-tools` org](https://data.humdata.org/organization/hpc-tools)
  (some back to 2015), and raw HPC API disaggregation matrices
  (`?disaggregation=true`, plan-specific categories, ~8 MB/attachment). Both are
  bespoke-parsing projects — candidates for a later phase, not mirrored in v1.
- **HAPI resilience**: the same humanitarian-needs data is also published as flat
  CSVs on HDX ([`hdx-hapi-humanitarian-needs`](https://data.humdata.org/dataset/hdx-hapi-humanitarian-needs),
  [`global-hpc-hno`](https://data.humdata.org/dataset/global-hpc-hno), and per-country
  `*_hpc_needs_api_<year>.csv`). If HAPI were ever discontinued, swapping `src/hapi.py`
  to read those CSVs is a small change; the DB schema would not change.
- **Subnational `reached` has exactly one public source** — the GHO monitoring
  dashboard. The HPC API publishes no measurements for 2026 plans
  (`measurementsGenerated: false`, empty `measurements` arrays) and HAPI's 2026
  `population_status` rows stop at admin-0, with `REA` present only for SOM 2024
  and VEN 2024. Everything subnational about the *response* (as opposed to needs)
  comes from `monitoring_admin`.
- Monitoring coverage is **uneven by design** — partners report what they report.
  Syria carries PiN with no target or reach; Ukraine and Venezuela are near-empty
  (VEN has reach but no PiN/target); Cameroon and Somalia have targets but no
  prioritized target. Absence is not zero: read these as unreported, not nil.

## Tables (dev DB, schema `hpc`)

- `hpc.plans` — one row per plan: metadata, requirements, FTS funding, plan-level caseload totals. PK `plan_id`.
- `hpc.plan_caseloads` — cluster-level caseloads + requirements. PK `(plan_id, entity_id)`.
- `hpc.needs_admin` — HAPI humanitarian-needs mirror + Global HNO admin-3 rows (admin 0–3 × sector × category × status). Full replace on refresh.
- `hpc.severity_admin` — JIAF intersectoral final severity (1–5) per admin area × population group, parsed from per-country workbooks (localized EN/FR/ES templates; anchor-based parser, unparseable files logged). Full replace on refresh.
- `hpc.pin_admin` — JIAF intersectoral overall PiN (preliminary + final) per admin area × population group, from the same workbooks ("WS - 3.1 Overall PiN" / "PiN" sheet). Two severity columns: `severity` = the PiN sheet's own column, mirrored as-is (it's only a lookup of WS-3.2 and country offices break it); **`final_severity` = the WS-3.2 final severity joined on the unit at refresh time** (deepest admin code, name fallback; population group with area-level fallback). **PBS = final PiN grouped by `COALESCE(final_severity, severity)`** — the distribution the 2025 Humanitarian Reset reintroduced (overall PiN counts only phase-3+ areas from HPC 2026 on). Refresh logs warn when the two columns disagree. Full replace on refresh.
- `hpc.monitoring_admin` — subnational response monitoring from the GHO dashboard: `in_need`, `targeted`, `prioritized_target`, `reached`, `prioritized_reached` per admin area × cluster, with the area's intersectoral severity. The `HNRP` cluster row is the intersectoral figure the dashboard's country table shows. **Append-only**, PK `(snapshot_date, plan_id, pcode, cluster_name)` — see below.
- `hpc.monitoring_periods` — per-plan reporting vintage (`latest_update`: the month a country last reported). PK `(snapshot_date, plan_id)`.

### Why monitoring is append-only

Every other table here is a full replace. `monitoring_admin` is not, because
`reached` is **cumulative and climbs through the plan year** while the dashboard
only ever exposes current state — it keeps no history of its own. Replacing on
each refresh would discard the single thing worth refreshing frequently for.
Re-running on the same day overwrites that day's rows and leaves earlier
snapshots alone. Read the latest with `storage.read_monitoring()`, or take
`max(snapshot_date)` per `plan_id`; a plan's figures only actually move when its
`monitoring_periods.latest_update` month does.

## Pipelines (GitHub Actions)

- **Refresh HNRP mirror** (`refresh-hnrp.yml`) — daily 04:17 UTC refreshes current/previous/next plan years (HPC + FTS), then the admin-level PiN (HAPI + Global HNO adm3), JIAF severity, and subnational monitoring mirrors; Sunday 02:47 UTC runs the full historical backfill. Manual dispatch with `all_years` for an on-demand backfill.
- **Deploy explorer site** (`deploy-site.yml`) — chains off a successful refresh (plus a daily backstop), exports `site/data/*.json` from the DB and deploys `site/` to GitHub Pages. Nothing is committed; data is regenerated each deploy.

## Local use

```bash
uv sync
cp .env.example .env  # fill in creds
uv run python scripts/refresh_hpc.py --years 2025,2026
uv run python scripts/refresh_needs.py
uv run python scripts/refresh_jiaf.py
uv run python scripts/refresh_monitoring.py --dry-run   # fetch + summarise only
uv run python scripts/export_site_data.py && open site/index.html
```

## Gotchas

- Key on `plan_id` (HPC) — plan **codes/names change** between versions and years; "HNRP" vs "HNO + HRP" is a naming shift around 2024.
- **`monitoring_admin` is a scrape, not an API contract.** The GHO dashboard is a
  publish-to-web Power BI report — public and anonymous by design, resource key
  in the embed URL, no auth bypass — but nothing obliges OCHA to keep the model
  stable. One of the columns is misspelled upstream (`People priritized`), and
  the day that typo is fixed a trusting parser would write zeros over a good
  snapshot. So the refresh validates every entity and column against the report's
  `conceptualschema` **before** issuing a data query and raises `SchemaChanged`
  otherwise; append-only means the last good snapshot survives the failure. It
  also aborts if a per-country query comes back at the row-window cap, since a
  truncated result looks like a complete one. The durable fix is asking OCHA for
  the feed behind the report — worth doing.
- **Subnational monitoring sums sit below the national figures** the dashboard
  prints on its country table (Sudan 2.6M reached subnationally vs 8.0M
  nationally; Chad 0.05M vs 1.1M). A lot of delivery is reported without a
  location attached. Per-area reach is a floor, not a decomposition of the
  headline — the refresh logs how many plans fall short of 90%.
- **`reached` can exceed `targeted` in a unit** (Afghanistan AF0101: 224,446
  reached against 209,847 targeted). The three quantities are not strictly
  nested — don't build a visual that assumes reach ⊆ target ⊆ PiN.
- Monitoring figures carry a **per-country vintage**, not a common as-of date:
  Afghanistan last reported March, DRC May, Chad June, OPT January. Join
  `monitoring_periods` and say which month is on screen.
- FTS funding is as-reported (self-reported, lags); a low % funded is not a data error.
- HAPI category rows **overlap** (e.g. Adult / Total / by-gender) — filter, don't sum across categories.
- `pin_admin` national sums can differ from the official plan PiN in `hpc.plans`
  (workbook vs. HPC-reconciled figures; some workbooks track refugees in a separate
  column). A few workbooks fill only one of preliminary/final (NGA 2025 publishes
  no final PiN). Where a dataset re-uploads a revised workbook (COD 2026), the
  **newest resource wins** for both severity and PiN.
- `pin_admin.severity` (the PiN sheet's own column) is untrustworthy: it's a live
  `INDEX/MATCH` of WS-3.2 keyed on a pcode-built ID, and offices break it — SSD 2026
  pasted a constant 4 over it; a LBN 2026 preliminary workbook left all P-Codes
  blank, collapsing every ID to `""` so `MATCH` returned the *first* unit's severity
  for all 76 rows (a wrong-but-plausible all-3 column). That's why the refresh joins
  WS-3.2 directly into `final_severity` and logs disagreements — use
  `COALESCE(final_severity, severity)` for PBS, never `severity` alone.
- **PiN-by-severity (PBS)** = Σ `final_pin` grouped by `severity` (per unit =
  admin × population group × pocket). It partitions the overall PiN; classes 1–2
  are ≈0 by construction (the 2026 template blanks PiN below severity 3 —
  nonzero values there are manual overrides, e.g. refugee caseloads). In the
  template both the mosaic max (overall PiN) and the intersectoral severity rule
  run over the **8 core sectors only** — Protection AoRs (CP/GBV/MA/HLP) never
  drive them — and "final" columns are formula defaults unless a validation
  workshop overwrote the cell. Full mechanics + doc citations: KB page
  `pipelines/hnrp-mirror.md`. Template ground truth: the blank WS-3A/3B tool on
  the [OCHA KB JIAF Manuals page](https://humanitarian.atlassian.net/wiki/spaces/hpc/pages/3993829401/JIAF+Manuals)
  ("HPC 2026 Tools"), methodology in the
  [JIAF 2 Technical Manual](https://jiaf.info/wp-content/uploads/2024/07/JIAF-2-Technical-Manual_Final-for-2025-HPC.pdf)
  (Mosaic: Box 21 p. 50; severity rule: Box 22 p. 50) and the
  ["Overview of changes in JIAF PIN and Severity tool" (2025-08-22)](https://knowledge.base.unocha.org/wiki/download/attachments/3993829401/Overview%20of%20changes%20in%20JIAF%20PIN%20and%20Severity%20tool.docx?api=v2).
- **Sense-checking**: colleagues usually check HNRP/GHO headline figures against the
  [GHO country plans interactive dashboard](https://humanitarianaction.info/article/gho-country-plans-interactive-dashboard)
  (humanitarianaction.info, Power BI). Its downloadable plan table is the HDX dataset
  `global-humanitarian-overview-<year>` and carries HPC `plan_id`, so it joins
  directly to `hpc.plans`. That export is a **snapshot** (refreshed at GHO
  publication points: December launch + June Mid-Year Review) while this mirror
  refreshes daily from the same HPC/FTS APIs — verified 2026-08-04: all 35 GHO-2026
  plans present, figures exact except plans revised upstream after the June export
  (COL/COD/VEN), where the mirror matched the live HPC API. Mid-cycle, expect the
  mirror to be *ahead* of the dashboard export, especially on funding.
- License: open, attribution to UN OCHA (HPC/FTS) and OCHA via HDX.

## Pcode quality (audited 2026-07 vs `public.polygon`, the team's COD-AB reference)

- **`needs_admin` (HAPI) is p-code-aligned by design** — near-100% match. Residual
  mismatches are (a) HAPI's own `*-XXX` placeholder codes for population not attributable
  to an admin unit (keep or filter, they're intentional), (b) Chad, where HAPI uses
  `TCD##`-prefixed pcodes vs `TD##` in COD-AB (map prefix `TCD`→`TD` to join), and
  (c) Somalia's Banadir districts (`SO22##`), absent from the reference's adm2 layer.
- **adm3 rows** ship without parent pcodes upstream; the refresh derives admin-1/2
  parents by longest-pcode-prefix match against `public.polygon` (+ HAPI codes).
  Note: for the adm3-only countries (**BFA, COD, ETH, SYR**) HAPI carries *no*
  subnational rows at all — their Global HNO publishes only admin-3 — so the CSV
  supplement is the sole source of subnational PiN for them in this mirror.
- **`severity_admin` / `pin_admin` (hand-built workbooks) are messier**: Niger uses `NER###` vs
  COD-AB `NE###`; Chad `TCD##` as above; Colombia drops DIVIPOLA zero-padding
  (`CO5001` vs `CO05001`). Mali (`ML11+`) and Burkina (`BF58+`) reflect **real
  post-reform admin units newer than the COD-AB reference** — those are not errors.
  Join on names or normalize prefixes/padding when matching to boundaries.
