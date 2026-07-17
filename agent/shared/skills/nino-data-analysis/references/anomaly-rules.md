# Anomaly Rules

- `NEGATIVE_MARGIN`: demo gross margin is below zero.
- `SUPPLIER_COST_EXCEEDS_SALES`: net supplier cost is greater than customer sales.
- `REFUND_NOT_RECOVERED_FROM_SUPPLIER`: a successful customer refund exists without sufficient supplier recovery.
- `PAYMENT_SALES_MISMATCH`: paid amount differs from customer sales.
- Reason codes are deterministic MVP signals, not production reconciliation decisions.

