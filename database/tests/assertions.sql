\set ON_ERROR_STOP on

SET search_path TO nino_data, public;

DO $$
DECLARE
    actual integer;
BEGIN
    SELECT COUNT(*) INTO actual FROM order_info;
    IF actual <> 40 THEN RAISE EXCEPTION 'Expected 40 order_info rows, got %', actual; END IF;

    SELECT COUNT(*) INTO actual FROM customer_resource_info;
    IF actual <> 41 THEN RAISE EXCEPTION 'Expected 41 customer_resource_info rows, got %', actual; END IF;

    SELECT COUNT(*) INTO actual FROM supplier_resource_info;
    IF actual <> 48 THEN RAISE EXCEPTION 'Expected 48 supplier_resource_info rows, got %', actual; END IF;

    SELECT COUNT(*) INTO actual FROM pay_info;
    IF actual <> 39 THEN RAISE EXCEPTION 'Expected 39 pay_info rows, got %', actual; END IF;

    SELECT COUNT(*) INTO actual FROM refund_info;
    IF actual <> 9 THEN RAISE EXCEPTION 'Expected 9 refund_info rows, got %', actual; END IF;

    SELECT COUNT(*) INTO actual
    FROM order_info o
    WHERE o.is_test = 0
      AND EXISTS (SELECT 1 FROM pay_info p WHERE p.order_serial_id = o.order_serial_id);
    IF actual <> 38 THEN RAISE EXCEPTION 'Expected 38 effective paid orders, got %', actual; END IF;
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
    IF actual <> 5 THEN RAISE EXCEPTION 'Expected 5 negative-margin orders, got %', actual; END IF;
END $$;

SELECT 'All Nino Data database assertions passed.' AS result;
