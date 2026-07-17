BEGIN;

SET search_path TO nino_data, public;

-- Deterministic synthetic data. No row is copied from a company database.
WITH demo_orders AS (
    SELECT
        i,
        'DEMO-202607-' || lpad(i::text, 3, '0') AS order_serial_id,
        CASE
            WHEN i <= 30 THEN (200 + i * 25)::numeric(30, 10)
            WHEN i = 31 THEN 1000::numeric(30, 10)
            WHEN i = 32 THEN 500::numeric(30, 10)
            WHEN i = 33 THEN 1000::numeric(30, 10)
            WHEN i = 34 THEN 800::numeric(30, 10)
            WHEN i = 35 THEN 900::numeric(30, 10)
            WHEN i = 36 THEN 700::numeric(30, 10)
            WHEN i = 37 THEN 300::numeric(30, 10)
            WHEN i = 38 THEN 600::numeric(30, 10)
            WHEN i = 39 THEN 400::numeric(30, 10)
            ELSE 250::numeric(30, 10)
        END AS sale_amount
    FROM generate_series(1, 40) AS source(i)
)
INSERT INTO order_info (
    order_serial_id, row_guid, receipt_no, ref_order_serial_id, customer_serial_id,
    data_serial_id, sale_type, channel, order_type, project_source, order_scene,
    order_create_time, sale_amount, sale_currency_amount, receive_company, is_test,
    environment, member_id, currency_type, exchange_rate, order_sub_type,
    "CreateTime", "UpdateTime", distribution_channel, distribution_order_serial_id,
    sale_ref_id, main_product_type, depart_date, order_source, channel_sub_detail,
    back_date, customer_settle_period, customer_settle_mode, customer_name,
    customer_type, customer_id, is_create_calculation, customer_settle_date
)
SELECT
    d.order_serial_id,
    'ORD-GUID-' || lpad(d.i::text, 3, '0'),
    'CO-DEMO-' || lpad(d.i::text, 3, '0'),
    CASE WHEN d.i IN (11, 22, 33) THEN 'DEMO-202607-001' ELSE '' END,
    'CUS-ORDER-' || lpad(d.i::text, 3, '0'),
    'FIN-DEMO-' || lpad(d.i::text, 3, '0'),
    CASE d.i % 3 WHEN 1 THEN 'B2C' WHEN 2 THEN 'B2B' ELSE 'DISTRIBUTION' END,
    CASE d.i % 3 WHEN 1 THEN 'app' WHEN 2 THEN 'web' ELSE 'corporate' END,
    CASE d.i % 3 WHEN 1 THEN 1 WHEN 2 THEN 2 ELSE 3 END,
    1,
    CASE WHEN d.i IN (11, 22, 33) THEN 2 ELSE 0 END,
    timestamp '2026-07-01 09:00:00'
        + ((d.i - 1) % 28) * interval '1 day'
        + (d.i % 8) * interval '1 hour',
    d.sale_amount,
    d.sale_amount,
    0,
    CASE WHEN d.i = 35 THEN 1 ELSE 0 END,
    'prod',
    900000 + d.i,
    'CNY',
    1,
    CASE WHEN d.i % 3 = 0 THEN 2 ELSE 0 END,
    timestamp '2026-07-01 09:00:00'
        + ((d.i - 1) % 28) * interval '1 day'
        + (d.i % 8) * interval '1 hour',
    timestamp '2026-07-01 09:05:00'
        + ((d.i - 1) % 28) * interval '1 day'
        + (d.i % 8) * interval '1 hour',
    CASE WHEN d.i % 3 = 0 THEN 'demo-distributor' ELSE '' END,
    CASE WHEN d.i % 3 = 0 THEN 'DIST-' || lpad(d.i::text, 3, '0') ELSE '' END,
    CASE d.i % 3 WHEN 1 THEN 'APP_DIRECT' WHEN 2 THEN 'WEB_DIRECT' ELSE 'CORP_CHANNEL' END,
    CASE d.i % 3 WHEN 1 THEN 'AIR_TICKET' WHEN 2 THEN 'TRAIN_TICKET' ELSE 'CAR_SERVICE' END,
    to_char(date '2026-07-01' + ((d.i + 3) % 28), 'YYYY-MM-DD'),
    CASE d.i % 3 WHEN 1 THEN 'DOMESTIC_FLIGHT' WHEN 2 THEN 'TRAIN' ELSE 'CAR' END,
    CASE d.i % 3 WHEN 1 THEN 'native-app' WHEN 2 THEN 'h5' ELSE 'enterprise' END,
    '',
    'MONTHLY',
    'AUTO',
    'Demo Customer ' || lpad(d.i::text, 2, '0'),
    CASE WHEN d.i % 3 = 0 THEN 'enterprise' ELSE 'individual' END,
    'DEMO-CUSTOMER-' || lpad(d.i::text, 3, '0'),
    1,
    '2026-08-05'
