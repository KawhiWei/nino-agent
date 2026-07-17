\set ON_ERROR_STOP on
\pset pager off

SET search_path TO nino_data, public;

-- 1. Full order overview using all five Nino Data tables.
\set order_serial_id 'DEMO-202607-001'

WITH customer_totals AS (
    SELECT order_serial_id, SUM(sale_amount) AS customer_sale_amount
    FROM customer_resource_info
    GROUP BY order_serial_id
), supplier_totals AS (
    SELECT order_serial_id, SUM(contract_amount) AS net_supplier_cost
    FROM supplier_resource_info
    GROUP BY order_serial_id
), pay_totals AS (
    SELECT order_serial_id, SUM(amount) AS paid_amount
    FROM pay_info
    GROUP BY order_serial_id
), refund_totals AS (
    SELECT order_serial_id, SUM(refund_amount) AS successful_refund_amount
    FROM refund_info
    WHERE refund_status = 2
    GROUP BY order_serial_id
)
SELECT
    o.order_serial_id,
    o.main_product_type,
    o.channel,
    o.order_create_time,
    o.currency_type,
    o.sale_amount AS order_sale_amount,
    COALESCE(c.customer_sale_amount, 0) AS customer_sale_amount,
    COALESCE(s.net_supplier_cost, 0) AS net_supplier_cost,
    COALESCE(p.paid_amount, 0) AS paid_amount,
    COALESCE(r.successful_refund_amount, 0) AS successful_refund_amount,
    COALESCE(c.customer_sale_amount, 0)
        - COALESCE(s.net_supplier_cost, 0)
        - COALESCE(r.successful_refund_amount, 0) AS demo_gross_margin
FROM order_info o
LEFT JOIN customer_totals c USING (order_serial_id)
LEFT JOIN supplier_totals s USING (order_serial_id)
LEFT JOIN pay_totals p USING (order_serial_id)
LEFT JOIN refund_totals r USING (order_serial_id)
WHERE o.order_serial_id = :'order_serial_id';

-- 2. July summary. A pay_info row means the order has completed payment in this MVP.
WITH effective_orders AS (
    SELECT o.*
    FROM order_info o
    WHERE o.order_create_time >= timestamp '2026-07-01 00:00:00'
      AND o.order_create_time <  timestamp '2026-08-01 00:00:00'
      AND o.is_test = 0
      AND EXISTS (
          SELECT 1 FROM pay_info p WHERE p.order_serial_id = o.order_serial_id
      )
), customer_totals AS (
    SELECT order_serial_id, SUM(sale_amount) AS customer_sale_amount
    FROM customer_resource_info
    GROUP BY order_serial_id
), supplier_totals AS (
    SELECT order_serial_id, SUM(contract_amount) AS net_supplier_cost
    FROM supplier_resource_info
    GROUP BY order_serial_id
), pay_totals AS (
    SELECT order_serial_id, SUM(amount) AS paid_amount
    FROM pay_info
    GROUP BY order_serial_id
), refund_totals AS (
    SELECT order_serial_id, SUM(refund_amount) AS successful_refund_amount
    FROM refund_info
    WHERE refund_status = 2
    GROUP BY order_serial_id
)
SELECT
    o.main_product_type,
    COUNT(*) AS order_count,
    SUM(COALESCE(c.customer_sale_amount, 0)) AS customer_sale_amount,
    SUM(COALESCE(s.net_supplier_cost, 0)) AS net_supplier_cost,
    SUM(COALESCE(p.paid_amount, 0)) AS paid_amount,
    SUM(COALESCE(r.successful_refund_amount, 0)) AS successful_refund_amount,
    SUM(
        COALESCE(c.customer_sale_amount, 0)
        - COALESCE(s.net_supplier_cost, 0)
        - COALESCE(r.successful_refund_amount, 0)
    ) AS demo_gross_margin
FROM effective_orders o
LEFT JOIN customer_totals c USING (order_serial_id)
LEFT JOIN supplier_totals s USING (order_serial_id)
LEFT JOIN pay_totals p USING (order_serial_id)
LEFT JOIN refund_totals r USING (order_serial_id)
GROUP BY o.main_product_type
ORDER BY o.main_product_type;

-- 3. Five lowest-margin paid, non-test orders.
WITH customer_totals AS (
    SELECT order_serial_id, SUM(sale_amount) AS customer_sale_amount
    FROM customer_resource_info
    GROUP BY order_serial_id
), supplier_totals AS (
    SELECT order_serial_id, SUM(contract_amount) AS net_supplier_cost
    FROM supplier_resource_info
    GROUP BY order_serial_id
), refund_totals AS (
    SELECT order_serial_id, SUM(refund_amount) AS successful_refund_amount
    FROM refund_info
    WHERE refund_status = 2
    GROUP BY order_serial_id
)
SELECT
    o.order_serial_id,
    o.main_product_type,
    COALESCE(c.customer_sale_amount, 0) AS customer_sale_amount,
    COALESCE(s.net_supplier_cost, 0) AS net_supplier_cost,
    COALESCE(r.successful_refund_amount, 0) AS successful_refund_amount,
    COALESCE(c.customer_sale_amount, 0)
        - COALESCE(s.net_supplier_cost, 0)
        - COALESCE(r.successful_refund_amount, 0) AS demo_gross_margin
FROM order_info o
LEFT JOIN customer_totals c USING (order_serial_id)
LEFT JOIN supplier_totals s USING (order_serial_id)
LEFT JOIN refund_totals r USING (order_serial_id)
WHERE o.order_create_time >= timestamp '2026-07-01 00:00:00'
  AND o.order_create_time <  timestamp '2026-08-01 00:00:00'
  AND o.is_test = 0
  AND EXISTS (
      SELECT 1 FROM pay_info p WHERE p.order_serial_id = o.order_serial_id
  )
ORDER BY demo_gross_margin ASC, o.order_serial_id ASC
LIMIT 5;
