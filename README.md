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

## Tables (dev DB, schema `hpc`)

- `hpc.plans` — one row per plan: metadata, requirements, FTS funding, plan-level caseload totals. PK `plan_id`.
- `hpc.plan_caseloads` — cluster-level caseloads + requirements. PK `(plan_id, entity_id)`.
- `hpc.needs_admin` — HAPI humanitarian-needs mirror + Global HNO admin-3 rows (admin 0–3 × sector × category × status). Full replace on refresh.
- `hpc.severity_admin` — JIAF intersectoral final severity (1–5) per admin area × population group, parsed from per-country workbooks (localized EN/FR/ES templates; anchor-based parser, unparseable files logged). Full replace on refresh.
- `hpc.pin_admin` — JIAF intersectoral overall PiN (preliminary + final) per admin area × population group, from the same workbooks ("WS - 3.1 Overall PiN" / "PiN" sheet). 2026-cycle rows carry their own `severity` — final PiN grouped by it is the **PiN-by-severity distribution** the 2025 Humanitarian Reset reintroduced (overall PiN counts only phase-3+ areas from HPC 2026 on). 2025 rows have `severity` NULL — join `severity_admin` on (iso3, year, admin codes, population_group). Full replace on refresh.

## Pipelines (GitHub Actions)

- **Refresh HNRP mirror** (`refresh-hnrp.yml`) — daily 04:17 UTC refreshes current/previous/next plan years (HPC + FTS), then the admin-level PiN (HAPI + Global HNO adm3) and JIAF severity mirrors; Sunday 02:47 UTC runs the full historical backfill. Manual dispatch with `all_years` for an on-demand backfill.
- **Deploy explorer site** (`deploy-site.yml`) — chains off a successful refresh (plus a daily backstop), exports `site/data/*.json` from the DB and deploys `site/` to GitHub Pages. Nothing is committed; data is regenerated each deploy.

## Local use

```bash
uv sync
cp .env.example .env  # fill in creds
uv run python scripts/refresh_hpc.py --years 2025,2026
uv run python scripts/refresh_needs.py
uv run python scripts/refresh_jiaf.py
uv run python scripts/export_site_data.py && open site/index.html
```

## Gotchas

- Key on `plan_id` (HPC) — plan **codes/names change** between versions and years; "HNRP" vs "HNO + HRP" is a naming shift around 2024.
- FTS funding is as-reported (self-reported, lags); a low % funded is not a data error.
- HAPI category rows **overlap** (e.g. Adult / Total / by-gender) — filter, don't sum across categories.
- `pin_admin` national sums can differ from the official plan PiN in `hpc.plans`
  (workbook vs. HPC-reconciled figures; some workbooks track refugees in a separate
  column). A few workbooks fill only one of preliminary/final (NGA 2025 publishes
  no final PiN). Where a dataset re-uploads a revised workbook (COD 2026), the
  **newest resource wins** for both severity and PiN.
- `pin_admin.severity` is the PiN sheet's own column, mirrored as-is — a few
  country offices fill it carelessly (SSD 2026 has a constant 4 on every row while
  its severity sheet has a real 3/4/5 spread). For analysis, sanity-check against
  `severity_admin` (the WS-3.2 final severity) before trusting a degenerate
  distribution.
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