FROM demo_orders d
ON CONFLICT (row_guid) DO NOTHING;

-- One customer resource for orders 1-32 and 34-40. Order 33 has two resources below.
WITH resource_orders AS (
    SELECT i
    FROM generate_series(1, 40) AS source(i)
    WHERE i <> 33
), amounts AS (
    SELECT
        i,
        CASE
            WHEN i <= 30 THEN (200 + i * 25)::numeric(30, 10)
            WHEN i = 31 THEN 1000::numeric(30, 10)
            WHEN i = 32 THEN 500::numeric(30, 10)
            WHEN i = 34 THEN 800::numeric(30, 10)
            WHEN i = 35 THEN 900::numeric(30, 10)
            WHEN i = 36 THEN 700::numeric(30, 10)
            WHEN i = 37 THEN 300::numeric(30, 10)
            WHEN i = 38 THEN 600::numeric(30, 10)
            WHEN i = 39 THEN 400::numeric(30, 10)
            ELSE 250::numeric(30, 10)
        END AS sale_amount
    FROM resource_orders
)
INSERT INTO customer_resource_info (
    row_guid, order_serial_id, resource_type, gold_toad_resource_type, business_guid,
    resource_count, resource_company_id, sale_amount, flight_price, build_fee, fue_tax,
    receipt_no, "CreateTime", "UpdateTime", extend_info, currency, exchange_rate,
    sale_currency_amount, resource_state, back_date, revenue_date, activity_code,
    packge_resource_type
)
SELECT
    'CR-GUID-' || lpad(a.i::text, 3, '0'),
    'DEMO-202607-' || lpad(a.i::text, 3, '0'),
    CASE a.i % 3 WHEN 1 THEN 1001 WHEN 2 THEN 2001 ELSE 3001 END,
    CASE a.i % 3 WHEN 1 THEN 'GT-AIR' WHEN 2 THEN 'GT-TRAIN' ELSE 'GT-CAR' END,
    'BUSINESS-CR-' || lpad(a.i::text, 3, '0'),
    1,
    0,
    a.sale_amount,
    CASE WHEN a.i % 3 = 1 THEN greatest(a.sale_amount - 100, 0) ELSE 0 END,
    CASE WHEN a.i % 3 = 1 THEN 50 ELSE 0 END,
    CASE WHEN a.i % 3 = 1 THEN 50 ELSE 0 END,
    'CO-DEMO-' || lpad(a.i::text, 3, '0'),
    timestamp '2026-07-01 09:00:00' + ((a.i - 1) % 28) * interval '1 day',
    timestamp '2026-07-01 09:05:00' + ((a.i - 1) % 28) * interval '1 day',
    json_build_object('demo', true, 'source', 'seed')::text,
    'CNY',
    1,
    a.sale_amount,
    CASE WHEN a.i IN (7, 14, 21, 28, 31, 32, 34, 38, 39) THEN 5 ELSE 0 END,
    '',
    to_char(date '2026-07-01' + ((a.i - 1) % 28), 'YYYY-MM-DD'),
    '',
    0
FROM amounts a
ON CONFLICT (row_guid) DO NOTHING;

