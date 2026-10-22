---
name: add-report
description: Use when adding a new scheduled report or changing an existing one (daily, weekly, monthly, or a new report type) in config/reports.yaml, including its queries, email template section and tests.
---

# Add a scheduled report

Reports are config-driven (plan sections 7 and 8). Adding a report should mean
adding a YAML block that reuses existing section and chart names, not writing a
new pipeline. KPI definitions are in `docs/decisions.md` (Week 2 section).

How the pieces fit:

- `config/reports.yaml`: one block per report.
- `src/analytics/kpis.sql`: all KPI SQL, as named blocks (`-- name: ...`) taking
  `$start` / `$end`. `src/analytics/kpis.py` runs them read-only and lines up
  periods and comparisons.
- `src/reports/builder.py`: `QUERY_SECTIONS` maps a query name to a function that
  returns that section's numbers; `CHARTS` maps a chart name to a function that
  returns PNG bytes. `build_report(name, as_of)` returns a `Report`
  (subject, html, images keyed by cid, excel_path, numbers) and saves a dry run.
- `src/reports/templates/report.html`: one generic, Gmail-safe template. It renders
  whatever blocks the builder produces, so most sections need no template change.

## Checklist

1. **Add the block to `config/reports.yaml`:** `title`, `period` (day / week /
   month: the report covers the period ending on `as_of`), `schedule` (APScheduler
   cron fields), `queries` (section names from `QUERY_SECTIONS`, in display order),
   `compare_to` (names from `kpis.COMPARISONS` that fit the period; the first one
   drives the change columns in breakdown and top tables), `charts` (names from
   `CHARTS`), `attach_excel`, `recipients`.
   - During the build, recipients are only `"${REPORT_OWNER_EMAIL}"`. Write
     addresses as `${NAME}` taken from `.env`, never as literals.
   - Every recipient must be on `delivery.allowed_recipients`, or the whole send
     is refused.
   - `load_config` rejects unknown names, so a typo fails loudly. The top-level
     `delivery` key is settings, not a report.
2. **Reuse sections first.** Existing: `kpis_day`, `kpis_week`, `kpis_month`,
   `category_breakdown`, `margin_by_category`, `channel_breakdown`,
   `region_breakdown`, `customer_type_breakdown`, `top_products`,
   `top_products_5`, `top_customers`, `customer_growth`, `demand_alerts` and
   `forecast_accuracy`.
   - The two demand sections (Week 5, `src/reports/demand_sections.py`) read
     the demand snapshot for the report's as_of through
     `src.demand.run.get_or_run`. They reuse a stored snapshot, or compute one,
     which adds about 100 s.
   - They are `groups` sections: lists cut at `reports.max_rows_per_type` in
     `config/demand.yaml`, with "and N more", and one Excel sheet with every row.
     `email_columns` picks the columns the email shows (the pre-build table shows
     5).
   - Never read `data/planted_issues.json` in report code.
3. **Only if a new number is needed:**
   - Add a named block to `kpis.sql` using `$start` / `$end`; never build SQL from
     strings. Expose it from `kpis.py` returning a DataFrame.
   - Add a hand-calculated case to `tests/test_kpis.py` (literal expected values,
     with the working in the module docstring).
   - Add a section function in `builder.py` that returns plain, rounded values
     (`rounded(value, kind)`) and a `type` the template already handles
     (`kpis`, `breakdown`, `top`, `groups`, `placeholder`), then register it in
     `QUERY_SECTIONS`. Add an Excel frame in `excel_frames` if it needs a sheet.
   - Everything shown must come from the section's numbers: the HTML test fails on
     any number in the HTML that is not in `Report.numbers`.
4. **New chart:** a function `(ctx, sections) -> (png_bytes, {"title": ...})` using
   `new_axes()` and `to_png()` (2x dpi, house colours), registered in `CHARTS`.
   Reuse section numbers rather than re-querying where possible.
5. **Template:** only touch `report.html` for a new block type. Keep table layout,
   inline styles, right-aligned numbers, explicit +/- signs, and check it at phone
   width (375px) without horizontal overflow.
6. **Summary input (Week 4):** `Report.numbers` is what the LLM receives. Make sure it
   holds everything the summary may mention.
7. **Tests:** add the report and a fixed `as_of` to `AS_OF` in
   `tests/test_reports.py` (and its sheet list if it attaches Excel). The existing
   checks then cover template tags, numbers, Excel sheets and PNGs.
8. **Dry run and hand check:**
   `python -m src.reports.builder --report <name> --as-of <YYYY-MM-DD>`, open
   `outputs/reports/<name>_<as_of>/report.html` in a browser and the `.xlsx` in Excel,
   and check headline numbers against plain SQL (see
   `docs/hand_checks/week2_weekly_2026-09-27.md` for the pattern).
9. **Schedule check:** `python -m src.reports.scheduler --list` shows the new job
   and its next run time.
   - `REPORT_MODE=dry_run python -m src.reports.scheduler --run-now <name>` builds
     the period chosen by the data clock and writes a `report_runs` row.
   - If the new period kind needs it, add a case to `tests/test_data_clock.py`.
10. **Backfill and dashboard (Week 6):**
    - The backfill (`python -m src.reports.backfill`) takes run dates from the
      schedules in `reports.yaml`, so a new report is backfilled with no code
      change. Check that the `tests/test_backfill.py` counts still hold, or update
      them.
    - If the report should also show on the dashboard, add a data function to
      `src/reports/views.py` that reuses the builder sections and rounds once
      (`views.table`), with a test in `tests/test_views.py`.
    - Then add the page in `src/ui/app_pages/` (not `pages/`), register it in
      `src/ui/app.py`, and add it to `PAGES` in `tests/test_ui.py`. Pages have no
      SQL and open no database (`tests/test_guardrails.py`).
11. Run `ruff format . && ruff check --fix .`, `pytest` and `pytest --runslow`
    (the Week 5 acceptance tests build the weekly and monthly reports), then commit.
