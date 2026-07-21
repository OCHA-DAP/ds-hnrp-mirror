# ds-hnrp-mirror

Mirror of OCHA **HNRP/HRP plan data and People in Need (PiN)** figures in the team
Postgres (dev, schema `hnrp`), refreshed automatically, with a
[GitHub Pages explorer](https://ocha-dap.github.io/ds-hnrp-mirror/).

## Sources

| Source | What | Granularity | Coverage |
|---|---|---|---|
| [HPC API](https://api.hpc.tools) (`/v2/public/plan`, `/v1/public/plan/id/{id}?content=measurements`) | Plan metadata, plan- and cluster-level caseloads (total population, in need, targeted, reached), requirements | plan / cluster | all plan years (2004+; caseloads ~2017+) |
| [FTS](https://api.hpc.tools/v1/public/fts/flow) | Reported funding per plan | plan | all years |
| [HDX HAPI](https://hapi.humdata.org) `affected-people/humanitarian-needs` | PiN by admin area, sector, category, population status (Global HNO) | up to **admin-2** | 2024+ (~24 HNRP countries) |

HAPI is the most granular *standardized* public source. The HPC API also exposes raw
per-plan disaggregation matrices (admin-level, plan-specific categories, ~MBs per
attachment) — not mirrored in v1; see `src/hpc.py` if we ever want it.

## Tables (dev DB, schema `hnrp`)

- `hnrp.plans` — one row per plan: metadata, requirements, FTS funding, plan-level caseload totals. PK `plan_id`.
- `hnrp.plan_caseloads` — cluster-level caseloads + requirements. PK `(plan_id, entity_id)`.
- `hnrp.needs_admin` — HAPI humanitarian-needs mirror (admin 0–2 × sector × category × status). Full replace on refresh.

## Pipelines (GitHub Actions)

- **Refresh HNRP mirror** (`refresh-hnrp.yml`) — daily 04:17 UTC refreshes current/previous/next plan years (HPC + FTS), then the full HAPI mirror; Sunday 02:47 UTC runs the full historical backfill. Manual dispatch with `all_years` for an on-demand backfill.
- **Deploy explorer site** (`deploy-site.yml`) — chains off a successful refresh (plus a daily backstop), exports `site/data/*.json` from the DB and deploys `site/` to GitHub Pages. Nothing is committed; data is regenerated each deploy.

## Local use

```bash
uv sync
cp .env.example .env  # fill in creds
uv run python scripts/refresh_hpc.py --years 2025,2026
uv run python scripts/refresh_hapi.py
uv run python scripts/export_site_data.py && open site/index.html
```

## Gotchas

- Key on `plan_id` (HPC) — plan **codes/names change** between versions and years; "HNRP" vs "HNO + HRP" is a naming shift around 2024.
- FTS funding is as-reported (self-reported, lags); a low % funded is not a data error.
- HAPI category rows **overlap** (e.g. Adult / Total / by-gender) — filter, don't sum across categories.
- License: open, attribution to UN OCHA (HPC/FTS) and OCHA via HDX.