INSERT INTO customer_resource_info (
    row_guid, order_serial_id, resource_type, gold_toad_resource_type, business_guid,
    resource_count, resource_company_id, sale_amount, flight_price, build_fee, fue_tax,
    receipt_no, "CreateTime", "UpdateTime", extend_info, currency, exchange_rate,
    sale_currency_amount, resource_state, back_date, revenue_date, activity_code,
    packge_resource_type
) VALUES
    ('CR-GUID-033-A', 'DEMO-202607-033', 3001, 'GT-CAR', 'BUSINESS-CR-033-A', 1, 0,
     600, 0, 0, 0, 'CO-DEMO-033', timestamp '2026-07-05 09:00:00', timestamp '2026-07-05 09:05:00',
     '{"demo":true,"component":"car"}', 'CNY', 1, 600, 0, '', '2026-07-05', '', 0),
    ('CR-GUID-033-B', 'DEMO-202607-033', 9001, 'GT-INSURANCE', 'BUSINESS-CR-033-B', 1, 0,
     400, 0, 0, 0, 'CO-DEMO-033', timestamp '2026-07-05 09:00:00', timestamp '2026-07-05 09:05:00',
     '{"demo":true,"component":"insurance"}', 'CNY', 1, 400, 0, '', '2026-07-05', '', 3001)
ON CONFLICT (row_guid) DO NOTHING;

-- Positive supplier settlement resources.
WITH base_costs AS (
    SELECT
        i,
        CASE
            WHEN i <= 30 THEN (200 + i * 25 - (50 + (i % 5) * 10))::numeric(30, 10)
            WHEN i = 31 THEN 800::numeric(30, 10)
            WHEN i = 32 THEN 450::numeric(30, 10)
            WHEN i = 34 THEN 700::numeric(30, 10)
            WHEN i = 35 THEN 760::numeric(30, 10)
            WHEN i = 36 THEN 580::numeric(30, 10)
            WHEN i = 37 THEN 350::numeric(30, 10)
            WHEN i = 38 THEN 500::numeric(30, 10)
            WHEN i = 39 THEN 380::numeric(30, 10)
            ELSE 300::numeric(30, 10)
        END AS contract_amount
    FROM generate_series(1, 40) AS source(i)
    WHERE i <> 33
)
INSERT INTO supplier_resource_info (
    order_serial_id, row_guid, resource_type, gold_toad_resource_type, business_guid,
    resource_count, contract_amount, settle_period, settle_date, settle_mode, complete_date,
    electronic_ticket_no, payment_type, pnr, ticket_out_state, airways_code, airways_name,
    merchant_id, out_project_serial_id, supplier_trade_no, receipt_no, "CreateTime",
    "UpdateTime", extend_info, currency, exchange_rate, contract_currency_amount,
    resource_state
)
SELECT
    'DEMO-202607-' || lpad(b.i::text, 3, '0'),
    'SR-GUID-' || lpad(b.i::text, 3, '0'),
    CASE b.i % 3 WHEN 1 THEN 1001 WHEN 2 THEN 2001 ELSE 3001 END,
    CASE b.i % 3 WHEN 1 THEN 'GT-AIR' WHEN 2 THEN 'GT-TRAIN' ELSE 'GT-CAR' END,
    'BUSINESS-SR-' || lpad(b.i::text, 3, '0'),
    1,
    b.contract_amount,
    'T+1',
    to_char(date '2026-07-02' + ((b.i - 1) % 28), 'YYYY-MM-DD'),
    'AUTO',
    timestamp '2026-07-02 12:00:00' + ((b.i - 1) % 28) * interval '1 day',
    CASE WHEN b.i % 3 = 1 THEN 'DEMO-TKT-' || lpad(b.i::text, 6, '0') ELSE '' END,
    'BANK_TRANSFER',
    CASE WHEN b.i % 3 = 1 THEN 'PNR' || lpad(b.i::text, 3, '0') ELSE '' END,
    1,
    CASE WHEN b.i % 3 = 1 THEN 'DM' ELSE '' END,
    CASE b.i % 3 WHEN 1 THEN 'Demo Airline' WHEN 2 THEN 'Demo Railway' ELSE 'Demo Mobility' END,
    'DEMO-MERCHANT-' || (b.i % 3 + 1),
    'OUT-DEMO-' || lpad(b.i::text, 3, '0'),
    'SUPPLIER-TRADE-' || lpad(b.i::text, 3, '0'),
    'SO-DEMO-' || lpad(b.i::text, 3, '0'),
    timestamp '2026-07-01 10:00:00' + ((b.i - 1) % 28) * interval '1 day',
    timestamp '2026-07-02 12:00:00' + ((b.i - 1) % 28) * interval '1 day',
    json_build_object('demo', true, 'direction', 'settlement')::text,
    'CNY',
    1,
    b.contract_amount,
    0
