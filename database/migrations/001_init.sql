BEGIN;

CREATE SCHEMA IF NOT EXISTS nino_data;
SET search_path TO nino_data, public;

-- Source order entity
CREATE TABLE IF NOT EXISTS order_info (
    order_serial_id                 text NOT NULL,
    row_guid                        text NOT NULL,
    receipt_no                      text,
    ref_order_serial_id             text,
    customer_serial_id              text,
    data_serial_id                  text,
    sale_type                       text,
    channel                         text,
    order_type                      integer NOT NULL DEFAULT 0,
    project_source                  integer NOT NULL DEFAULT 0,
    order_scene                     integer NOT NULL DEFAULT 0,
    order_create_time               timestamp without time zone NOT NULL,
    sale_amount                     numeric(30, 10) NOT NULL DEFAULT 0,
    sale_currency_amount            numeric(30, 10) NOT NULL DEFAULT 0,
    receive_company                 integer NOT NULL DEFAULT 0,
    is_test                         integer NOT NULL DEFAULT 0,
    environment                     text,
    member_id                       bigint NOT NULL DEFAULT 0,
    currency_type                   text,
    exchange_rate                   numeric(30, 10) NOT NULL DEFAULT 1,
    order_sub_type                  integer NOT NULL DEFAULT 0,
    "CreateTime"                    timestamp without time zone NOT NULL,
    "UpdateTime"                    timestamp without time zone NOT NULL,
    distribution_channel            text,
    distribution_order_serial_id    text,
    sale_ref_id                     text,
    main_product_type               text,
    depart_date                     text,
    order_source                    text,
    channel_sub_detail              text,
    back_date                       text,
    customer_settle_period          text,
    customer_settle_mode            text,
    customer_name                   text,
    customer_type                   text,
    customer_id                     text,
    is_create_calculation           integer NOT NULL DEFAULT 0,
    customer_settle_date            text,
    CONSTRAINT pk_order_info PRIMARY KEY (row_guid),
    CONSTRAINT uq_order_info_order_serial_id UNIQUE (order_serial_id),
    CONSTRAINT ck_order_info_is_test CHECK (is_test IN (0, 1)),
    CONSTRAINT ck_order_info_exchange_rate CHECK (exchange_rate > 0)
);

-- Source payment entity
CREATE TABLE IF NOT EXISTS pay_info (
    order_serial_id                 text NOT NULL,
    row_guid                        text NOT NULL,
    pay_type                        integer NOT NULL DEFAULT 0,
    pay_type_description            text,
    trade_no                        text,
    transfer_trade_no               text,
    receipt_no                      text,
    amount                          numeric(30, 10) NOT NULL DEFAULT 0,
    exchange_rate                   numeric(30, 10) NOT NULL DEFAULT 1,
    currency                        text,
    pay_channel_id                  integer NOT NULL DEFAULT 0,
    pay_product_id                  integer NOT NULL DEFAULT 0,
    "CreateTime"                    timestamp without time zone NOT NULL,
    "UpdateTime"                    timestamp without time zone NOT NULL,
    pay_channel_code                text,
    pay_product_code                text,
    pay_project_code                text,
    pay_company_id                  text,
    order_type                      integer NOT NULL DEFAULT 0,
    paid_currency_amount            numeric(30, 10) NOT NULL DEFAULT 0,
    paid_currency_type              text,
    CONSTRAINT pk_pay_info PRIMARY KEY (row_guid),
    CONSTRAINT fk_pay_info_order
        FOREIGN KEY (order_serial_id) REFERENCES order_info(order_serial_id) ON DELETE CASCADE,
    CONSTRAINT ck_pay_info_exchange_rate CHECK (exchange_rate > 0)
);

-- Source refund entity
CREATE TABLE IF NOT EXISTS refund_info (
    order_serial_id                    text NOT NULL,
    row_guid                           text NOT NULL,
    refund_no                          text,
    refund_amount                      numeric(30, 10) NOT NULL DEFAULT 0,
    receipt_no                         text,
    refund_status                      integer NOT NULL DEFAULT 0,
    trade_no                           text,
    refund_trade_no                    text,
    notify_info                        text,
    refund_type                        integer NOT NULL DEFAULT 0,
    "CreateTime"                       timestamp without time zone NOT NULL,
    "UpdateTime"                       timestamp without time zone NOT NULL,
    order_serial_id_of_trade_no        text,
    refund_category                    integer NOT NULL DEFAULT 0,
    refund_reason                      text,
    order_serial_id_of_refunded        text,
    refund_callback_reason             text,
    refund_request_time                text,
    refund_receive_account             text,
    refund_arrive_date                 text,
    refund_channel_code                text,
    refund_product_code                text,
    refund_finish_time                 text,
    refund_company_id                  text,
    order_type                         integer NOT NULL DEFAULT 0,
    project_source                     integer NOT NULL DEFAULT 0,
    extend_info                        text,
    business_apply_refund_amount       numeric(30, 10) NOT NULL DEFAULT 0,
    business_apply_refund_currency_type text,
    CONSTRAINT pk_refund_info PRIMARY KEY (row_guid),
    CONSTRAINT fk_refund_info_order
        FOREIGN KEY (order_serial_id) REFERENCES order_info(order_serial_id) ON DELETE CASCADE,
    CONSTRAINT ck_refund_info_status CHECK (refund_status IN (0, 1, 2, 3, 4, 11))
);

