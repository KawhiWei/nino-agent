\set ON_ERROR_STOP on

SET search_path TO nino_data, public;

DO $$
DECLARE
    actual integer;
    version text;
BEGIN
    SELECT current_setting('server_version') INTO version;
    IF version NOT LIKE '12.18%' THEN
        RAISE EXCEPTION 'Expected PostgreSQL 12.18, got %', version;
    END IF;

    SELECT COUNT(*) INTO actual FROM order_info;
    IF actual <> 25040 THEN RAISE EXCEPTION 'Expected 25040 order_info rows, got %', actual; END IF;

    SELECT COUNT(*) INTO actual FROM customer_resource_info;
    IF actual <> 27541 THEN RAISE EXCEPTION 'Expected 27541 customer_resource_info rows, got %', actual; END IF;

    SELECT COUNT(*) INTO actual FROM supplier_resource_info;
    IF actual <> 28908 THEN RAISE EXCEPTION 'Expected 28908 supplier_resource_info rows, got %', actual; END IF;

    SELECT COUNT(*) INTO actual FROM pay_info;
    IF actual <> 24364 THEN RAISE EXCEPTION 'Expected 24364 pay_info rows, got %', actual; END IF;

    SELECT COUNT(*) INTO actual FROM refund_info;
    IF actual <> 2059 THEN RAISE EXCEPTION 'Expected 2059 refund_info rows, got %', actual; END IF;

    SELECT COUNT(*) INTO actual
    FROM order_info o
    WHERE o.is_test = 0
      AND EXISTS (SELECT 1 FROM pay_info p WHERE p.order_serial_id = o.order_serial_id);
    IF actual <> 23876 THEN RAISE EXCEPTION 'Expected 23876 effective paid orders, got %', actual; END IF;
END $$;

DO $$
DECLARE
    actual numeric(30, 10);
BEGIN
    WITH totals AS (
        SELECT
            o.order_serial_id,
            COALESCE((SELECT SUM(sale_amount) FROM customer_resource_info WHERE order_serial_id = o.order_serial_id), 0)
            - COALESCE((SELECT SUM(contract_amount) FROM supplier_resource_info WHERE order_serial_id = o.order_serial_id), 0)
            - COALESCE((SELECT SUM(refund_amount) FROM refund_info WHERE order_serial_id = o.order_serial_id AND refund_status = 2), 0)
                AS gross_margin
        FROM order_info o
    )
    SELECT gross_margin INTO actual FROM totals WHERE order_serial_id = 'DEMO-202607-001';
    IF actual <> 60 THEN RAISE EXCEPTION 'Order 001 margin: expected 60, got %', actual; END IF;

    WITH totals AS (
        SELECT
            o.order_serial_id,
            COALESCE((SELECT SUM(sale_amount) FROM customer_resource_info WHERE order_serial_id = o.order_serial_id), 0)
            - COALESCE((SELECT SUM(contract_amount) FROM supplier_resource_info WHERE order_serial_id = o.order_serial_id), 0)
            - COALESCE((SELECT SUM(refund_amount) FROM refund_info WHERE order_serial_id = o.order_serial_id AND refund_status = 2), 0)
                AS gross_margin
        FROM order_info o
    )
    SELECT gross_margin INTO actual FROM totals WHERE order_serial_id = 'DEMO-202607-031';
    IF actual <> 100 THEN RAISE EXCEPTION 'Order 031 margin: expected 100, got %', actual; END IF;

    WITH totals AS (
        SELECT
            o.order_serial_id,
            COALESCE((SELECT SUM(sale_amount) FROM customer_resource_info WHERE order_serial_id = o.order_serial_id), 0)
            - COALESCE((SELECT SUM(contract_amount) FROM supplier_resource_info WHERE order_serial_id = o.order_serial_id), 0)
            - COALESCE((SELECT SUM(refund_amount) FROM refund_info WHERE order_serial_id = o.order_serial_id AND refund_status = 2), 0)
                AS gross_margin
        FROM order_info o
    )
    SELECT gross_margin INTO actual FROM totals WHERE order_serial_id = 'DEMO-202607-032';
    IF actual <> -450 THEN RAISE EXCEPTION 'Order 032 margin: expected -450, got %', actual; END IF;

    WITH totals AS (
        SELECT
            o.order_serial_id,
            COALESCE((SELECT SUM(sale_amount) FROM customer_resource_info WHERE order_serial_id = o.order_serial_id), 0)
            - COALESCE((SELECT SUM(contract_amount) FROM supplier_resource_info WHERE order_serial_id = o.order_serial_id), 0)
            - COALESCE((SELECT SUM(refund_amount) FROM refund_info WHERE order_serial_id = o.order_serial_id AND refund_status = 2), 0)
                AS gross_margin
        FROM order_info o
    )
    SELECT gross_margin INTO actual FROM totals WHERE order_serial_id = 'DEMO-202607-033';
    IF actual <> 180 THEN RAISE EXCEPTION 'Order 033 margin: expected 180, got %', actual; END IF;
