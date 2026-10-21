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
   `CHARTS`), `attach_excel`, `recipients` (only the project owner during the build).
   `load_config` rejects unknown names, so a typo fails loudly.
2. **Reuse sections first.** Existing: `kpis_day`, `kpis_week`, `kpis_month`,
   `category_breakdown`, `margin_by_category`, `channel_breakdown`,
   `region_breakdown`, `customer_type_breakdown`, `top_products`,
   `top_products_5`, `top_customers`, `customer_growth`, plus placeholders
   `demand_alerts` and `forecast_accuracy` (Week 5).
3. **Only if a new number is needed:**
   - Add a named block to `kpis.sql` using `$start` / `$end`; never build SQL from
     strings. Expose it from `kpis.py` returning a DataFrame.
   - Add a hand-calculated case to `tests/test_kpis.py` (literal expected values,
     with the working in the module docstring).
   - Add a section function in `builder.py` that returns plain, rounded values
     (`rounded(value, kind)`) and a `type` the template already handles
     (`kpis`, `breakdown`, `top`, `placeholder`), then register it in
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
9. Run `ruff format . && ruff check --fix .` and `pytest`, then commit.
