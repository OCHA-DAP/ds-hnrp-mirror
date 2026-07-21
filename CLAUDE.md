# ds-hnrp-mirror

Mirror of OCHA HNRP/appeal plan data + PiN into the team **dev** Postgres, schema
**`hpc`** (named for the source — it covers ALL appeal types: HNRPs/HRPs, flash
appeals, RRPs, CAPs, other). GH Pages explorer deployed from Actions.

## Key facts

- Tables: `hpc.plans` (PK `plan_id` — the only stable join key; codes/names shift),
  `hpc.plan_caseloads` (PK `(plan_id, entity_id)`), `hpc.needs_admin` (HAPI mirror,
  full replace with a min-row-count guard).
- Sources: HPC API + FTS (all years, plan/cluster level) and HDX HAPI
  `affected-people/humanitarian-needs` (admin 0–2, Global-HNO countries only, 2024+).
  Flash appeals/RRPs have **no** public admin-level PiN — plan/cluster is their max.
- HAPI category rows overlap (Total/Adult/by-gender) — filter, never sum across.
- DB access via `ocha_stratus.get_engine(stage=STAGE, write=...)`; `PGSSLMODE=require`.
- GHA: `DSCI_AZ_DB_*` are OCHA-DAP **org-level** secrets (no per-repo setup);
  only `HAPI_APP_IDENTIFIER` (base64 of `app-name:email`) is a repo secret.
- CI pins Python 3.12 (psycopg2-binary); installs with `uv pip install --no-sources -e .`.
- Workflows: `refresh-hnrp.yml` (daily = current±1 years; Sunday cron + `all_years`
  dispatch input = full 2004+ backfill) → `deploy-site.yml` chains via `workflow_run`,
  regenerates `site/data/*.json` from the DB (git-ignored, never committed).
- Historical pre-2024 admin-level PiN exists only as heterogeneous per-country xlsx
  on HDX (`ocha-hpc-tools` org) or ~8 MB HPC disaggregation matrices — phase-2, not v1.
- KB pages: `pipelines/hnrp-mirror.md`, `infrastructure/datasets/hnrp.md`.
