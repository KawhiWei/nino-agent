# Metric Definitions（指标定义）

- Currency is CNY for the demo dataset.
  中文：演示数据集币种为 CNY。
- Date ranges are half-open: start date inclusive, end date exclusive.
  中文：日期范围左闭右开，包含开始日期，不包含结束日期。
- Effective aggregate orders are non-test orders with at least one payment record.
  中文：有效聚合订单是至少有一条支付记录的非测试订单。
- Customer sales equal the sum of customer resource `sale_amount`.
  中文：客户销售收入等于客户资源 `sale_amount` 之和。
- Net supplier cost equals the sum of supplier resource `contract_amount`; negative values represent supplier recovery.
  中文：净供应商成本等于供应商资源 `contract_amount` 之和；负值表示供应商侧回收。
- Successful refunds include only rows where `refund_status = 2`.
  中文：成功退款只包含 `refund_status = 2` 的记录。
- Demo gross margin equals customer sales minus net supplier cost minus successful refunds.
  中文：演示毛利 = 客户销售收入 - 净供应商成本 - 成功退款。
- The demo margin excludes tax, commission, payment fees, and foreign-exchange gains or losses.
  中文：演示毛利不包含税费、佣金、支付手续费和汇兑损益。
- Summary grand totals come from `nino_data_query_summary.data.totals`; do not calculate them by
  adding `groups` in the model.
  中文：汇总总计来自 `nino_data_query_summary.data.totals`，模型不得通过累加 `groups` 计算。
