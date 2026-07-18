# Anomaly Rules（异常规则）

- `NEGATIVE_MARGIN`: demo gross margin is below zero.
  中文：演示毛利小于 0。
- `SUPPLIER_COST_EXCEEDS_SALES`: net supplier cost is greater than customer sales.
  中文：净供应商成本大于客户销售收入。
- `REFUND_NOT_RECOVERED_FROM_SUPPLIER`: a successful customer refund exists without sufficient supplier recovery.
  中文：存在成功客户退款，但供应商侧回收不足。
- `PAYMENT_SALES_MISMATCH`: paid amount differs from customer sales.
  中文：已支付金额与客户销售收入不一致。
- Reason codes are deterministic MVP signals, not production reconciliation decisions.
  中文：Reason code 是确定性的 MVP 信号，不是生产级对账结论。
