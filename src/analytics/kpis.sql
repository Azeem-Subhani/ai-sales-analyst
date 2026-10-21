-- KPI logic (plan section 7, clarified in docs/decisions.md). Every report, dashboard and
-- chat answer uses these queries so the numbers always agree.
--
-- Format: blocks separated by "-- name: <query_name>" lines, loaded by src/analytics/kpis.py.
-- The "order_lines" block is a TEMP VIEW (works on a read-only connection); the others are
-- SELECTs that take DuckDB named parameters $start and $end (inclusive dates).
-- Line value = quantity * unit_price * (1 - discount_pct), using the order_items price.

-- name: order_lines
CREATE OR REPLACE TEMP VIEW order_lines AS
SELECT
    o.order_id,
    o.order_date,
    o.status,
    o.channel,
    o.customer_id,
    c.name AS customer_name,
    c.customer_type,
    c.region,
    p.product_id,
    p.sku,
    p.name AS product_name,
    p.category,
    oi.quantity,
    oi.quantity * oi.unit_price * (1 - oi.discount_pct) AS line_value,
    oi.quantity * p.unit_cost AS line_cost
FROM order_items AS oi
JOIN orders AS o USING (order_id)
JOIN customers AS c ON c.customer_id = o.customer_id
JOIN products AS p ON p.product_id = oi.product_id;

-- name: kpi_summary
-- One row of headline KPIs. Order counts come from the orders table so an order without
-- lines would still be counted; money and units come from order_lines.
WITH money AS (
    SELECT
        SUM(line_value) FILTER (WHERE status = 'completed') AS net_revenue,
        SUM(line_cost) FILTER (WHERE status = 'completed') AS cost_of_goods,
        SUM(quantity) FILTER (WHERE status = 'completed') AS units,
        SUM(line_value) FILTER (WHERE status = 'returned') AS returns_value
    FROM order_lines
    WHERE order_date BETWEEN $start AND $end
),
counts AS (
    SELECT
        COUNT(*) AS all_orders,
        COUNT(*) FILTER (WHERE status = 'completed') AS orders,
        COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled_orders,
        COUNT(*) FILTER (WHERE status = 'returned') AS returned_orders
    FROM orders
    WHERE order_date BETWEEN $start AND $end
)
SELECT
    CAST(COALESCE(m.net_revenue, 0) AS DOUBLE) AS net_revenue,
    c.orders,
    CAST(m.net_revenue / NULLIF(c.orders, 0) AS DOUBLE) AS aov,
    COALESCE(m.units, 0) AS units,
    CAST(COALESCE(m.cost_of_goods, 0) AS DOUBLE) AS cost_of_goods,
    CAST((m.net_revenue - m.cost_of_goods) / NULLIF(m.net_revenue, 0) AS DOUBLE)
        AS gross_margin_pct,
    CAST(COALESCE(m.returns_value, 0) AS DOUBLE) AS returns_value,
    c.all_orders,
    c.cancelled_orders,
    c.returned_orders,
    c.cancelled_orders / NULLIF(c.all_orders, 0) AS cancellation_rate,
    c.returned_orders / NULLIF(c.orders + c.returned_orders, 0) AS return_rate
FROM money AS m, counts AS c;

-- name: new_returning
-- New = the customer's first-ever non-cancelled order falls in the period.
-- Population = customers with at least one non-cancelled order in the period.
WITH first_orders AS (
    SELECT customer_id, MIN(order_date) AS first_order_date
    FROM orders
    WHERE status <> 'cancelled'
    GROUP BY customer_id
),
active AS (
    SELECT DISTINCT customer_id
    FROM orders
    WHERE status <> 'cancelled' AND order_date BETWEEN $start AND $end
)
SELECT
    COUNT(*) FILTER (WHERE f.first_order_date >= $start) AS new_customers,
    COUNT(*) FILTER (WHERE f.first_order_date < $start) AS returning_customers
FROM active AS a
JOIN first_orders AS f USING (customer_id);

-- name: breakdowns
-- Net revenue (completed orders) by category, region, channel and customer type in one
-- pass. "dimension" says which grouping a row belongs to; "share" is within that grouping.
SELECT
    CASE
        WHEN GROUPING(category) = 0 THEN 'category'
        WHEN GROUPING(region) = 0 THEN 'region'
        WHEN GROUPING(channel) = 0 THEN 'channel'
        ELSE 'customer_type'
    END AS dimension,
    COALESCE(category, region, channel, customer_type) AS value,
    CAST(SUM(line_value) AS DOUBLE) AS net_revenue,
    CAST(SUM(line_cost) AS DOUBLE) AS cost_of_goods,
    CAST((SUM(line_value) - SUM(line_cost)) / NULLIF(SUM(line_value), 0) AS DOUBLE)
        AS gross_margin_pct,
    SUM(quantity) AS units,
    COUNT(DISTINCT order_id) AS orders
FROM order_lines
WHERE status = 'completed' AND order_date BETWEEN $start AND $end
GROUP BY GROUPING SETS ((category), (region), (channel), (customer_type));

-- name: revenue_by_product
SELECT
    product_id,
    sku,
    product_name,
    category,
    CAST(SUM(line_value) AS DOUBLE) AS net_revenue,
    SUM(quantity) AS units
FROM order_lines
WHERE status = 'completed' AND order_date BETWEEN $start AND $end
GROUP BY product_id, sku, product_name, category;

-- name: revenue_by_customer
SELECT
    customer_id,
    customer_name,
    customer_type,
    CAST(SUM(line_value) AS DOUBLE) AS net_revenue,
    COUNT(DISTINCT order_id) AS orders
FROM order_lines
WHERE status = 'completed' AND order_date BETWEEN $start AND $end
GROUP BY customer_id, customer_name, customer_type;

-- name: revenue_by_week
-- Net revenue per Monday-starting week.
SELECT
    CAST(date_trunc('week', order_date) AS DATE) AS bucket_start,
    CAST(SUM(line_value) AS DOUBLE) AS net_revenue
FROM order_lines
WHERE status = 'completed' AND order_date BETWEEN $start AND $end
GROUP BY 1
ORDER BY 1;

-- name: revenue_by_month
SELECT
    CAST(date_trunc('month', order_date) AS DATE) AS bucket_start,
    CAST(SUM(line_value) AS DOUBLE) AS net_revenue
FROM order_lines
WHERE status = 'completed' AND order_date BETWEEN $start AND $end
GROUP BY 1
ORDER BY 1;

-- name: last_order_date
SELECT MAX(order_date) AS last_order_date FROM orders;