END $$;

DO $$
DECLARE
    actual integer;
BEGIN
    WITH totals AS (
        SELECT
            o.order_serial_id,
            COALESCE((SELECT SUM(sale_amount) FROM customer_resource_info WHERE order_serial_id = o.order_serial_id), 0)
            - COALESCE((SELECT SUM(contract_amount) FROM supplier_resource_info WHERE order_serial_id = o.order_serial_id), 0)
            - COALESCE((SELECT SUM(refund_amount) FROM refund_info WHERE order_serial_id = o.order_serial_id AND refund_status = 2), 0)
                AS gross_margin
        FROM order_info o
        WHERE o.is_test = 0
          AND EXISTS (SELECT 1 FROM pay_info p WHERE p.order_serial_id = o.order_serial_id)
    )
    SELECT COUNT(*) INTO actual FROM totals WHERE gross_margin < 0;
    IF actual <> 198 THEN RAISE EXCEPTION 'Expected 198 negative-margin orders, got %', actual; END IF;
END $$;

DO $$
DECLARE
    actual_order_count integer;
    actual_customer_sales numeric(30, 10);
    actual_supplier_cost numeric(30, 10);
    actual_paid_amount numeric(30, 10);
    actual_refund_amount numeric(30, 10);
    actual_gross_margin numeric(30, 10);
    actual_negative_count integer;
BEGIN
    WITH customer_totals AS (
        SELECT order_serial_id, SUM(sale_amount) AS amount
        FROM customer_resource_info GROUP BY order_serial_id
    ), supplier_totals AS (
        SELECT order_serial_id, SUM(contract_amount) AS amount
        FROM supplier_resource_info GROUP BY order_serial_id
    ), pay_totals AS (
        SELECT order_serial_id, SUM(amount) AS amount
        FROM pay_info GROUP BY order_serial_id
    ), refund_totals AS (
        SELECT order_serial_id, SUM(refund_amount) AS amount
        FROM refund_info WHERE refund_status = 2 GROUP BY order_serial_id
    ), july_orders AS (
        SELECT
            COALESCE(c.amount, 0) AS customer_sales,
            COALESCE(s.amount, 0) AS supplier_cost,
            COALESCE(p.amount, 0) AS paid_amount,
            COALESCE(r.amount, 0) AS refund_value,
            COALESCE(c.amount, 0) - COALESCE(s.amount, 0) - COALESCE(r.amount, 0) AS margin_value
        FROM order_info o
        JOIN pay_totals p USING (order_serial_id)
        LEFT JOIN customer_totals c USING (order_serial_id)
        LEFT JOIN supplier_totals s USING (order_serial_id)
        LEFT JOIN refund_totals r USING (order_serial_id)
        WHERE o.order_create_time >= timestamp '2026-07-01 00:00:00'
          AND o.order_create_time < timestamp '2026-08-01 00:00:00'
          AND o.is_test = 0
    )
    SELECT COUNT(*), SUM(customer_sales), SUM(supplier_cost), SUM(paid_amount),
           SUM(refund_value), SUM(margin_value), COUNT(*) FILTER (WHERE margin_value < 0)
    INTO actual_order_count, actual_customer_sales, actual_supplier_cost, actual_paid_amount,
         actual_refund_amount, actual_gross_margin, actual_negative_count
    FROM july_orders;

    IF actual_order_count <> 2042 THEN RAISE EXCEPTION 'July order count: expected 2042, got %', actual_order_count; END IF;
    IF actual_customer_sales <> 2282430 THEN RAISE EXCEPTION 'July sales: expected 2282430, got %', actual_customer_sales; END IF;
    IF actual_supplier_cost <> 1958219 THEN RAISE EXCEPTION 'July supplier cost: expected 1958219, got %', actual_supplier_cost; END IF;
    IF actual_paid_amount <> 2282430 THEN RAISE EXCEPTION 'July paid: expected 2282430, got %', actual_paid_amount; END IF;
    IF actual_refund_amount <> 15770 THEN RAISE EXCEPTION 'July refunds: expected 15770, got %', actual_refund_amount; END IF;
    IF actual_gross_margin <> 308441 THEN RAISE EXCEPTION 'July margin: expected 308441, got %', actual_gross_margin; END IF;
    IF actual_negative_count <> 24 THEN RAISE EXCEPTION 'July negative orders: expected 24, got %', actual_negative_count; END IF;
END $$;

SELECT 'All Nino Data database assertions passed.' AS result;
