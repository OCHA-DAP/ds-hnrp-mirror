# ds-hnrp-mirror

Mirror of OCHA HNRP/appeal plan data + PiN into the team **dev** Postgres, schema
**`hpc`** (named for the source — it covers ALL appeal types: HNRPs/HRPs, flash
appeals, RRPs, CAPs, other). GH Pages explorer deployed from Actions.

## Key facts

- Tables: `hpc.plans` (PK `plan_id` — the only stable join key; codes/names shift),
  `hpc.plan_caseloads` (PK `(plan_id, entity_id)`), `hpc.needs_admin` (HAPI + Global HNO adm3 rows,
  full replace with a min-row-count guard), `hpc.severity_admin` (JIAF final
  severity 1–5; anchor-based parser over localized country workbooks — see
  src/jiaf.py; UKR/SYR-2026/YEM-2025 publish no severity sheet), `hpc.pin_admin`
  (JIAF overall PiN per admin×popgroup from the WS-3.1 sheet; 2026 rows carry own
  `severity` → final PiN by severity = the Reset-reintroduced PiN-by-severity
  distribution; 2025 rows: severity NULL, join severity_admin; newest HDX
  resource wins per country-year, so revised re-uploads supersede; mirrored
  as-is — SSD 2026 fills a constant severity 4, cross-check severity_admin).
- PBS = sum(final_pin) by COALESCE(final_severity, severity) per unit
  (admin×popgroup×pocket); partitions overall PiN; sev 1–2 ≈ 0 by template design
  (PiN blanked below sev 3). pin_admin.final_severity = WS-3.2 join done at refresh
  (jiaf.attach_final_severity: deepest-code key, name + area-level fallbacks) —
  NEVER trust pin_admin.severity alone: it's a pcode-keyed lookup offices break
  (SSD 2026 pasted constant; LBN 2026 prelim had blank pcodes → all IDs "" → MATCH
  broadcast row 1's severity to every unit). Template formulas: mosaic max +
  severity IFS run over core sectors ONLY (AoRs excluded; sector count varies by
  country — LBN runs 9); final cols = formula defaults unless workshop-overwritten.
  Blank WS-3A/3B template + "Overview of changes" doc: OCHA KB wiki page "JIAF
  Manuals" (HPC 2026 Tools); README + KB pipelines/hnrp-mirror.md have links.
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
- Pcode audit (2026-07, vs `public.polygon` prod): HAPI needs pcodes ~100% clean except
  `*-XXX` placeholders (intentional), Chad `TCD`→`TD` prefix, SOM Banadir adm2 gap in the
  reference. Severity workbook quirks: NER `NER`→`NE`, COL zero-padding, MLI/BFA codes
  newer than COD-AB (real 2023–25 admin reforms). adm3 parents are derived by longest
  pcode-prefix vs public.polygon (prod read) in refresh_needs.py — upstream CSV leaves
  them blank, and BFA/COD/ETH/SYR have NO HAPI subnational rows (adm3-only countries),
  so polygon is the only parent universe for them.
- Sense-check reference colleagues use: the GHO country-plans dashboard
  (humanitarianaction.info/article/gho-country-plans-interactive-dashboard).
  Its HDX export (`global-humanitarian-overview-<year>`) carries HPC `plan_id`
  (joins to `hpc.plans`) but is a Dec/June snapshot — the mirror pulls the same
  HPC/FTS APIs daily, so mid-cycle diffs = upstream revisions, not mirror bugs
  (verified 2026-08-04: 35/35 plans, exact except July-revised COL/COD/VEN).
- KB pages: `pipelines/hnrp-mirror.md`, `infrastructure/datasets/hnrp.md`.
