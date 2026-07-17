# Nino Data 演示库指标定义

数据库由订单、支付、退款、客户资源和供应商资源五类实体组成。所有 mock 数据均为虚构数据，币种统一为 CNY。

## MVP 约定

- `refund_info.refund_status = 2` 表示退款成功，对应源码中的 `RefundCoreRefundStatusEnum.RefundSucceed`。
- 演示数据没有支付状态字段，因此 MVP 将存在 `pay_info` 记录视为已支付。
- 供应商退款通过负向 `supplier_resource_info.contract_amount` 表达，并使用 `resource_state = 99` 标识退款资源。
- `order_info` 没有通用订单状态字段，因此有效订单定义为：`is_test = 0`、时间在查询范围内且存在 `pay_info`。
- 以上是演示数据约定，不应声称是原公司完整生产口径。

## 指标

| 指标 | 公式 | 数据来源 |
|---|---|---|
| 订单量 | 有效 `order_info` 数量 | `order_info` + `pay_info` |
| 订单销售额 | `SUM(order_info.sale_amount)` | `order_info` |
| 客户资源销售额 | `SUM(customer_resource_info.sale_amount)` | `customer_resource_info` |
| 实付金额 | `SUM(pay_info.amount)` | `pay_info` |
| 供应商净成本 | `SUM(supplier_resource_info.contract_amount)` | 包含负向退款资源 |
| 成功退款金额 | `SUM(refund_info.refund_amount) WHERE refund_status = 2` | `refund_info` |
| 演示毛利 | `客户资源销售额 - 供应商净成本 - 成功退款金额` | 派生指标 |

## 边界约束

- MVP 不计算税费、返佣、支付手续费和汇兑损益。
- Mock 数据全部为 CNY，不进行跨币种汇总。
- 金额使用 PostgreSQL `numeric(30, 10)` 和 .NET `decimal`。
- 同一订单可有多条客户、供应商、支付和退款记录。每张明细表必须先按订单聚合，再做关联，避免笛卡尔积导致金额重复。
- 查询日期使用半开区间 `[start, end)`。

## Golden Cases

| 订单 | 场景 | 客户销售额 | 供应商净成本 | 成功退款 | 演示毛利 |
|---|---|---:|---:|---:|---:|
| `DEMO-202607-001` | 普通盈利 | 225.00 | 165.00 | 0.00 | 60.00 |
| `DEMO-202607-031` | 客户与供应商部分退款 | 1000.00 | 500.00 | 400.00 | 100.00 |
| `DEMO-202607-032` | 客户已退、供应商未退 | 500.00 | 450.00 | 500.00 | -450.00 |
| `DEMO-202607-033` | 多客户/供应商资源 | 1000.00 | 820.00 | 0.00 | 180.00 |
| `DEMO-202607-039` | 供应商拒绝退款 | 400.00 | 380.00 | 200.00 | -180.00 |