-- Source customer resource entity
CREATE TABLE IF NOT EXISTS customer_resource_info (
    row_guid                        text NOT NULL,
    order_serial_id                 text NOT NULL,
    resource_type                   integer NOT NULL DEFAULT 0,
    gold_toad_resource_type         text,
    business_guid                   text,
    resource_count                  integer NOT NULL DEFAULT 0,
    resource_company_id             integer NOT NULL DEFAULT 0,
    sale_amount                     numeric(30, 10) NOT NULL DEFAULT 0,
    flight_price                    numeric(30, 10) NOT NULL DEFAULT 0,
    build_fee                       numeric(30, 10) NOT NULL DEFAULT 0,
    fue_tax                         numeric(30, 10) NOT NULL DEFAULT 0,
    receipt_no                      text,
    "CreateTime"                    timestamp without time zone NOT NULL,
    "UpdateTime"                    timestamp without time zone NOT NULL,
    extend_info                     text,
    currency                        text,
    exchange_rate                   numeric(30, 10) NOT NULL DEFAULT 1,
    sale_currency_amount            numeric(30, 10) NOT NULL DEFAULT 0,
    resource_state                  integer NOT NULL DEFAULT 0,
    back_date                       text,
    revenue_date                    text,
    activity_code                   text,
    packge_resource_type            integer NOT NULL DEFAULT 0,
    CONSTRAINT pk_customer_resource_info PRIMARY KEY (row_guid),
    CONSTRAINT fk_customer_resource_info_order
        FOREIGN KEY (order_serial_id) REFERENCES order_info(order_serial_id) ON DELETE CASCADE,
    CONSTRAINT ck_customer_resource_info_exchange_rate CHECK (exchange_rate > 0)
);

-- Source supplier resource entity
CREATE TABLE IF NOT EXISTS supplier_resource_info (
    order_serial_id                 text NOT NULL,
    row_guid                        text NOT NULL,
    resource_type                   integer NOT NULL DEFAULT 0,
    gold_toad_resource_type         text,
    business_guid                   text,
    resource_count                  integer NOT NULL DEFAULT 0,
    contract_amount                 numeric(30, 10) NOT NULL DEFAULT 0,
    settle_period                   text,
    settle_date                     text,
    settle_mode                     text,
    complete_date                   timestamp without time zone NOT NULL,
    electronic_ticket_no            text,
    payment_type                    text,
    pnr                             text,
    ticket_out_state                integer NOT NULL DEFAULT 0,
    airways_code                    text,
    airways_name                    text,
    merchant_id                     text,
    out_project_serial_id           text,
    supplier_trade_no               text,
    receipt_no                      text,
    "CreateTime"                    timestamp without time zone NOT NULL,
    "UpdateTime"                    timestamp without time zone NOT NULL,
    extend_info                     text,
    currency                        text,
    exchange_rate                   numeric(30, 10) NOT NULL DEFAULT 1,
    contract_currency_amount        numeric(30, 10) NOT NULL DEFAULT 0,
    resource_state                  integer NOT NULL DEFAULT 0,
    CONSTRAINT pk_supplier_resource_info PRIMARY KEY (row_guid),
    CONSTRAINT fk_supplier_resource_info_order
        FOREIGN KEY (order_serial_id) REFERENCES order_info(order_serial_id) ON DELETE CASCADE,
    CONSTRAINT ck_supplier_resource_info_exchange_rate CHECK (exchange_rate > 0)
);

CREATE INDEX IF NOT EXISTS ix_order_info_order_create_time
    ON order_info (order_create_time);
CREATE INDEX IF NOT EXISTS ix_order_info_product_time
    ON order_info (main_product_type, order_create_time);
CREATE INDEX IF NOT EXISTS ix_order_info_data_serial_id
    ON order_info (data_serial_id);
CREATE INDEX IF NOT EXISTS ix_pay_info_order_trade
    ON pay_info (order_serial_id, trade_no);
CREATE INDEX IF NOT EXISTS ix_pay_info_create_time
    ON pay_info ("CreateTime");
CREATE INDEX IF NOT EXISTS ix_refund_info_order_status
    ON refund_info (order_serial_id, refund_status);
CREATE INDEX IF NOT EXISTS ix_refund_info_refund_no
    ON refund_info (refund_no);
CREATE INDEX IF NOT EXISTS ix_customer_resource_info_order_row
    ON customer_resource_info (order_serial_id, row_guid);
CREATE INDEX IF NOT EXISTS ix_customer_resource_info_business_guid
    ON customer_resource_info (business_guid);
CREATE INDEX IF NOT EXISTS ix_supplier_resource_info_order_row
    ON supplier_resource_info (order_serial_id, row_guid);
CREATE INDEX IF NOT EXISTS ix_supplier_resource_info_ticket_no
    ON supplier_resource_info (electronic_ticket_no);

COMMENT ON TABLE order_info IS 'Nino Data demo orders';
COMMENT ON TABLE pay_info IS 'Nino Data demo payments';
COMMENT ON TABLE refund_info IS 'Nino Data demo refunds';
COMMENT ON TABLE customer_resource_info IS 'Nino Data demo customer resources';
COMMENT ON TABLE supplier_resource_info IS 'Nino Data demo supplier resources';

COMMIT;