FROM base_costs b
ON CONFLICT (row_guid) DO NOTHING;

INSERT INTO supplier_resource_info (
    order_serial_id, row_guid, resource_type, gold_toad_resource_type, business_guid,
    resource_count, contract_amount, settle_period, settle_date, settle_mode, complete_date,
    electronic_ticket_no, payment_type, pnr, ticket_out_state, airways_code, airways_name,
    merchant_id, out_project_serial_id, supplier_trade_no, receipt_no, "CreateTime",
    "UpdateTime", extend_info, currency, exchange_rate, contract_currency_amount,
    resource_state
) VALUES
    ('DEMO-202607-033', 'SR-GUID-033-A', 3001, 'GT-CAR', 'BUSINESS-SR-033-A', 1, 500,
     'T+1', '2026-07-06', 'AUTO', timestamp '2026-07-06 12:00:00', '', 'BANK_TRANSFER', '', 1, '',
     'Demo Mobility', 'DEMO-MERCHANT-1', 'OUT-DEMO-033-A', 'SUPPLIER-TRADE-033-A', 'SO-DEMO-033',
     timestamp '2026-07-05 10:00:00', timestamp '2026-07-06 12:00:00', '{"demo":true}', 'CNY', 1, 500, 0),
    ('DEMO-202607-033', 'SR-GUID-033-B', 9001, 'GT-INSURANCE', 'BUSINESS-SR-033-B', 1, 320,
     'T+1', '2026-07-06', 'AUTO', timestamp '2026-07-06 12:00:00', '', 'BANK_TRANSFER', '', 1, '',
     'Demo Insurance', 'DEMO-MERCHANT-2', 'OUT-DEMO-033-B', 'SUPPLIER-TRADE-033-B', 'SO-DEMO-033',
     timestamp '2026-07-05 10:00:00', timestamp '2026-07-06 12:00:00', '{"demo":true}', 'CNY', 1, 320, 0)
ON CONFLICT (row_guid) DO NOTHING;

-- Negative supplier resources model successful supplier-side refunds.
WITH supplier_refunds(i, amount) AS (
    VALUES (7, -80::numeric), (14, -80), (21, -80), (28, -80),
           (31, -300), (34, -700), (38, -100)
)
INSERT INTO supplier_resource_info (
    order_serial_id, row_guid, resource_type, gold_toad_resource_type, business_guid,
    resource_count, contract_amount, settle_period, settle_date, settle_mode, complete_date,
    electronic_ticket_no, payment_type, pnr, ticket_out_state, airways_code, airways_name,
    merchant_id, out_project_serial_id, supplier_trade_no, receipt_no, "CreateTime",
    "UpdateTime", extend_info, currency, exchange_rate, contract_currency_amount,
    resource_state
)
SELECT
    'DEMO-202607-' || lpad(r.i::text, 3, '0'),
    'SR-REFUND-GUID-' || lpad(r.i::text, 3, '0'),
    CASE r.i % 3 WHEN 1 THEN 1001 WHEN 2 THEN 2001 ELSE 3001 END,
    'GT-SUPPLIER-REFUND',
    'BUSINESS-SR-REFUND-' || lpad(r.i::text, 3, '0'),
    -1,
    r.amount,
    'T+1',
    '2026-07-30',
    'AUTO',
    timestamp '2026-07-30 12:00:00',
    '', 'BANK_TRANSFER', '', 1, '', 'Demo Refunding Supplier',
    'DEMO-MERCHANT-REFUND',
    'OUT-REFUND-' || lpad(r.i::text, 3, '0'),
    'SUPPLIER-REFUND-' || lpad(r.i::text, 3, '0'),
    'SRO-DEMO-' || lpad(r.i::text, 3, '0'),
    timestamp '2026-07-29 10:00:00',
    timestamp '2026-07-30 12:00:00',
    json_build_object('demo', true, 'direction', 'supplier_refund')::text,
    'CNY', 1, r.amount, 99
FROM supplier_refunds r
ON CONFLICT (row_guid) DO NOTHING;

