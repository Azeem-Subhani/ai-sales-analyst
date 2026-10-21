---
name: add-report
description: Use when adding a new scheduled report or changing an existing one (daily, weekly, monthly, or a new report type) in config/reports.yaml, including its queries, email template section and tests.
---

# Add a scheduled report

Reports are config-driven (plan sections 7 and 8). Adding a report should mean
adding a YAML block and reusing existing queries, not writing a new pipeline.

## Checklist

1. **Define it in `config/reports.yaml`.** Add a block with `schedule`
   (APScheduler cron fields), `queries`, `compare_to`, `charts`,
   `attach_excel`, and `recipients`. During the build, recipients list only
   the project owner.
2. **Queries.** Reuse existing named queries in `src/analytics/` first. If a new
   one is needed, put the SQL in `src/analytics/kpis.sql` (or a clearly named
   constant) and expose it through `src/analytics/kpis.py`. All numbers come
   from SQL or pandas, never the LLM.
3. **Template section.** Add the section to
   `src/reports/templates/report.html` (Jinja2). Keep it Gmail-safe: inline
   styles, tables, PNG charts from matplotlib.
4. **Summary input.** Make sure the computed numbers passed to the LLM summary
   (as JSON, via `src/agent/llm.py`) include everything the summary may mention.
5. **Test.** Add a test in `tests/` that builds the report against the mini
   dataset or `data/sales.duckdb` and checks the key numbers and sections.
6. **Dry-run check.** With `REPORT_MODE=dry_run`, build the report and open
   the HTML and Excel files in `outputs/reports/`. Check the numbers by hand
   against a direct query.
7. Run `ruff format . && ruff check --fix .` and `pytest`, then commit.
