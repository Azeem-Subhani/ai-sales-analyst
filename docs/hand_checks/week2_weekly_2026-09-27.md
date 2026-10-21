# Hand check: weekly sales review, week ending 2026-09-27

- **Purpose:** check the weekly report's headline numbers with plain SQL that does not
  use `src/analytics/kpis.sql`, so a mistake in the KPI code cannot hide itself.
- **Scope:** ISO week 39, Monday 2026-09-21 to Sunday 2026-09-27, seed-42 data
  (`python -m src.data.generate`). Definitions: `docs/decisions.md`, Week 2 section.
- **Report checked:** `outputs/reports/weekly_sales_review_2026-09-27/report.html`, built with
  `python -m src.reports.builder --report weekly_sales_review --as-of 2026-09-27`.
- **Report values below were copied from that build (2026-10-21).** All six queries were run
  against the same database on that date and returned the same values.

## How to run the queries

Open the database read-only, either with the DuckDB CLI:

```bash
duckdb -readonly data/sales.duckdb
```

or from a Python prompt in the project's venv (no extra install):

```python
import duckdb
con = duckdb.connect("data/sales.duckdb", read_only=True)
con.sql("""<paste a query here>""").show()
```

Line value = `quantity * unit_price * (1 - discount_pct)`; only `completed` orders count.

## 1. Net revenue

```sql
SELECT ROUND(SUM(oi.quantity * oi.unit_price * (1 - oi.discount_pct)), 2) AS net_revenue
FROM order_items oi
JOIN orders o USING (order_id)
WHERE o.status = 'completed'
  AND o.order_date BETWEEN DATE '2026-09-21' AND DATE '2026-09-27';
```

Report shows: **114,286.82**

## 2. Orders

```sql
SELECT COUNT(*) AS orders
FROM orders o
WHERE o.status = 'completed'
  AND o.order_date BETWEEN DATE '2026-09-21' AND DATE '2026-09-27';
```

Report shows: **661**

## 3. Average order value

```sql
SELECT ROUND(SUM(oi.quantity * oi.unit_price * (1 - oi.discount_pct))
             / COUNT(DISTINCT o.order_id), 2) AS aov
FROM order_items oi
JOIN orders o USING (order_id)
WHERE o.status = 'completed'
  AND o.order_date BETWEEN DATE '2026-09-21' AND DATE '2026-09-27';
```

Report shows: **172.90**

## 4. Gross margin %

```sql
SELECT ROUND(100 * (SUM(oi.quantity * oi.unit_price * (1 - oi.discount_pct))
                    - SUM(oi.quantity * p.unit_cost))
             / SUM(oi.quantity * oi.unit_price * (1 - oi.discount_pct)), 1) AS gross_margin_pct
FROM order_items oi
JOIN orders o USING (order_id)
JOIN products p USING (product_id)
WHERE o.status = 'completed'
  AND o.order_date BETWEEN DATE '2026-09-21' AND DATE '2026-09-27';
```

Report shows: **43.1%**

## 5. Top category

```sql
SELECT p.category,
       ROUND(SUM(oi.quantity * oi.unit_price * (1 - oi.discount_pct)), 2) AS net_revenue
FROM order_items oi
JOIN orders o USING (order_id)
JOIN products p USING (product_id)
WHERE o.status = 'completed'
  AND o.order_date BETWEEN DATE '2026-09-21' AND DATE '2026-09-27'
GROUP BY p.category
ORDER BY net_revenue DESC
LIMIT 1;
```

Report shows: **Electronics, 31,481.36** (first row of "Net revenue by category")

## 6. Top customer

```sql
SELECT c.name,
       ROUND(SUM(oi.quantity * oi.unit_price * (1 - oi.discount_pct)), 2) AS net_revenue
FROM order_items oi
JOIN orders o USING (order_id)
JOIN customers c USING (customer_id)
WHERE o.status = 'completed'
  AND o.order_date BETWEEN DATE '2026-09-21' AND DATE '2026-09-27'
GROUP BY c.name
ORDER BY net_revenue DESC
LIMIT 1;
```

Report shows: **Stokes, Robinson and Gough, 2,767.45** (first row of "Top 10 customers")

## Sign-off

| Check | Matches? | Checked by | Date |
| --- | --- | --- | --- |
| 1 to 6 | | | |