-- A pay_info row represents a completed payment in this five-table MVP.
INSERT INTO pay_info (
    order_serial_id, row_guid, pay_type, pay_type_description, trade_no,
    transfer_trade_no, receipt_no, amount, exchange_rate, currency, pay_channel_id,
    pay_product_id, "CreateTime", "UpdateTime", pay_channel_code, pay_product_code,
    pay_project_code, pay_company_id, order_type, paid_currency_amount,
    paid_currency_type
)
SELECT
    o.order_serial_id,
    'PAY-GUID-' || right(o.order_serial_id, 3),
    1,
    'Demo online payment',
    'TRADE-DEMO-' || right(o.order_serial_id, 3),
    '',
    o.receipt_no,
    o.sale_amount,
    o.exchange_rate,
    o.currency_type,
    100,
    1001,
    o.order_create_time + interval '5 minutes',
    o.order_create_time + interval '10 minutes',
    'DEMO_CHANNEL',
    'DEMO_PRODUCT',
    'NINO_DATA',
    o.receive_company::text,
    o.order_type,
    o.sale_currency_amount,
    o.currency_type
FROM order_info o
WHERE o.order_serial_id <> 'DEMO-202607-036'
ON CONFLICT (row_guid) DO NOTHING;

-- RefundCoreRefundStatusEnum.RefundSucceed = 2.
WITH refund_cases(i, refund_amount, supplier_refund, reason) AS (
    VALUES
        (7,  100::numeric,  80::numeric, 'Customer changed itinerary'),
        (14, 100,           80,          'Customer changed itinerary'),
        (21, 100,           80,          'Customer changed itinerary'),
        (28, 100,           80,          'Customer changed itinerary'),
        (31, 400,          300,          'Partial itinerary refund'),
        (32, 500,            0,          'Supplier refund not received'),
        (34, 800,          700,          'Full customer and supplier refund'),
        (38, 300,          100,          'Partial supplier recovery'),
        (39, 200,            0,          'Supplier rejected refund')
)
INSERT INTO refund_info (
    order_serial_id, row_guid, refund_no, refund_amount, receipt_no, refund_status,
    trade_no, refund_trade_no, notify_info, refund_type, "CreateTime", "UpdateTime",
    order_serial_id_of_trade_no, refund_category, refund_reason,
    order_serial_id_of_refunded, refund_callback_reason, refund_request_time,
    refund_receive_account, refund_arrive_date, refund_channel_code,
    refund_product_code, refund_finish_time, refund_company_id, order_type,
    project_source, extend_info, business_apply_refund_amount,
    business_apply_refund_currency_type
)
SELECT
    'DEMO-202607-' || lpad(r.i::text, 3, '0'),
    'REFUND-GUID-' || lpad(r.i::text, 3, '0'),
    'REFUND-DEMO-' || lpad(r.i::text, 3, '0'),
    r.refund_amount,
    'RO-DEMO-' || lpad(r.i::text, 3, '0'),
    2,
    'TRADE-DEMO-' || lpad(r.i::text, 3, '0'),
    'REFUND-TRADE-DEMO-' || lpad(r.i::text, 3, '0'),
    json_build_object('success', true, 'demo', true)::text,
    1,
    timestamp '2026-07-29 10:00:00' + (r.i % 8) * interval '1 hour',
    timestamp '2026-07-30 10:00:00' + (r.i % 8) * interval '1 hour',
    'DEMO-202607-' || lpad(r.i::text, 3, '0'),
    1,
    r.reason,
    'DEMO-202607-' || lpad(r.i::text, 3, '0'),
    'Mock refund completed',
    '2026-07-29 10:00:00',
    'Original payment account',
    '2026-07-30',
    'DEMO_CHANNEL',
    'DEMO_PRODUCT',
    '2026-07-30 10:00:00',
    '0',
    CASE r.i % 3 WHEN 1 THEN 1 WHEN 2 THEN 2 ELSE 3 END,
    1,
    json_build_object('demo', true, 'supplier_refund_amount', r.supplier_refund)::text,
    r.refund_amount,
    'CNY'
FROM refund_cases r
ON CONFLICT (row_guid) DO NOTHING;

COMMIT;
